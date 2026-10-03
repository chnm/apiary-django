from django.apps import apps
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, router, transaction
from django.db.models import CharField, Value
from django.db.models.functions import Cast, Concat

# The developer refresh restores each night's backup into <app>_next and runs this
# there before swapping it into place (rrchnm-systems/infra#197). Requiring the
# suffix on every database it touches means production can never be sanitized.
DEVELOPER_COPY_SUFFIX = "_next"

# Every model must be listed here. The refresh refuses a copy that holds a table
# nobody has classified, so a new model fails CI (test_sanitize) and a table made
# outside the ORM fails the refresh, which keeps yesterday's copy.
KEEP = {
    "apiary.Project",
    "auth.Group",
    "auth.Group_permissions",
    "auth.Permission",
    "auth.User_groups",
    "auth.User_user_permissions",
    "connthreads.Textile",
    "contenttypes.ContentType",
    "mappingviolence.Witness",  # historical testimony from published sources
    "relec.Denomination",
}
SANITIZED = {
    "account.EmailAddress",
    "account.EmailConfirmation",
    "admin.LogEntry",
    "auth.User",
    "relec.Schedule",
    "sessions.Session",
    "socialaccount.SocialAccount",
    "socialaccount.SocialApp",
    "socialaccount.SocialToken",
}
# bom lives in SQLite on the app's `bom` volume, not in the PostgreSQL dump.
NOT_IN_COPY = {"bom.MortalityBill"}

ALWAYS_PRESENT_TABLES = {"django_migrations"}


def classified_models():
    return {m._meta.label: m for m in apps.get_models(include_auto_created=True) if m._meta.label in KEEP | SANITIZED}


def copy_aliases():
    return sorted({router.db_for_write(m) for m in classified_models().values()})


class Command(BaseCommand):
    help = (
        "Pseudonymize people and delete credentials, sessions and audit text in a "
        "developer copy of the database. Refuses unless every database it touches "
        f"is named with the {DEVELOPER_COPY_SUFFIX!r} suffix."
    )

    def handle(self, *args, **options):
        aliases = copy_aliases()
        for alias in aliases:
            name = str(connections[alias].settings_dict["NAME"])
            if not name.endswith(DEVELOPER_COPY_SUFFIX):
                raise CommandError(
                    f"refusing to sanitize {alias} ({name!r}): only a developer copy "
                    f"(a database name ending in {DEVELOPER_COPY_SUFFIX!r}) may be sanitized"
                )
        self.check_every_table_is_classified(aliases)

        with transaction.atomic(using="default"), transaction.atomic(using="relec_db"):
            counts = self.sanitize()
        summary = ", ".join(f"{label} {n}" for label, n in counts.items())
        self.stdout.write(f"sanitized {connections['default'].settings_dict['NAME']}: {summary}")

    def check_every_table_is_classified(self, aliases):
        expected = {alias: set(ALWAYS_PRESENT_TABLES) for alias in aliases}
        for model in classified_models().values():
            expected[router.db_for_write(model)].add(model._meta.db_table)
        unknown = []
        for alias in aliases:
            with connections[alias].cursor() as cursor:
                tables = set(connections[alias].introspection.table_names(cursor))
            unknown += [f"{alias}:{t}" for t in sorted(tables - expected[alias])]
        if unknown:
            raise CommandError(
                "refusing to sanitize: unclassified tables "
                f"{', '.join(unknown)}; add their models to KEEP or SANITIZED"
            )

    def sanitize(self):
        model = classified_models()
        counts = {}

        # Credentials, sessions and free-text audit history go entirely.
        for label in ("sessions.Session", "socialaccount.SocialToken", "account.EmailConfirmation", "admin.LogEntry"):
            counts[label], _ = model[label].objects.all().delete()

        # Keep accounts, groups and permissions so access bugs reproduce, but with
        # placeholder identities and no usable password (`changepassword` locally).
        User = model["auth.User"]
        counts["auth.User"] = User.objects.update(
            username=Concat(Value("user"), Cast("pk", CharField())),
            first_name="",
            last_name="",
            email=Concat(Value("user"), Cast("pk", CharField()), Value("@example.invalid")),
            password=make_password(None),
        )
        EmailAddress = model["account.EmailAddress"]
        # A primary address matches its user's placeholder; others stay unique per row.
        counts["account.EmailAddress"] = EmailAddress.objects.filter(primary=True).update(
            email=Concat(Value("user"), Cast("user_id", CharField()), Value("@example.invalid"))
        ) + EmailAddress.objects.filter(primary=False).update(
            email=Concat(Value("address"), Cast("pk", CharField()), Value("@example.invalid"))
        )
        counts["socialaccount.SocialAccount"] = model["socialaccount.SocialAccount"].objects.update(
            uid=Cast("pk", CharField()), extra_data={}
        )
        counts["socialaccount.SocialApp"] = model["socialaccount.SocialApp"].objects.update(
            client_id="", secret="", key="", settings={}
        )

        # Contributor names become stable pseudonyms, so "who transcribed what"
        # still groups correctly without naming anyone.
        Schedule = model["relec.Schedule"]
        names = set(Schedule.objects.values_list("transcriber", flat=True))
        names |= set(Schedule.objects.values_list("reviewer", flat=True))
        names.discard("")
        width = max(3, len(str(len(names))))
        pseudonyms = {name: f"contributor-{i:0{width}d}" for i, name in enumerate(sorted(names), 1)}
        for name, pseudonym in pseudonyms.items():
            Schedule.objects.filter(transcriber=name).update(transcriber=pseudonym)
            Schedule.objects.filter(reviewer=name).update(reviewer=pseudonym)
        counts["relec.Schedule"] = len(pseudonyms)
        return counts

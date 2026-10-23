"""
The projects: each is a Django app with its own database (a PostgreSQL schema
or a SQLite file) holding its tables. demo's tables are models in code; the
others define theirs live (apiary.live). Access follows each project's
<project>-viewer, -editor and -admin groups.
"""
PROJECT_APPS = ("demo", "bom", "connthreads", "mappingviolence", "relec", "pinkertons")
PROJECT_DATABASES = {label: f"{label}_db" for label in PROJECT_APPS}

# Each role includes the ones before it.
ROLES = ("viewer", "editor", "admin")


class UnknownProject(LookupError):
    pass


def check_project(project):
    if project not in PROJECT_APPS:
        raise UnknownProject(f"Unknown project {project!r}; one of {', '.join(PROJECT_APPS)}.")
    return project


def project_alias(project):
    return PROJECT_DATABASES[check_project(project)]


def role(user, project):
    """The user's role in the project: superusers are admins everywhere."""
    if user is None or not user.is_active or not user.is_authenticated:
        return None
    if user.is_superuser:
        return "admin"
    names = {group.name for group in user.groups.all()}
    for name in reversed(ROLES):
        if f"{project}-{name}" in names:
            return name
    return None


def has_role(user, project, needed):
    current = role(user, project)
    return current is not None and ROLES.index(current) >= ROLES.index(needed)


def code_models(project):
    """The project's models defined in code, by model name (demo's)."""
    from django.apps import apps

    return {m._meta.model_name: m for m in apps.get_app_config(check_project(project)).get_models()}


def project_models(project):
    """Every table of the project as a Django model, by name: code models, then live tables."""
    from apiary.live.runtime import live_models

    return {**code_models(project), **live_models(project)}

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import camel_case_to_spaces

logger = logging.getLogger(__name__)

class ModelLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):

        # add model class name to extra
        if 'extra' not in kwargs:
            kwargs['extra'] = {}
        kwargs['extra']['className'] = self.extra['className']

        return msg, kwargs

class BaseMeta(models.base.ModelBase):

    def __new__(cls, name, bases, attrs, **kwargs):

        meta = attrs.get('Meta')

        if meta:
            logger.debug(f"{name} has meta")

            if (
                not hasattr(meta, 'db_table')
                and not getattr(meta, 'abstract', False)
            ):
                logger.debug(f"{name} is concrete and has no db_table meta")
                meta.db_table = name.lower()
            else:
                logger.debug(f"{name} is abstract or has db_table meta")
        else:
            logger.debug(f"{name} has no meta")
            class Meta:
                db_table = name.lower()
            attrs['Meta'] = Meta

        return super().__new__(cls, name, bases, attrs, **kwargs)

class BaseModel(models.Model, metaclass=BaseMeta):

    class Meta:
        abstract = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.logger = ModelLoggerAdapter(logger, {'className': cls.__name__})

class Project(BaseModel):

    class Meta:
        db_table_comment = "Project table"

    app_label = models.CharField(max_length=50)
    description = models.CharField(max_length=500)
    name = models.CharField(max_length=50)

    def to_string(self):
        self.logger.debug("Project.to_string()")
        return f"{self.app_label}: {self.name} - {self.description}"


class APIToken(BaseModel):
    """
    A bearer token for a user's AI agent, for /mcp. Only its SHA-256 digest
    is stored; the token itself is shown once, when it is created.
    """
    PREFIX = "apiary_"
    # Record use at most this often, so each request does not write a row.
    LAST_USED_RESOLUTION = timedelta(minutes=1)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=100, help_text="What the token is for, e.g. the agent or machine using it.")
    hint = models.CharField(max_length=16, editable=False, help_text="The token's first characters, to tell tokens apart.")
    digest = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Leave empty for no expiry.")
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        db_table_comment = "Bearer tokens for users' AI agents"
        verbose_name = "API token"

    def __str__(self):
        return f"{self.name} ({self.hint}…)"

    @staticmethod
    def hash(raw):
        return hashlib.sha256(raw.encode()).hexdigest()

    def generate(self):
        """Set a new secret on this token and return it; the caller must show it now."""
        raw = self.PREFIX + secrets.token_urlsafe(32)
        self.hint = raw[:len(self.PREFIX) + 4]
        self.digest = self.hash(raw)
        return raw

    @property
    def is_active(self):
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > timezone.now())

    @classmethod
    def authenticate(cls, raw):
        """The active token for this secret, if its user is active too; else None."""
        token = cls.objects.select_related("user").filter(digest=cls.hash(raw)).first()
        if token is None or not token.is_active or not token.user.is_active:
            return None
        now = timezone.now()
        if token.last_used_at is None or now - token.last_used_at > cls.LAST_USED_RESOLUTION:
            cls.objects.filter(pk=token.pk).update(last_used_at=now)
        return token

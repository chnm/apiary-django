import logging

from django.db import models
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

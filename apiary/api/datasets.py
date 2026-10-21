"""
Ninja derived datasets (aggregates, joins, time series) for charts:
/api/<project>/v1/datasets/<name>/, from each project's datasets.py router.
"""
from importlib import import_module

from django.apps import apps
from django.utils.module_loading import module_has_submodule
from ninja import NinjaAPI
from ninja.security import SessionAuth

from apiary.views import PROJECT_APPS


class ProjectAuth(SessionAuth):
    """A session user holding any permission in the project, as its groups grant."""

    def __init__(self, app_label):
        super().__init__()
        self.app_label = app_label

    def authenticate(self, request, key):
        user = super().authenticate(request, key)
        if user and user.is_active and user.has_module_perms(self.app_label):
            return user
        return None


api = NinjaAPI(
    title='Apiary datasets',
    version='1',
    urls_namespace='datasets',
    docs_url='/datasets/docs/',
    openapi_url='/datasets/openapi.json',
)
for label in PROJECT_APPS:
    config = apps.get_app_config(label)
    if module_has_submodule(config.module, 'datasets'):
        api.add_router(
            f"/{label}/v1/datasets/",
            import_module(f"{label}.datasets").router,
            auth=ProjectAuth(label),
            tags=[config.verbose_name],
            url_name_prefix=label,
        )

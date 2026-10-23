"""
CRUD for every project table: /api/<project>/v1/<table>/. Each project
versions its API on its own.

Tables defined in code (demo's) are registered on a router, so the OpenAPI
schema documents them. Live tables (apiary.live) change at runtime, so one
generic viewset serves them, building its serializer from the current
model. Writes are recorded as changes (apiary.changes), like MCP's.
"""
from django.http import Http404
from rest_framework import serializers, viewsets
from rest_framework.routers import DefaultRouter

from apiary import changes
from apiary.api.permissions import ProjectRolePermission
from apiary.live.runtime import live_models
from apiary.projects import PROJECT_APPS, UnknownProject, code_models, project_alias

VIA = "API"


def serializer_for(model):
    # Prefixed with the project so tables that share a name across projects
    # get distinct OpenAPI components.
    name = f"{model._meta.app_label.title()}{model.__name__.title().replace('_', '')}"
    meta = type("Meta", (), {"model": model, "fields": "__all__"})
    return type(f"{name}Serializer", (serializers.ModelSerializer,), {"Meta": meta})


class RecordedViewSet(viewsets.ModelViewSet):
    """Saves through the project's database and records each write as an applied change."""
    permission_classes = [ProjectRolePermission]

    def get_queryset(self):
        return self.model._default_manager.using(project_alias(self.project)).order_by("pk")

    def perform_create(self, serializer):
        obj = serializer.save()
        changes.log_records(self.request.user, self.project, self.table, "create", after=[changes.row(obj)], via=VIA)

    def perform_update(self, serializer):
        before = changes.row(serializer.instance)
        obj = serializer.save()
        changes.log_records(self.request.user, self.project, self.table, "update",
                            before=[before], after=[changes.row(obj)], via=VIA)

    def perform_destroy(self, instance):
        before = changes.row(instance)
        instance.delete()
        changes.log_records(self.request.user, self.project, self.table, "delete", before=[before], via=VIA)


def code_viewset(model):
    name = f"{model._meta.app_label.title()}{model.__name__}"
    return type(f"{name}ViewSet", (RecordedViewSet,), {
        "model": model,
        "project": model._meta.app_label,
        "table": model._meta.model_name,
        "queryset": model._default_manager.order_by("pk"),
        "serializer_class": serializer_for(model),
    })


router = DefaultRouter()
for label in PROJECT_APPS:
    for table, model in code_models(label).items():
        router.register(f"{label}/v1/{table}", code_viewset(model), basename=f"{label}-{table}")


class LiveTableViewSet(RecordedViewSet):
    schema = None  # changes at runtime; not in the OpenAPI document
    _serializers = {}

    def initial(self, request, *args, **kwargs):
        self.project, self.table = kwargs["project"], kwargs["table"]
        try:
            models = live_models(self.project)
        except UnknownProject:
            raise Http404
        if self.table not in models:
            raise Http404
        self.model = models[self.table]
        super().initial(request, *args, **kwargs)

    def get_serializer_class(self):
        # Each schema change builds new model classes, so this keys on the class.
        if self.model not in self._serializers:
            self._serializers[self.model] = serializer_for(self.model)
        return self._serializers[self.model]


live_list = LiveTableViewSet.as_view({"get": "list", "post": "create"})
live_detail = LiveTableViewSet.as_view({"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"})

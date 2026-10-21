"""
DRF CRUD generated for every project model: /api/v1/<project>/<model>/.
"""
from django.apps import apps
from rest_framework import serializers, viewsets
from rest_framework.routers import DefaultRouter

from apiary.views import PROJECT_APPS


def viewset_for(model):
    # Prefixed with the project so models that share a name across projects
    # get distinct OpenAPI components.
    name = f"{model._meta.app_label.title()}{model.__name__}"
    meta = type('Meta', (), {'model': model, 'fields': '__all__'})
    serializer = type(f"{name}Serializer", (serializers.ModelSerializer,), {'Meta': meta})
    return type(f"{name}ViewSet", (viewsets.ModelViewSet,), {
        'queryset': model.objects.order_by('pk'),
        'serializer_class': serializer,
    })


router = DefaultRouter()
for label in PROJECT_APPS:
    for model in apps.get_app_config(label).get_models():
        router.register(
            f"{label}/{model._meta.model_name}",
            viewset_for(model),
            basename=f"{label}-{model._meta.model_name}",
        )

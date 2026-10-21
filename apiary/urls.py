import debug_toolbar
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings

from apiary import views
from apiary.api import crud, datasets
from apiary.health import health

urlpatterns = [
    path("", views.project_tables_page, name="apiary_project_tables_page"),
    path("health/", health),
    path("admin/", admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='api_schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='api_schema'), name='api_docs'),
    # DRF first: both claim /api/ as their root, and DRF's lists every model.
    path('api/', include(crud.router.urls)),
    path('api/', datasets.api.urls),
    path('apiary/whoami/', views.whoami_page, name='apiary_whoami_page'),
    path('apiary/mgmt/', views.management_commands_dashboard, name='apiary_management_commands_dashboard'),
    path('apiary/run-command/', views.run_management_command, name='run_management_command'),
    path('apiary/export/', views.export_project, name='apiary_export_project'),
    path('apiary/status/', views.status_page, name='apiary_status'),
    path('apiary/status.json', views.status_json, name='apiary_status_json'),
    path('apiary/status/stream/', views.status_stream, name='apiary_status_stream'),
    re_path(r'^media/(?P<visibility>public|private)/(?P<path>.+)$', views.media, name='media'),
]

if settings.DEBUG:
    urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns

import debug_toolbar
from rest_framework.routers import DefaultRouter
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings

from apiary import views
from apiary.health import health

router = DefaultRouter()

urlpatterns = [
    path("health/", health),
    path("admin/", admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('api/', include(router.urls)),
    path('apiary/whoami/', views.whoami_page, name='apiary_whoami_page'),
    path('apiary/mgmt/', views.management_commands_dashboard, name='apiary_management_commands_dashboard'),
    path('apiary/run-command/', views.run_management_command, name='run_management_command'),
    path('apiary/status/', views.status_page, name='apiary_status'),
    path('apiary/status.json', views.status_json, name='apiary_status_json'),
    path('apiary/status/stream/', views.status_stream, name='apiary_status_stream'),
    re_path(r'^media/(?P<visibility>public|private)/(?P<path>.+)$', views.media, name='media'),
]

if settings.DEBUG:
    urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns

import debug_toolbar
from rest_framework.routers import DefaultRouter
from django.contrib import admin
from django.urls import path, include
from django.conf import settings

from apiary import views

router = DefaultRouter()

urlpatterns = [
    path("admin/", admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('api/', include(router.urls)),
    path('apiary/whoami/', views.whoami_page, name='apiary_whoami_page'),
    path('apiary/mgmt/', views.management_commands_dashboard, name='apiary_management_commands_dashboard'),
    path('apiary/run-command/', views.run_management_command, name='run_management_command'),
]

if settings.DEBUG:
    urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns

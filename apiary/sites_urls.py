"""The URL space of a site's own host, <name>.SITES_DOMAIN (apiary.sites)."""
from django.urls import path

from apiary import sites

urlpatterns = [
    path("", sites.serve),
    path("<path:path>", sites.serve),
]

"""The URL space of a site's own host, <name>.SITES_DOMAIN (apiary.pages)."""
from django.urls import path

from apiary import pages

urlpatterns = [
    path("", pages.serve),
    path("<path:path>", pages.serve),
]

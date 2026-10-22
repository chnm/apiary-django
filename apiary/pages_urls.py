"""The former shared host, PAGES_HOST: an index, and redirects to each site's own host."""
from django.urls import path

from apiary import pages

urlpatterns = [
    path("", pages.index),
    path("<slug:project>/<slug:site>", pages.redirect_old_url),
    path("<slug:project>/<slug:site>/", pages.redirect_old_url),
    path("<slug:project>/<slug:site>/<path:path>", pages.redirect_old_url),
]

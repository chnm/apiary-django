"""The whole URL space of the pages host (apiary.pages.PagesHostMiddleware)."""
from django.urls import path

from apiary import pages

urlpatterns = [
    path("", pages.index),
    path("<slug:project>/<slug:site>", pages.redirect_to_site),
    path("<slug:project>/<slug:site>/", pages.serve),
    path("<slug:project>/<slug:site>/<path:path>", pages.serve),
]

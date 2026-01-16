from .settings import *

# django-debug-toolbar

# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#prerequisites
INSTALLED_APPS += ["debug_toolbar"]

# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#middleware
MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]


# Custom callback to restrict debug toolbar to superusers only
def show_toolbar(request):
    """
    Only show debug toolbar to superusers.
    """
    return DEBUG and request.user.is_authenticated and request.user.is_superuser


# https://django-debug-toolbar.readthedocs.io/en/latest/configuration.html#debug-toolbar-config
DEBUG_TOOLBAR_CONFIG = {
    "DISABLE_PANELS": ["debug_toolbar.panels.redirects.RedirectsPanel"],
    "SHOW_TEMPLATE_CONTEXT": True,
    "SHOW_TOOLBAR_CALLBACK": show_toolbar,
}

INTERNAL_IPS = ["127.0.0.1"]

# show debug toolbar callback for docker doesn't work, so this hack works
if DEBUG:
    INTERNAL_IPS = type("c", (), {"__contains__": lambda *a: True})()

"""
Django Unfold admin interface settings.

Documentation: https://github.com/unfoldadmin/django-unfold
"""
import logging
from urllib.parse import parse_qs

from django.urls import reverse_lazy

from .settings import *

TABLES_PATH = "/admin/apiary/change/tables/"


def listed(path, project=None):
    """
    Active on path's list and object pages for this project only (None: unfiltered).
    Unfold matches a link by path alone, so every project's filtered link to
    the same list would light up, and open its section, at once.
    """
    def active(request):
        if not request.path.startswith(path) or request.path.startswith(TABLES_PATH):
            return False
        current = request.GET.get("project__exact")
        if current is None:  # an object page keeps the list's filter here
            current = (parse_qs(request.GET.get("_changelist_filters", "")).get("project__exact") or [None])[0]
        return current == project
    return active


# Each project's sidebar section: Data Models, Unfold's collapsible list (open
# on any of its pages) of the project's tables page and every table it has,
# then its change history, sites and data snapshots. Agents add
# tables at any time over MCP, so the sections are built per request. Links
# are plain strings: Unfold marks an item active only when its link is one.
PROJECTS = [
    ("Demo", "demo"),
    ("Death by Numbers", "bom"),
    ("Connecting Threads", "connthreads"),
    ("Mapping Violence", "mappingviolence"),
    ("Religious Ecologies", "relec"),
    ("Pinkertons", "pinkertons"),
]
# Tables defined in code keep their own titles and icons, and their admin pages.
CODE_TABLES = {
    ("demo", "mortalitybill"): ("Bills of Mortality", "skull"),
    ("demo", "activity"): ("Activities", "visibility"),
    ("demo", "location"): ("Locations", "location_on"),
}


def table_items(label):
    from django.apps import apps
    from django.db import DatabaseError
    from apiary.projects import project_models

    # Every admin page lists every project's tables: one project's database
    # being unreachable must not take the rest of the admin with it.
    try:
        found = project_models(label)
    except DatabaseError:
        logging.getLogger(__name__).exception("Sidebar: cannot list %s tables", label)
        return []
    items = []
    for name, model in found.items():
        title, icon = CODE_TABLES.get((label, name)) or (str(model._meta.verbose_name_plural).capitalize(), "table_view")
        in_code = model._meta.apps is apps
        items.append({"title": title, "icon": icon, "link": f"/admin/{label}/{name}/" if in_code else f"{TABLES_PATH}{label}/{name}/"})
    return items


def project_section(request, title, label):
    from apiary.projects import has_role

    member = has_role(request.user, label, "viewer")
    allowed = lambda yes: lambda request: yes
    superuser = allowed(request.user.is_superuser)
    overview = f"{TABLES_PATH}{label}/"
    data_models = {"title": "Data Models", "icon": "database", "link": overview, "permission": allowed(member)}
    if member and (tables := table_items(label)):
        # An item with sub-items only opens them, so the overview is the first;
        # Unfold matches links as substrings, which would light it up on every table.
        data_models["items"] = [{"title": "All tables", "link": overview, "active": lambda request: request.path == overview}, *tables]
    # Unfold opens a section only for an active top-level item: Data Models is
    # active on any of its pages, including tables in code outside the overview's path.
    pages = [overview, *(table["link"] for table in data_models.get("items", ()))]
    data_models["active"] = lambda request: any(request.path.startswith(page) for page in pages)
    return {"title": title, "separator": True, "collapsible": True, "items": [
        data_models,
        {"title": "Changes", "icon": "history", "link": f"/admin/apiary/change/?project__exact={label}",
         "active": listed("/admin/apiary/change/", label), "permission": superuser},
        {"title": "Sites", "icon": "web", "link": f"/admin/apiary/site/?project__exact={label}",
         "active": listed("/admin/apiary/site/", label), "permission": superuser},
        {"title": "Data Snapshots", "icon": "backup", "link": f"/admin/apiary/datasnapshot/?project__exact={label}",
         "active": listed("/admin/apiary/datasnapshot/", label), "permission": superuser},
    ]}


def navigation(request):
    """The sidebar: Apiary, each project, then System Administration."""
    first, *rest = NAVIGATION
    return [first, *(project_section(request, title, label) for title, label in PROJECTS), *rest]


UNFOLD = {
    # Site Information
    "SITE_TITLE": "Apiary",
    "SITE_HEADER": "Apiary Administration",
    "SITE_SYMBOL": "hive",
    
    # Site URL and Favicon
    "SITE_URL": env("UNFOLD_SITE_URL", default="/"),
    
    # Theme Mode
    "THEME": "light",  # Force light mode ("light" | "dark" | "auto")

    # "SITE_ICON": {
    #     "light": lambda request: static("icon-light.svg"),
    #     "dark": lambda request: static("icon-dark.svg"),
    # },
    # "SITE_LOGO": {
    #     "light": lambda request: static("logo-light.svg"),
    #     "dark": lambda request: static("logo-dark.svg"),
    # },
    # "SITE_FAVICONS": [
    #     {
    #         "rel": "icon",
    #         "sizes": "32x32",
    #         "type": "image/svg+xml",
    #         "href": lambda request: static("favicon.svg"),
    #     },
    # ],
    
    # Colors and Theme
    "COLORS": {
        "primary": {
            "50": "254 242 242",   # Lightest red tint
            "100": "254 226 226",  # Very light red
            "200": "254 202 202",  # Light red
            "300": "252 165 165",  # Soft red
            "400": "248 113 113",  # Medium light red
            "500": "195 42 38",    # Primary color #c32a26
            "600": "185 28 28",    # Darker red
            "700": "153 27 27",    # Deep red
            "800": "127 29 29",    # Very dark red
            "900": "105 29 29",    # Almost black red
            "950": "69 10 10",     # Darkest red
        },
    },
    
    "SITE_DROPDOWN": [
        {
            "icon": "diamond",
            "title": ("My site"),
            "link": "https://example.com",
            "attrs": {
                "target": "_blank",
            },
        },
        {
            "icon": "diamond",
            "title": ("My site"),
            "link": reverse_lazy("admin:index"),
        },
    ],
    
    # Sidebar Configuration
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Apiary",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Overview",
                        "icon": "dashboard",
                        "link": "/admin/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Profile",
                        "icon": "account_circle",
                        "link": "/apiary/whoami/",
                        "permission": lambda request: request.user.is_staff,
                    },
                    {
                        "title": "Management Commands",
                        "icon": "analytics",
                        "link": "/apiary/mgmt/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "System Status",
                        "icon": "monitor_heart",
                        "link": "/apiary/status/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Projects",
                        "icon": "hub",
                        "link": "/admin/apiary/project/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                ],
            },
            {
                "title": "System Administration",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": "/admin/auth/user/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Groups",
                        "icon": "group",
                        "link": "/admin/auth/group/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Social Accounts",
                        "icon": "account_circle",
                        "link": "/admin/socialaccount/socialaccount/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Social Apps",
                        "icon": "apps",
                        "link": "/admin/socialaccount/socialapp/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Social App Tokens",
                        "icon": "apps",
                        "link": "/admin/socialaccount/socialtoken/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Data Snapshots (all projects)",
                        "icon": "backup",
                        "link": "/admin/apiary/datasnapshot/",
                        "active": listed("/admin/apiary/datasnapshot/"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Sites (all projects)",
                        "icon": "web",
                        "link": "/admin/apiary/site/",
                        "active": listed("/admin/apiary/site/"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "API Tokens",
                        "icon": "key",
                        "link": "/admin/apiary/apitoken/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "MCP Connections",
                        "icon": "link",
                        "link": "/admin/apiary/oauthtoken/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "OAuth Clients",
                        "icon": "apps",
                        "link": "/admin/apiary/oauthclient/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Changes",
                        "icon": "history",
                        "link": "/admin/apiary/change/",
                        "active": listed("/admin/apiary/change/"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "User Sessions",
                        "icon": "vpn_key",
                        "link": "/admin/sessions/session/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                ],
            },
        ],
    },
    
    # Tabs Configuration
    "TABS": [
        {
            "models": [
                "auth.user",
                "auth.group",
            ],
            "items": [
                {
                    "title": "Users",
                    "link": "/admin/auth/user/",
                },
                {
                    "title": "Groups",
                    "link": "/admin/auth/group/",
                },
            ],
        },
        {
            "models": [
                "bom.mortalitybill",
            ],
            "items": [
                {
                    "title": "Mortality Bills",
                    "link": "/admin/bom/mortalitybill/",
                },
            ],
        },
        {
            "models": [
                "connthreads.textile",
            ],
            "items": [
                {
                    "title": "Textiles",
                    "link": "/admin/connthreads/textile/",
                },
            ],
        },
        {
            "models": [
                "mappingviolence.witness",
            ],
            "items": [
                {
                    "title": "Witnesses",
                    "link": "/admin/mappingviolence/witness/",
                },
            ],
        },
        {
            "models": [
                "relec.denomination",
                "relec.schedule",
            ],
            "items": [
                {
                    "title": "Denominations",
                    "link": "/admin/relec/denomination/",
                },
                {
                    "title": "Schedules",
                    "link": "/admin/relec/schedule/",
                },
            ],
        },
        {
            "models": [
                "pinkertons.activity",
                "pinkertons.location",
            ],
            "items": [
                {
                    "title": "Activities",
                    "link": "/admin/pinkertons/activity/",
                },
                {
                    "title": "Locations",
                    "link": "/admin/pinkertons/location/",
                },
            ],
        },
    ],
    
    # Environment Badge
    # "ENVIRONMENT": env("UNFOLD_ENVIRONMENT", default=None),
    # "ENVIRONMENT": "development" | "production" | None
    
    # Show/Hide Elements
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    
    # Extensions
    "EXTENSIONS": {
        "modeltranslation": {
            "flags": {
                "en": "🇬🇧",
                "fr": "🇫🇷",
                "nl": "🇳🇱",
            },
        },
    },
    
    # Additional Styles and Scripts
    # "STYLES": [
    #     lambda request: static("css/custom.css"),
    # ],
    # "SCRIPTS": [
    #     lambda request: static("js/custom.js"),
    # ],
}

# The static groups; navigation() puts the projects between them per request.
NAVIGATION = UNFOLD["SIDEBAR"]["navigation"]
UNFOLD["SIDEBAR"]["navigation"] = navigation

"""
Django Unfold admin interface settings.

Documentation: https://github.com/unfoldadmin/django-unfold
"""
from django.urls import reverse_lazy

from .settings import *

# Each project's sidebar section: its overview, its models under Data Models
# (Unfold's nested items, a collapsible list that opens on any of them), and
# its sites and data snapshots. Links are plain strings: Unfold marks an item
# active by its link before it calls a link function, so a function link never
# shows as the current page.
def project_section(title, label, models):
    """models: (title, icon, model_name) for each of the project's models."""
    def member(request):
        # Any permission in the project, as its viewer, editor and admin groups give.
        return request.user.has_module_perms(label)

    def superuser(request):
        return request.user.is_superuser

    return {
        "title": title,
        "separator": True,
        "collapsible": True,
        "items": [
            {"title": "Overview", "icon": "dashboard", "link": f"/admin/{label}/", "permission": member},
            {
                "title": "Data Models",
                "icon": "database",
                "link": f"/admin/{label}/",
                "permission": member,
                "items": [
                    {"title": model_title, "icon": icon, "link": f"/admin/{label}/{model}/", "permission": member}
                    for model_title, icon, model in models
                ],
            },
            {"title": "Sites", "icon": "web", "link": f"/admin/apiary/site/?project__exact={label}",
             "permission": superuser},
            {"title": "Data Snapshots", "icon": "backup",
             "link": f"/admin/apiary/datasnapshot/?project__exact={label}", "permission": superuser},
        ],
    }


PROJECT_SECTIONS = [
    project_section("Death by Numbers", "bom", [("Bills of Mortality", "skull", "mortalitybill")]),
    project_section("Connecting Threads", "connthreads", [("Textiles", "checkroom", "textile")]),
    project_section("Mapping Violence", "mappingviolence", [("Witnesses", "gavel", "witness")]),
    project_section("Religious Ecologies", "relec", [
        ("Denominations", "church", "denomination"),
        ("Schedules", "assignment", "schedule"),
    ]),
    project_section("Pinkertons", "pinkertons", [
        ("Activities", "visibility", "activity"),
        ("Locations", "location_on", "location"),
    ]),
]


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
            *PROJECT_SECTIONS,
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
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Sites (all projects)",
                        "icon": "web",
                        "link": "/admin/apiary/site/",
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

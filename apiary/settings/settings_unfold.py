"""
Django Unfold admin interface settings.

Documentation: https://github.com/unfoldadmin/django-unfold
"""
from django.urls import reverse_lazy

from .settings import *

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
                        "link": lambda request: "/apiary/whoami/",
                        "permission": lambda request: request.user.is_staff,
                    },
                    {
                        "title": "Management Commands",
                        "icon": "analytics",
                        "link": lambda request: "/apiary/mgmt/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Projects",
                        "icon": "hub",
                        "link": lambda request: "/admin/apiary/project/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                ],
            },
            {
                "title": "Death by Numbers",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Overview",
                        "icon": "dashboard",
                        "link": lambda request: "/admin/bom/",
                        "permission": lambda request: request.user.groups.filter(name='bom-viewer').exists() or request.user.is_superuser,
                    },
                    {
                        "title": "Bills of Mortality",
                        "icon": "skull",
                        "link": lambda request: "/admin/bom/mortalitybill",
                        "permission": lambda request: request.user.groups.filter(name='bom-viewer').exists() or request.user.is_superuser,
                    },
                ],
            },
            {
                "title": "Connecting Threads",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Overview",
                        "icon": "dashboard",
                        "link": lambda request: "/admin/connthreads/",
                        "permission": lambda request: request.user.groups.filter(name='connthreads-viewer').exists() or request.user.is_superuser,
                    },
                    {
                        "title": "Textiles",
                        "icon": "checkroom",
                        "link": lambda request: "/admin/connthreads/textile/",
                        "permission": lambda request: request.user.groups.filter(name='connthreads-viewer').exists() or request.user.is_superuser,
                    },
                ],
            },
            {
                "title": "Mapping Violence",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Overview",
                        "icon": "dashboard",
                        "link": lambda request: "/admin/mappingviolence/",
                        "permission": lambda request: request.user.groups.filter(name='mappingviolence-viewer').exists() or request.user.is_superuser,
                    },
                    {
                        "title": "Witnesses",
                        "icon": "gavel",
                        "link": lambda request: "/admin/mappingviolence/witness/",
                        "permission": lambda request: request.user.groups.filter(name='mappingviolence-viewer').exists() or request.user.is_superuser,
                    },
                ],
            },
            {
                "title": "Religious Ecologies",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Overview",
                        "icon": "dashboard",
                        "link": lambda request: "/admin/relec/",
                        "permission": lambda request: request.user.groups.filter(name='relec-viewer').exists() or request.user.is_superuser,
                    },
                    {
                        "title": "Denominations",
                        "icon": "church",
                        "link": lambda request: "/admin/relec/denomination/",
                        "permission": lambda request: request.user.groups.filter(name='relec-viewer').exists() or request.user.is_superuser,
                    },
                    {
                        "title": "Schedules",
                        "icon": "assignment",
                        "link": lambda request: "/admin/relec/schedule/",
                        "permission": lambda request: request.user.groups.filter(name='relec-viewer').exists() or request.user.is_superuser,
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
                        "link": lambda request: "/admin/auth/user/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Groups",
                        "icon": "group",
                        "link": lambda request: "/admin/auth/group/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Social Accounts",
                        "icon": "account_circle",
                        "link": lambda request: "/admin/socialaccount/socialaccount/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Social Apps",
                        "icon": "apps",
                        "link": lambda request: "/admin/socialaccount/socialapp/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Social App Tokens",
                        "icon": "apps",
                        "link": lambda request: "/admin/socialaccount/socialtoken/",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "User Sessions",
                        "icon": "vpn_key",
                        "link": lambda request: "/admin/sessions/session/",
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

"""
Django Unfold admin interface settings.

Documentation: https://github.com/unfoldadmin/django-unfold
"""
from .settings import *

UNFOLD = {
    # Site Information
    "SITE_TITLE": env("UNFOLD_SITE_TITLE", default="Apiary"),
    "SITE_HEADER": env("UNFOLD_SITE_HEADER", default="Apiary Administration"),
    "SITE_SYMBOL": env("UNFOLD_SITE_SYMBOL", default="speed"),
    
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
    
    # Sidebar Configuration
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Navigation",
                "separator": True,
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": "/admin/",
                    },
                ],
            },
            {
                "title": "Apiary",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Projects",
                        "icon": "hub",
                        "link": lambda request: "/admin/apiary/project/",
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
                        "icon": "monitoring",
                        "link": lambda request: "/admin/bom/",
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
                        "icon": "timeline",
                        "link": lambda request: "/admin/connthreads/",
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
                        "icon": "map",
                        "link": lambda request: "/admin/mappingviolence/",
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
                        "icon": "church",
                        "link": lambda request: "/admin/relec/",
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
                    },
                    {
                        "title": "Groups",
                        "icon": "group",
                        "link": lambda request: "/admin/auth/group/",
                    },
                    {
                        "title": "Social Accounts",
                        "icon": "account_circle",
                        "link": lambda request: "/admin/socialaccount/socialaccount/",
                    },
                    {
                        "title": "Social Apps",
                        "icon": "apps",
                        "link": lambda request: "/admin/socialaccount/socialapp/",
                    },
                    {
                        "title": "Social App Tokens",
                        "icon": "apps",
                        "link": lambda request: "/admin/socialaccount/socialtoken/",
                    },
                    {
                        "title": "User Sessions",
                        "icon": "vpn_key",
                        "link": lambda request: "/admin/sessions/session/",
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

# AGENTS.md - Apiary Django Development Guide

This document provides essential context for AI agents working in the Apiary Django codebase.

## Project Overview

**Apiary Django** is a Django 5.2+ workspace application that hosts multiple digital humanities projects in a single deployment. It uses a multi-database architecture where each project has its own PostgreSQL schema (or SQLite database), with custom database routers to manage data isolation.

**Core projects:**
- **apiary** - Main Django app with shared models and settings
- **bom** (Death by Numbers) - Mortality data project
- **connthreads** (Connecting Threads) - Indian weavers and African Caribbean consumers history
- **mappingviolence** (Mapping Violence) - Violence mapping project
- **relec** (Religious Ecologies) - American religious history with 1926 Census data

## Technology Stack

- **Python**: 3.14 (see `.python-version`; the image uses the StageX Python base)
- **Django**: 5.2.7+
- **Package Management**: `uv` (modern Python package manager)
- **Database**: PostgreSQL 18 (with schema-per-project) + SQLite for bom_db
- **Admin UI**: django-unfold (modern admin interface)
- **Authentication**: django-allauth (ORCID, GitHub, Slack providers)
- **API**: Django REST Framework + drf-spectacular (OpenAPI)
- **Storage**: S3-compatible object storage via django-storages (optional)
- **Containerization**: Docker + Docker Compose

## Essential Commands

### Development Setup

```bash
# Install dependencies
uv sync

# Run migrations for all databases
uv run manage.py migrate --database=default
uv run manage.py migrate bom --database=bom_db
uv run manage.py migrate connthreads --database=connthreads_db
uv run manage.py migrate mappingviolence --database=mappingviolence_db
uv run manage.py migrate relec --database=relec_db

# Load fixtures
uv run manage.py loaddata apiary/fixtures/projects.yaml

# Run development server
uv run manage.py runserver

# Create superuser if missing (idempotent; skips when username/password are unset,
# never changes an existing account). The k8s migrate Job runs it on every sync.
DJANGO_SUPERUSER_USERNAME=admin \
DJANGO_SUPERUSER_EMAIL=admin@example.com \
DJANGO_SUPERUSER_PASSWORD=secret \
uv run manage.py init_superuser
```

### Docker

```bash
# Start all services
docker compose up

# Start in background
docker compose up -d

# View logs
docker compose logs -f app

# Stop services
docker compose down

# Rebuild after changes
docker compose up --build
```

### Database Management

```bash
# Create migrations for a specific app
uv run manage.py makemigrations <app_name>

# Apply migrations to specific database
uv run manage.py migrate <app_name> --database=<db_name>

# Show migration status
uv run manage.py showmigrations

# Access Django shell
uv run manage.py shell

# Database shell
uv run manage.py dbshell
```

### Other Useful Commands

```bash
# Collect static files
uv run manage.py collectstatic

# Initialize project groups
uv run manage.py init_project_groups

# Test command (example)
uv run manage.py test_command --message "Hello World"

# Lock dependencies
uv lock

# Check for issues
uv run manage.py check
```

## Architecture & Code Organization

### Settings Structure

Settings are split into multiple modules in `apiary/settings/`:
- `settings.py` - Core Django settings, installed apps, middleware
- `settings_db.py` - Database configurations and routers
- `settings_auth.py` - Authentication backends and allauth config
- `settings_media.py` - Media/static files and object storage
- `settings_logging.py` - Logging configuration
- `settings_debug_toolbar.py` - Debug toolbar setup
- `settings_unfold.py` - Django Unfold admin interface customization

All settings modules are imported in `apiary/settings/__init__.py`.

**Environment Variables:**
- `DEBUG` - Enable debug mode (default: False)
- `DJANGO_SECRET_KEY` - Secret key for production
- `DJANGO_ALLOWED_HOSTS` - Comma-separated list of allowed hosts
- `DJANGO_LOG_LEVEL` - Log level for apiary.models (default: INFO)
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASS` - Database connection
- `ALLAUTH_*_CLIENT_ID` and `ALLAUTH_*_CLIENT_SECRET` - OAuth providers
- `OBJ_STORAGE*` - S3-compatible object storage configuration
- `UNFOLD_SITE_TITLE`, `UNFOLD_SITE_HEADER`, `UNFOLD_SITE_SYMBOL` - Admin interface branding
- `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD` - Superuser credentials for init_superuser (in k8s, from the `eso/apiary-django` OpenBao secret)

### Multi-Database Architecture

**Critical Concept:** Each project app has its own database routing via PostgreSQL schemas (or separate SQLite database for bom).

**Database Configuration (`settings_db.py`):**
```python
DATABASES = {
    "default": {...},           # PostgreSQL public schema (admin, auth, sessions)
    "bom_db": {...},           # SQLite for bom app
    "connthreads_db": {...},   # PostgreSQL connthreads schema
    "mappingviolence_db": {...}, # PostgreSQL mappingviolence schema
    "relec_db": {...}          # PostgreSQL relec schema
}
```

**Database Routers (`apiary/routers/db.py`):**
- `AdminRouter` - Routes admin, auth, contenttypes, sessions to default
- `BomRouter`, `ConnThreadsRouter`, etc. - Routes each app to its database
- `DefaultRouter` - Fallback for apiary app models

Router order in `DATABASE_ROUTERS` is critical - specific routers before fallback routers.

**Schema Initialization:**
PostgreSQL schemas are created via `postgres_initdb/schema.sql` on first database startup.

### Model Architecture

**BaseModel (`apiary/models.py`):**
All models should inherit from `BaseModel`:
```python
from apiary.models import BaseModel

class YourModel(BaseModel):
    # fields...
    pass
```

**BaseModel features:**
- Uses custom `BaseMeta` metaclass
- Automatically sets `db_table` to lowercase class name if not specified
- Provides `logger` class attribute via `ModelLoggerAdapter`
- Logs with className context for better debugging

**Example:**
```python
class Person(BaseModel):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    # db_table will be "person" automatically
```

### Admin Customization

Use django-unfold for admin interface:
```python
from unfold.admin import ModelAdmin

@admin.register(YourModel)
class YourModelAdmin(ModelAdmin):
    pass
```

For User and Group models, use unfold forms:
```python
from unfold.forms import UserChangeForm, UserCreationForm, AdminPasswordChangeForm
```

### Fixtures

Project metadata stored in `apiary/fixtures/projects.yaml`:
```yaml
- model: apiary.project
  pk: 1
  fields:
    name: Death by Numbers
    app_label: bom
    description: Lorem ipsum
```

User fixtures stored in `apiary/fixtures/users.yaml` for development.

Load with: `uv run manage.py loaddata apiary/fixtures/projects.yaml`

### Templates

Custom templates located in `apiary/templates/`:
- `whoami.html` - User profile page (minimal HTML)
- `management-commands-dashboard.html` - Management commands interface (Pico.css with Apiary brand colors)

## Development Patterns

### Creating a New Project App

1. **Create the Django app:**
   ```bash
   uv run manage.py startapp newproject
   ```

2. **Add database configuration in `settings_db.py`:**
   ```python
   "newproject_db": {
       "ENGINE": "django.db.backends.postgresql",
       "HOST": env("DB_HOST", default="localhost"),
       "PORT": env("DB_PORT", default="5432"),
       "NAME": env("DB_NAME", default="apiary-django"),
       "USER": env("DB_USER", default="apiary-django"),
       "PASSWORD": env("DB_PASS", default="password"),
       "OPTIONS": {
           "options": "-c search_path=newproject"
       },
   }
   ```

3. **Create database router in `apiary/routers/db.py`:**
   ```python
   class NewProjectRouter(AbstractRouter):
       def __init__(self):
           super().__init__(
               app_label='newproject',
               db_name='newproject_db'
           )
   ```

4. **Add router to `DATABASE_ROUTERS` list in `settings_db.py`**

5. **Add schema to `postgres_initdb/schema.sql`:**
   ```sql
   CREATE SCHEMA IF NOT EXISTS newproject;
   ```

6. **Add app to `INSTALLED_APPS` in `settings.py`**

7. **Add to docker-compose migrations:**
   ```yaml
   uv run manage.py migrate newproject --database=newproject_db --verbosity=1 &&
   ```

8. **Add fixture entry in `apiary/fixtures/projects.yaml`**

### Creating Models

Always inherit from `BaseModel`:
```python
from django.db import models
from apiary.models import BaseModel

class YourModel(BaseModel):
    name = models.CharField(max_length=100)
    
    # Optional: customize table name
    class Meta:
        db_table = "custom_table_name"
        db_table_comment = "Description for documentation"
```

Use the model's logger:
```python
class YourModel(BaseModel):
    def some_method(self):
        self.logger.debug("Processing data")
        # logger automatically includes className
```

Classify every new model in `apiary/management/commands/sanitize_for_dev.py` (`KEEP` or `SANITIZED`, with a rule for any personal data). The developer-database refresh refuses unclassified tables, and `apiary/test_sanitize.py` fails until the model is listed.

### Sub-applications Pattern

Some apps have nested sub-applications (e.g., `relec/transcriptions/`, `relec/locations/`):
- Each sub-app has its own `models.py`, `admin.py`, `apps.py`
- Migrations live in the parent app
- Sub-apps are referenced as dotted paths in `AppConfig.name`

Example from `relec/locations/apps.py`:
```python
class LocationsConfig(AppConfig):
    name = 'relec.locations'
```

## Important Gotchas

### Database Routing

1. **Always specify database when migrating:**
   ```bash
   uv run manage.py migrate <app> --database=<db_name>
   ```

2. **Models automatically route to correct database** via routers, but be aware of cross-database foreign keys (avoid them - not supported well)

3. **Admin/auth tables always go to default database** via `AdminRouter`

4. **Router order matters** - specific routers before `DefaultRouter`

### Environment Variables

- Use `django-environ` for all environment variables
- Always provide defaults for development: `env("VAR", default="value")`
- Never commit secrets - use environment variables

### Object Storage

- Optional S3-compatible storage enabled via `OBJ_STORAGE=True`
- Falls back to local filesystem if not enabled
- Uses boto3 + django-storages
- See `settings_media.py` for configuration

Two media storages, both served by the app at `/media/<public|private>/...`
(`apiary.views.media`), because Garage's S3 endpoint is internal-only:

- `storages["public"]`: anyone can read.
- `storages["default"]` (private): readable by superusers and users with any
  permission in the app named by the file's first path segment, so upload
  private files under `<app_label>/...` (e.g. `upload_to="bom/"`).

```python
from apiary.storage import public_storage

scan = models.FileField(upload_to="bom/scans/")                     # private
image = models.ImageField(upload_to="bom/", storage=public_storage)  # public
```

Files are served with `Content-Security-Policy: sandbox`. Check both storages
with `uv run manage.py check_storage`; it is also on the management dashboard.

### Debug Toolbar in Docker

- Special INTERNAL_IPS hack in `settings_debug_toolbar.py` to work in Docker
- Uses `__contains__` trick to allow all IPs when DEBUG=True
- **Restricted to superusers only** via custom `SHOW_TOOLBAR_CALLBACK`
- Only authenticated superusers will see the debug toolbar

### Custom Decorators

**`@superuser_required` (`apiary/decorators.py`):**
- Similar to `@staff_member_required` but checks for superuser status
- Usage: `@superuser_required` or `@superuser_required(login_url='/custom/')`
- Checks `user.is_active` and `user.is_superuser`
- Redirects to admin login by default

Example:
```python
from apiary.decorators import superuser_required

@superuser_required
def my_admin_view(request):
    # Only superusers can access this view
    pass
```

### Logging

- Custom `ModelLoggerAdapter` adds `className` to log context
- Configure log levels via `DJANGO_LOG_LEVEL` environment variable
- Two formatters: `simple` and `verbose` (includes className and funcName)
- apiary.models logger uses verbose format

### Unfold Admin

- Replaces default Django admin
- Must be listed before `django.contrib.admin` in `INSTALLED_APPS`
- Configuration in `settings_unfold.py` with custom:
  - Site branding (title, header, symbol)
  - Color theme (primary color: #c32a26)
  - Theme mode forced to light
  - Sidebar navigation with project-specific sections and custom Apiary views
  - Tabs configuration for related models
  - Support for django-allauth social accounts
  - SITE_DROPDOWN placeholder sections (to be customized)
- Use unfold's forms and ModelAdmin for consistency
- Sidebar includes links to custom Apiary views:
  - Profile (`/apiary/whoami/`) - staff only
  - Management Commands (`/apiary/mgmt/`) - superuser only

### Python Version

- Project uses Python 3.14 (specified in `.python-version`)
- Use `uv` for package management, not pip directly
- Dependencies locked in `uv.lock`

## Testing

No comprehensive test suite exists yet. Test files exist as Django stubs:
```python
from django.test import TestCase

# Create your tests here.
```

When adding tests:
- Use Django's TestCase
- Run tests: `uv run pytest` (in-memory SQLite for every alias via `apiary.settings.test`)
- Consider database routing in test setup

## CI/CD

GitHub Actions workflow (`.github/workflows/cicd.yml`):
- Builds and publishes Docker image to ghcr.io
- Deploys to dev and prod environments
- Uses reusable workflows from `chnm/.github`
- Triggers on push to main branch

## URL Structure

Current URL configuration (`apiary/urls.py`):
- `/admin/` - Django admin (unfold)
- `/accounts/` - django-allauth authentication
- `/api/` - Django REST Framework (currently empty router)
- `/apiary/whoami/` - User profile page (staff only)
- `/apiary/mgmt/` - Management commands dashboard (superuser only)
- `/apiary/run-command/` - AJAX endpoint for running management commands (superuser only)
- `/__debug__/` - Debug toolbar (DEBUG mode + superuser only)

### Apiary Custom Views

**User Profile (`/apiary/whoami/`):**
- Displays logged-in user information
- Shows username, email, staff status, superuser status, and groups
- Restricted to staff members (`@staff_member_required`)

**Management Commands Dashboard (`/apiary/mgmt/`):**
- Web interface for running whitelisted management commands
- Uses Pico.css with Apiary brand colors (#c32a26)
- Commands defined in `ALLOWED_COMMANDS` whitelist in `apiary/views.py`
- Supports commands with/without arguments
- Real-time AJAX execution with terminal-style output
- CSRF protected
- Restricted to superusers (`@superuser_required`)

**Available Management Commands:**
- `test_command` - Demo command with customizable message argument
- `init_project_groups` - Create project-specific user groups

To add new commands, update `ALLOWED_COMMANDS` in `apiary/views.py`:
```python
ALLOWED_COMMANDS = {
    'command_name': {
        'name': 'Display Name',
        'description': 'Command description',
        'args': [
            {'name': 'arg_name', 'type': 'text', 'default': 'value', 'required': False}
        ],
        'timeout': 30
    },
}
```

## Code Style

### Naming Conventions

- **Models**: PascalCase, inherit from `BaseModel`
- **Database tables**: Lowercase, auto-generated from model name unless specified
- **Apps**: Lowercase, single word or compound (e.g., `mappingviolence`)
- **Settings modules**: Prefix with `settings_` (e.g., `settings_db.py`)

### Imports

Standard Django import order:
```python
import os
from pathlib import Path

import environ
from django.db import models

from apiary.models import BaseModel
```

### Settings

- Split settings into logical modules
- Import everything in `__init__.py`
- Use `from .settings import *` pattern in other settings modules
- Always use `env()` for environment variables with sensible defaults

## Security Notes

1. **Never commit secrets** - use environment variables
2. **DEBUG=False in production** - set via environment
3. **Update ALLOWED_HOSTS** for production deployment
4. **Rotate SECRET_KEY** - default is for development only
5. **Configure CSRF_TRUSTED_ORIGINS** for production domains
6. **OAuth secrets** use PLACEHOLDER by default - configure for production

## Useful References

- Django documentation: https://docs.djangoproject.com/en/5.2/
- django-unfold: https://github.com/unfoldadmin/django-unfold
- django-allauth: https://docs.allauth.org/
- drf-spectacular: https://drf-spectacular.readthedocs.io/
- uv documentation: https://docs.astral.sh/uv/

## Quick Start for New Agents

1. **Understand the multi-database architecture** - each app has its own database/schema
2. **Always use BaseModel** for new models
3. **Check database routers** when working with data
4. **Use `uv run manage.py`** for all Django commands
5. **Split settings** follow the pattern in `apiary/settings/`
6. **Test with Docker** using `docker compose up`
7. **Check fixtures** in `apiary/fixtures/` for example data
8. **Follow the logging pattern** using `self.logger` in models

## Common Tasks Quick Reference

| Task | Command |
|------|---------|
| Install dependencies | `uv sync` |
| Run dev server | `uv run manage.py runserver` |
| Create migration | `uv run manage.py makemigrations <app>` |
| Apply migration | `uv run manage.py migrate <app> --database=<db>` |
| Create superuser (idempotent) | `uv run manage.py init_superuser` |
| Django shell | `uv run manage.py shell` |
| Load fixtures | `uv run manage.py loaddata <path>` |
| Docker start | `docker compose up` |
| Docker rebuild | `docker compose up --build` |
| View logs | `docker compose logs -f app` |
| Lock dependencies | `uv lock` |

## Project Status

This is an active development project for RRCHNM (Roy Rosenzweig Center for History and New Media). The codebase includes:
- ✅ Multi-database architecture with routers
- ✅ BaseModel with logging
- ✅ django-unfold admin interface with custom theme
- ✅ django-allauth authentication (OAuth)
- ✅ Custom decorators (`@superuser_required`)
- ✅ User profile page with group membership display
- ✅ Management commands web dashboard (superuser only)
- ✅ Idempotent superuser creation management command
- ✅ Debug toolbar restricted to superusers
- ✅ Docker deployment setup
- ✅ CI/CD pipeline
- ⚠️ Most app models are empty stubs
- ⚠️ No comprehensive test coverage yet
- ⚠️ API endpoints not yet implemented

When extending functionality, follow existing patterns and maintain the multi-tenant architecture.

# AGENTS.md - Apiary Django Development Guide

This document provides essential context for AI agents working in the Apiary Django codebase.

## Project Overview

**Apiary Django** is a Django 5.2+ workspace application that hosts multiple digital humanities projects in a single deployment. It uses a multi-database architecture where each project has its own PostgreSQL schema (or SQLite database), with custom database routers to manage data isolation.

**Core apps** (`apiary/projects.py` lists the projects):
- **apiary** - Main Django app with shared models and settings
- **apiary.live** (label `live`) - live table definitions, kept in every project database
- **demo** (Demo) - mock models and fixtures for the public root page; the only project with models in code
- **bom** (Death by Numbers), **connthreads** (Connecting Threads), **mappingviolence** (Mapping Violence), **relec** (Religious Ecologies), **pinkertons** (Pinkertons) - empty containers: their tables are created live, through MCP

## Technology Stack

- **Python**: 3.14 (see `.python-version`; the image uses the StageX Python base)
- **Django**: 5.2.7+
- **Package Management**: `uv` (modern Python package manager)
- **Database**: PostgreSQL 18 (with schema-per-project) + SQLite (WAL) for bom_db and pinkertons_db
- **Admin UI**: django-unfold (modern admin interface)
- **Authentication**: django-allauth (ORCID, GitHub, Slack providers)
- **API**: Django REST Framework + drf-spectacular for table CRUD
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
uv run manage.py migrate pinkertons --database=pinkertons_db

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

**Critical Concept:** Each project app has its own database routing via PostgreSQL schemas (or separate SQLite databases for bom and pinkertons).

**Database Configuration (`settings_db.py`):**
```python
DATABASES = {
    "default": {...},           # PostgreSQL public schema (admin, auth, sessions)
    "bom_db": {...},           # SQLite for bom app
    "connthreads_db": {...},   # PostgreSQL connthreads schema
    "mappingviolence_db": {...}, # PostgreSQL mappingviolence schema
    "relec_db": {...},         # PostgreSQL relec schema
    "pinkertons_db": {...}     # SQLite for pinkertons app
}
```

**Database Routers (`apiary/routers/db.py`):**
- `AdminRouter` - Routes admin, auth, contenttypes, sessions to default
- `BomRouter`, `ConnThreadsRouter`, etc. - Routes each app to its database
- `DefaultRouter` - Fallback for apiary app models

Router order in `DATABASE_ROUTERS` is critical - specific routers before fallback routers.

**Schema Initialization:**
PostgreSQL schemas are created via `postgres_initdb/schema.sql` on first database startup.

### Projects, roles and live tables

`apiary/projects.py` names the projects, their database aliases
(`<project>_db`) and roles. A user's role in a project comes from the
`<project>-viewer`, `-editor` and `-admin` groups (superusers are admins
everywhere): viewers read, editors create and update rows, admins delete
rows and change tables. `init_project_groups` creates the groups for every
project; only demo's code models get model permissions too, for the admin.

Live tables (`apiary/live/`) are a project's tables defined at runtime, like
PocketBase collections. Their definitions (`LiveTable`, `LiveColumn`,
`SchemaState`) live in the project's own database, so a schema change and its
definitions commit in one transaction on PostgreSQL and SQLite alike.
`runtime.py` builds Django models from them in a registry of their own
(`managed = False`, table `live_<name>`), cached per process until
`SchemaState` changes: migrations, the admin and `dumpdata` never see them.
`projects.project_models(project)` gives a project's code models and live
tables together; use it rather than the app registry.

- `schema.py` plans operations (create, drop, rename table; add, drop,
  rename, alter column), checking them against the data (a required column on
  a table with rows needs a default, unique needs unique values, ...), shows
  the SQL, and applies them with Django's schema editor. Column types never
  change in place. Names: lowercase, not reserved, no `meta_` prefix.
- Column types: text, longtext, integer, float, boolean, date, datetime,
  time, json, url, email, relation (to a live table of the same project;
  required links PROTECT, optional ones SET_NULL).
- On SQLite the schema editor cannot run inside another transaction, so
  tests that change tables are `TransactionTestCase`s and drop the tables
  after (`apiary.live.testing.drop_live_tables`).

### Changes

`apiary/changes.py` changes tables and records and records each as a `Change`
(who, through what, when, why, the operations, a preview, the rows before
and after). Never pruned.

- Records: creates apply at once. Updates and deletes of up to 25 rows apply
  at once; more become a proposal with a sample, applied with `apply`, which
  checks the rows have not changed since and snapshots first. Applied records
  changes can be reverted while their rows are as they left them.
- Tables: always a proposal (reason required), applied with `apply`, which
  refuses if the schema moved since and snapshots the project first, so
  restoring that snapshot undoes it.
- `changes.diff` shows a records change side by side, each row before and
  after, and short however big: per column, how many rows went from which
  value to which, then the first 50 rows. Admin > Changes renders it for
  people (`apiary/diffview.py`); MCP change results carry a compact copy
  (10 rows; `list_changes` only the summary).
- Admin > Changes (superusers): review, apply, reject, revert. Each project's
  sidebar section links its Tables page (`/admin/apiary/change/tables/<project>/`,
  for anyone with a role there) and its changes.

### API

`/api/<project>/v1/<table>/` - DRF CRUD for every table (`apiary/api/crud.py`),
by project role (`apiary/api/permissions.py`). Demo's code models are on a
router and in the OpenAPI schema (`/api/schema/`, docs at `/api/docs/`); live
tables are served by one generic viewset, built from the current definitions,
and left out of the schema. Writes are recorded as changes (via "API").
Paginated with `limit`/`offset`.

### MCP for AI agents

`/mcp` is an MCP server (`apiary/mcp_server.py`) on the official MCP SDK:
read tools, change tools by scope and project role, site tools for staff. It is an ASGI app under uvicorn, separate from Gunicorn so Django stays
WSGI: a second container in the k8s app pod (it reads the node-local SQLite
files), the `mcp` service on port 8001 in Compose. Stateless JSON transport.

- Auth: `Authorization: Bearer <token>`, either an OAuth access token or an
  `APIToken` (`apiary/models.py`) that superusers create and revoke in the
  admin (API Tokens; shown once). Only SHA-256 digests of tokens are stored.
- Scopes (`mcp_oauth.SCOPES`): `mcp:read` reads every project; `mcp:write`
  changes records (as the user's role allows) and publishes sites (staff);
  `mcp:schema` changes tables (project admins). OAuth users grant them on the
  consent page; API tokens get them in the admin. Tokens from before scopes
  keep read and write.
- OAuth (`apiary/mcp_oauth.py`): the MCP container is the authorization
  server, on the SDK's handlers, at `MCP_URL`: metadata, `/register` (dynamic
  client registration), `/authorize`, `/token`, `/revoke`, and the protected
  resource metadata for `/mcp`. `/authorize` sends the browser to Django's
  consent page (`/apiary/mcp/authorize/`, behind Django's login, at
  `APP_URL`), which issues the code. Only active staff can connect, checked
  again on every request and refresh. Codes last 5 minutes, access tokens an
  hour, refresh tokens 30 days and rotate on use; every token is issued for
  `/mcp` alone. Admin > MCP connections lists and ends connections; clients
  that registered but never connected are dropped after a day. The gateway
  routes those paths to the MCP container (`k8s/mcp-route.yaml`).
- Read tools: `list_projects` (tables, columns, your role), `query_records`,
  `get_record`, `aggregate`, `list_changes`, `list_sites`. Field paths are
  checked against the table's own project (`apiary/queries.py`), so filters
  cannot reach auth tables or another project.
- Change tools (see Changes): `create_records`, `update_records`,
  `delete_records`, `propose_table_change`, `apply_change`, `reject_change`,
  `revert_change`. Tools carry MCP annotations (read-only, destructive).
- Site tools (staff, write scope; see Sites): `create_site`, `put_files`,
  `delete_files`, `publish_site`, `rollback_site`, `unpublish_site`. Errors
  are `ToolError`s, so the agent sees why.
- ORM calls run on asgiref's single sync thread and close their connections:
  outside Django's request cycle nothing else would.
- The SDK's DNS-rebinding check uses `ALLOWED_HOSTS`.

### Data snapshots

`apiary/projectdata.py` snapshots and restores one project's rows, on SQLite
or PostgreSQL alike: a gzipped Django fixture of every table, code and live,
plus `manifest.json` (format 2) in private storage under
`snapshots/<project>/<key>/`, indexed by `DataSnapshot`.

- Read from one consistent moment (`REPEATABLE READ READ ONLY` on PostgreSQL).
- Records the applied migrations of the project's code models and the
  definitions of its live tables (`schema`). A restore refuses a snapshot from
  other migrations, but rebuilds the live tables as the snapshot defines
  them, so it undoes table changes too. It verifies the checksum and the
  records before touching data, snapshots the current rows first (so every
  restore can be undone), then flushes, rebuilds and loads in one
  transaction (on SQLite, with foreign key checks deferred to the commit).
- Admin > Data Snapshots (superusers; each project's sidebar section links its
  own): take one, compare a snapshot's rows with the current ones and restore
  it (typing the project name), download its data, delete it with its files.
  The schema column shows which are restorable.
- Explore (on a snapshot's page) browses it as it was taken, one table per
  model with search, paging and links between related rows, read from the
  snapshot alone so older schemas open too. Its SQL console runs DuckDB-WASM
  (jsDelivr, pinned) in the browser over the same tables, served as NDJSON by a
  superuser-only admin URL; nothing runs on the server. DuckDB's Python wheels
  are glibc-only, so it cannot run in the StageX (musl) image.
- Export / Import move a snapshot between environments as a zip bundle
  (`manifest.json` + `data.json.gz`, format 1 or 2). Import checks the
  checksum, the project, the live table definitions, and that every record
  belongs to that project; restore checks them again, so a bundle can never
  write users or another project. Imported snapshots are never pruned.
- `snapshot_project`, `restore_project_snapshot`, `reset_project_data` (which
  snapshots first) and `prune_snapshots` also run from `/apiary/mgmt/`.
  Pruning removes only automatic snapshots (before resets, restores and
  changes) past retention (newest 10 per project and the last 30 days kept),
  never manual ones or those a site version was built from.

### Backups

A full teardown keeps only the remote restic repositories, so they hold
everything. The nightly `postgres-backup` CronJob (k0s, 02:45 ET; patched in
`k8s/kustomization.yaml`) fills `/dump`, which restic backs up whole:

- PostgreSQL: `pg_dump` of every project schema, users, tokens, and the site
  and snapshot index.
- SQLite: online-backup copies of `bom.sqlite3` and `pinkertons.sqlite3`,
  integrity-checked and switched from WAL to a rollback journal so a
  read-only mount can open them.
- Object storage: the whole bucket (`python -m apiary.bucket dump`), with a
  manifest of every key and size. That covers site files, snapshot files and
  uploaded media.

`k8s/restore.yaml` (parked, applied by hand) rehearses a restore: it checks the
dump's schemas, the SQLite copies and the bucket copy against its manifest
(`apiary.bucket check`), and lists the steps for a real recovery, including
`apiary.bucket restore`. The backup and restore pods share the app's
object-storage egress (`k8s/netpol.yaml`).

### Sites

A *site* is a static website (`Site`, `SiteVersion`); a *page* is one HTML
file in it. Sites (`apiary/sites.py`) are each served on their own host,
`<name>.SITES_DOMAIN` (`<name>.sites.rrchnm.org`; `<name>.sites.localhost:8000`
in Compose), never the app's: `SiteHostMiddleware` gives those hosts their own
URLconf (`apiary/sites_urls.py`), so neither the app nor another site shares
their origin. Site names are therefore unique across projects, DNS labels, and
not reserved (`check_new_site`, used by MCP and the admin alike). Pages are
untrusted HTML, so they get the ADR 0050 headers for agent-written pages plus
jsDelivr/cdnjs for scripts: the CSP sandbox gives them an opaque origin with no
cookies.

- Files live in private storage under `sites/<project>/<site key>/v<n>/`.
  Tools write into the draft version; published versions never change, and
  rolling back points the site at an earlier one.
- Data a page shows is written into the site as files (JSON, CSV). Versions
  published when sites still froze datasets keep the snapshot they read.
- Limits: 2 MB a file, 200 files and 20 MB a site, a fixed list of file types.
- Admin > Sites (superusers; each project's sidebar section links its own):
  create a site, upload a zip into its draft (put_files' checks; a single
  top folder is stripped), publish, roll back, unpublish (both confirm on
  POST), and delete with its files and the snapshots taken to publish it
  (kind `site`, unless another site's version uses one).

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

Load with: `uv run manage.py loaddata apiary/fixtures/projects.yaml` (Compose,
on a fresh database only: it writes by primary key).

Deployments never load it. `migrate_projects` (the k8s migrate Job) creates
the PostgreSQL schemas, migrates each project's app and `live` on its
database, runs `sync_projects`, which gives every app in `PROJECT_APPS` a
Project row named after the app and never changes an existing one, then
`init_project_groups`, and loads `demo/fixtures/demo.json` when demo is
empty. `init_project_groups` resets each `<project>-viewer/editor/admin`
group to its role's permissions on every deploy, so new models are covered
and hand edits to those groups do not last; give extra access with other
groups. Only demo has fixtures; `reset_project_data` reloads them (other
projects' rows are emptied, their live tables kept).

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

6. **Add app to `INSTALLED_APPS` in `settings.py`, and to `PROJECT_APPS` in
   `apiary/projects.py`** (and to `POSTGRES_SCHEMAS` in `migrate_projects` and
   the schema check in `k8s/restore.yaml` for a PostgreSQL schema). Its
   tables are then created live; give it models only for data fixed in code,
   as demo has.

7. **Add fixture entry in `apiary/fixtures/projects.yaml`** (Compose and
   k8s both migrate it through `migrate_projects`)

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

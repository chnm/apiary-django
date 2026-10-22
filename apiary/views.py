"""
Apiary application views.
"""
import posixpath
import tempfile
import subprocess
import time
import sys
import json
from pathlib import Path
from django.apps import apps
from django.db import connections, router
from django.urls import reverse
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.core.files.storage import storages
from django.http import FileResponse, Http404, StreamingHttpResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from apiary.decorators import superuser_required
from apiary.export import write_project_zip
from apiary.status import snapshot

# Whitelist of allowed management commands with their configurations
PROJECT_APPS = ('bom', 'connthreads', 'mappingviolence', 'relec', 'pinkertons')

ALLOWED_COMMANDS = {
    'test_command': {
        'name': 'Test Command',
        'description': 'Simple test command for demonstration',
        'args': [
            {'name': 'message', 'type': 'text', 'default': 'Hello from test command!', 'required': False}
        ],
        'timeout': 30
    },
    'check_storage': {
        'name': 'Check Media Storage',
        'description': 'Write, read, serve and delete a probe file in public and private storage',
        'args': [],
        'timeout': 60
    },
    'init_project_groups': {
        'name': 'Initialize Project Groups',
        'description': 'Create project-specific user groups',
        'args': [],
        'timeout': 30
    },
    'reset_project_data': {
        'name': 'Reset Project Data',
        'description': (
            "Delete every row in a project's tables and reload its fixtures. "
            'Cannot be undone, except from the nightly backup. Type the project name to confirm.'
        ),
        'args': [
            {'name': 'project', 'type': 'select', 'choices': PROJECT_APPS, 'required': True},
            {'name': 'confirm', 'type': 'text', 'default': '', 'placeholder': 'project name again', 'required': True},
        ],
        'timeout': 120
    },
    'snapshot_project': {
        'name': 'Snapshot Project Data',
        'description': "Save a copy of a project's data to object storage. Admin > Data snapshots lists them.",
        'args': [
            {'name': 'project', 'type': 'select', 'choices': PROJECT_APPS, 'required': True},
            {'name': 'label', 'type': 'text', 'default': '', 'placeholder': 'optional note', 'required': False},
        ],
        'timeout': 120
    },
    'prune_snapshots': {
        'name': 'Prune Data Snapshots',
        'description': (
            'Delete automatic snapshots (before resets and restores) past retention. Manual snapshots '
            'and those sites were built from are kept. Preview first.'
        ),
        'args': [
            {'name': 'mode', 'type': 'select', 'choices': ('preview', 'delete'), 'required': True},
            {'name': 'keep', 'type': 'number', 'default': '10', 'required': False},
            {'name': 'days', 'type': 'number', 'default': '30', 'required': False},
        ],
        'timeout': 120
    },
    'restore_project_snapshot': {
        'name': 'Restore Project Snapshot',
        'description': (
            "Replace a project's data with a snapshot's. The current data is snapshotted first, "
            "so this can be undone. Type the snapshot's project name to confirm."
        ),
        'args': [
            {'name': 'snapshot', 'type': 'number', 'default': '', 'placeholder': 'snapshot number', 'required': True},
            {'name': 'confirm', 'type': 'text', 'default': '', 'placeholder': 'project name', 'required': True},
        ],
        'timeout': 180
    },
}

ROW_LIMIT = 100
# Root page D3 sample per project: Ninja dataset, label fields, value field.
SAMPLE_CHARTS = {
    'bom': ('deaths_by_year', ['year', 'type'], 'deaths'),
    'connthreads': ('circulation_by_year', ['year', 'type'], 'circulation'),
    'mappingviolence': ('testimonies_by_month', ['month', 'crime'], 'testimonies'),
    'relec': ('denominations_by_family', ['family'], 'denominations'),
    'pinkertons': ('activities_by_month', ['month', 'investigation'], 'activities'),
}


def project_tables_page(request):
    """
    Show every project model as a plain table, capped at ROW_LIMIT rows each,
    with its database backend and client-side DRF and Ninja API samples.

    Public: anyone can read the tables. The API samples use the visitor's own
    permissions, so they load only for users with access to the project.
    """
    projects = []
    for label in PROJECT_APPS:
        config = apps.get_app_config(label)
        tables = []
        for model in config.get_models():
            fields = model._meta.concrete_fields
            queryset = model.objects.order_by('pk')
            tables.append({
                'name': model._meta.verbose_name_plural,
                'db_table': model._meta.db_table,
                'columns': [field.verbose_name for field in fields],
                'rows': [
                    [field.value_from_object(obj) for field in fields]
                    for obj in queryset[:ROW_LIMIT]
                ],
                'count': queryset.count(),
            })
        first = next(config.get_models())
        alias = router.db_for_read(first)
        dataset, label_fields, value_field = SAMPLE_CHARTS[label]
        projects.append({
            'label': label,
            'name': config.verbose_name,
            'database': connections[alias].display_name,
            'alias': alias,
            'tables': tables,
            'drf_url': reverse(f'{label}-{first._meta.model_name}-list'),
            'chart': {
                'url': reverse(f'datasets:{label}_{dataset}'),
                'label': ','.join(label_fields),
                'value': value_field,
            },
        })
    return render(request, 'project-tables.html', {'projects': projects, 'limit': ROW_LIMIT})


@staff_member_required
def whoami_page(request):
    """
    Page to display logged in user details.
    
    This view is restricted to staff members only.
    """
    context = {
        'user': request.user,
        'groups': request.user.groups.all(),
    }
    return render(request, 'whoami.html', context)


@superuser_required
def management_commands_dashboard(request):
    """
    Dashboard view for running management commands.
    
    This view is restricted to superusers only.
    """
    context = {
        'commands': ALLOWED_COMMANDS,
        'projects': PROJECT_APPS,
    }
    return render(request, 'management-commands-dashboard.html', context)


@superuser_required
@require_GET
def export_project(request):
    """
    Download one project as a ZIP of CSV files with a datapackage.json.
    Superusers only: an export is every row of the project at once.
    """
    project = request.GET.get('project')
    if project not in PROJECT_APPS:
        raise Http404
    # Built before the response starts, so a failure is an error page rather
    # than a truncated download; past 16 MB it spills to /tmp.
    archive = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    write_project_zip(project, archive)
    archive.seek(0)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
    response = FileResponse(archive, as_attachment=True, filename=f'{project}-{stamp}.zip',
                            content_type='application/zip')
    response['Cache-Control'] = 'private, no-store'
    return response


@superuser_required
@require_POST
def run_management_command(request):
    """
    Execute allowed management commands via AJAX request.
    
    Returns JSON response with command output.
    Restricted to superusers only.
    """
    try:
        command_name = request.POST.get('command')
        
        # Validate command is in whitelist
        if command_name not in ALLOWED_COMMANDS:
            return JsonResponse({
                'success': False,
                'error': f'Command "{command_name}" is not allowed'
            }, status=400)
        
        command_config = ALLOWED_COMMANDS[command_name]
        
        # Build command arguments
        # The image's read-only root has no uv cache, so use this interpreter directly.
        cmd = [sys.executable, 'manage.py', command_name]
        
        # Add command-specific arguments
        for arg in command_config.get('args', []):
            arg_name = arg['name']
            arg_value = request.POST.get(arg_name)
            
            if arg_value:
                cmd.append(f'--{arg_name}')
                cmd.append(arg_value)
            elif arg.get('required'):
                return JsonResponse({
                    'success': False,
                    'error': f'Required argument "{arg_name}" is missing'
                }, status=400)
        
        # Run the management command
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=command_config.get('timeout', 30),
            cwd=Path(__file__).resolve().parent.parent,
        )
        
        success = result.returncode == 0
        return JsonResponse({
            'success': success,
            'output': result.stdout,
            'error': (result.stderr or None) if success else (result.stderr or result.stdout or f'Exited with code {result.returncode}'),
            'return_code': result.returncode
        })
    except subprocess.TimeoutExpired:
        return JsonResponse({
            'success': False,
            'error': 'Command timed out'
        }, status=500)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


MEDIA_STORAGES = {"public": "public", "private": "default"}


def media(request, visibility, path):
    """
    Serve a stored file. Public files are open to anyone; a private file needs a
    permission in the app named by its first path segment (e.g. private/bom/...).
    """
    if visibility == "private":
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.has_module_perms(path.split("/", 1)[0]):
            raise PermissionDenied
    storage = storages[MEDIA_STORAGES[visibility]]
    try:
        if not storage.exists(path):
            raise Http404
        response = FileResponse(storage.open(path), filename=posixpath.basename(path))
    except SuspiciousOperation:
        raise Http404
    response["Cache-Control"] = "public, max-age=3600" if visibility == "public" else "private, no-store"
    # Uploads are served from the app's own origin; never let one run script there.
    response["Content-Security-Policy"] = "sandbox"
    return response


@superuser_required
def status_page(request):
    """Live system status, refreshed by polling status_json or streaming status_stream."""
    return render(request, 'status.html', {'snapshot': snapshot()})


@superuser_required
def status_json(request):
    """Polling: the page fetches one snapshot every few seconds."""
    return JsonResponse(snapshot())


STATUS_STREAM_EVENTS = 5
STATUS_STREAM_INTERVAL = 2


@superuser_required
def status_stream(request):
    """
    Server-sent events: one open response carrying a snapshot every few seconds.

    Each stream ends after about ten seconds, inside the gateway's response timeout
    and without pinning a gunicorn thread; EventSource reconnects on its own.
    """
    def events():
        yield f"retry: {STATUS_STREAM_INTERVAL * 1000}\n\n"
        for i in range(STATUS_STREAM_EVENTS):
            if i:
                time.sleep(STATUS_STREAM_INTERVAL)
            yield f"data: {json.dumps(snapshot())}\n\n"

    response = StreamingHttpResponse(events(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    return response

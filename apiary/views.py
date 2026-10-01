"""
Apiary application views.
"""
import posixpath
import subprocess
import sys
import json
from pathlib import Path
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.core.files.storage import storages
from django.http import FileResponse, Http404
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from apiary.decorators import superuser_required

# Whitelist of allowed management commands with their configurations
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
}

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
    }
    return render(request, 'management-commands-dashboard.html', context)


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

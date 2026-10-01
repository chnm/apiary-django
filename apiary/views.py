"""
Apiary application views.
"""
import subprocess
import json
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
        cmd = ['uv', 'run', 'manage.py', command_name]
        
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
            timeout=command_config.get('timeout', 30)
        )
        
        return JsonResponse({
            'success': True,
            'output': result.stdout,
            'error': result.stderr if result.stderr else None,
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

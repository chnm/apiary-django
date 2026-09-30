"""
Custom decorators for Apiary views.
"""
from functools import wraps
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import user_passes_test


def superuser_required(
    function=None,
    redirect_field_name=REDIRECT_FIELD_NAME,
    login_url='admin:login'
):
    """
    Decorator for views that checks that the user is a superuser,
    redirecting to the log-in page if necessary.
    
    Usage:
        @superuser_required
        def my_view(request):
            ...
    
    Or with custom parameters:
        @superuser_required(login_url='/custom-login/')
        def my_view(request):
            ...
    """
    def check_superuser(user):
        return user.is_active and user.is_superuser
    
    actual_decorator = user_passes_test(
        check_superuser,
        login_url=login_url,
        redirect_field_name=redirect_field_name
    )
    
    if function:
        return actual_decorator(function)
    return actual_decorator

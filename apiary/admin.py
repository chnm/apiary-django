from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group

from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.admin import ModelAdmin

from .models import APIToken, Project


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    # Forms loaded from `unfold.forms`
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass

@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    list_display = ['name', 'app_label', 'description']
    search_fields = ['name', 'app_label']
    list_filter = ['app_label']


@admin.register(APIToken)
class APITokenAdmin(ModelAdmin):
    """
    Superusers create and revoke tokens for any user. A token acts as its
    user, so issuing one is as sensitive as setting their password.
    """
    list_display = ['name', 'user', 'hint', 'status', 'created_at', 'expires_at', 'last_used_at']
    list_filter = ['user']
    search_fields = ['name', 'hint', 'user__username']
    actions = ['revoke']

    def get_fields(self, request, obj=None):
        if obj is None:
            return ['user', 'name', 'expires_at']
        return ['user', 'name', 'hint', 'created_at', 'expires_at', 'last_used_at', 'revoked_at']

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ['user', 'hint', 'created_at', 'last_used_at', 'revoked_at']

    @admin.display(description='Status')
    def status(self, obj):
        if obj.revoked_at:
            return 'Revoked'
        return 'Active' if obj.is_active else 'Expired'

    def save_model(self, request, obj, form, change):
        if not change:
            request.new_api_token = obj.generate()
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        # The only time the secret exists in clear: render it rather than
        # redirecting, so it never passes through the messages cookie.
        return TemplateResponse(request, 'admin/apiary/apitoken/created.html', {
            **self.admin_site.each_context(request),
            'title': 'API token created',
            'opts': self.opts,
            'token': obj,
            'secret': request.new_api_token,
            'mcp_url': request.build_absolute_uri('/mcp'),
            'changelist_url': reverse('admin:apiary_apitoken_changelist'),
        })

    @admin.action(description='Revoke selected tokens')
    def revoke(self, request, queryset):
        revoked = queryset.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        self.message_user(request, f'Revoked {revoked} token{"s" if revoked != 1 else ""}.')

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)

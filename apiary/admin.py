from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group

from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.admin import ModelAdmin

from . import pages
from .models import APIToken, DataSnapshot, Project, Site, SiteVersion


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


class SuperuserOnly:
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(DataSnapshot)
class DataSnapshotAdmin(SuperuserOnly, ModelAdmin):
    """
    Read-only: snapshots are taken and restored with the snapshot_project and
    restore_project_snapshot commands (/apiary/mgmt/). Deleting a row here
    leaves its files in storage.
    """
    list_display = ['id', 'project', 'kind', 'label', 'created_at', 'created_by', 'rows', 'size']
    list_filter = ['project', 'kind']
    readonly_fields = ['key', 'project', 'kind', 'label', 'created_at', 'created_by',
                       'row_counts', 'migrations', 'sha256', 'size']

    @admin.display(description='Rows')
    def rows(self, obj):
        return sum(obj.row_counts.values())


class SiteVersionInline(admin.TabularInline):
    model = SiteVersion
    extra = 0
    can_delete = False
    fields = ['number', 'published_at', 'created_by', 'file_count', 'datasets', 'data_snapshot']
    readonly_fields = fields

    @admin.display(description='Files')
    def file_count(self, obj):
        return len(obj.files)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Site)
class SiteAdmin(SuperuserOnly, ModelAdmin):
    """Sites are built by staff through MCP; here superusers review and unpublish them."""
    list_display = ['__str__', 'title', 'live', 'url', 'created_by', 'created_at']
    list_filter = ['project']
    readonly_fields = ['project', 'name', 'title', 'created_by', 'created_at', 'live_version', 'url']
    inlines = [SiteVersionInline]
    actions = ['unpublish']

    @admin.display(description='Live version')
    def live(self, obj):
        return obj.live_version.number if obj.live_version else 'unpublished'

    @admin.display(description='URL')
    def url(self, obj):
        return pages.site_url(obj)

    @admin.action(description='Unpublish selected sites')
    def unpublish(self, request, queryset):
        for site in queryset:
            pages.unpublish(site)
        self.message_user(request, f'Unpublished {queryset.count()} site(s).')


from django import forms
from django.contrib import admin, messages
from django.core.files.storage import storages
from django.http import FileResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group

from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.admin import ModelAdmin
from unfold.decorators import action
from unfold.enums import ActionVariant
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

from . import pages, projectdata
from .views import PROJECT_APPS
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

    def has_superuser_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


class TakeSnapshotForm(forms.Form):
    project = forms.ChoiceField(choices=[(p, p) for p in PROJECT_APPS], widget=UnfoldAdminSelectWidget)
    label = forms.CharField(required=False, max_length=200, widget=UnfoldAdminTextInputWidget,
                            help_text='Optional note, e.g. why you took it.')


class RestoreForm(forms.Form):
    confirm = forms.CharField(widget=UnfoldAdminTextInputWidget, help_text='Type the project name to confirm.')

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project

    def clean_confirm(self):
        if self.cleaned_data['confirm'] != self.project:
            raise forms.ValidationError(f'Type "{self.project}" to confirm.')
        return self.cleaned_data['confirm']


@admin.register(DataSnapshot)
class DataSnapshotAdmin(SuperuserOnly, ModelAdmin):
    """
    Superusers take, compare, restore, download and delete project data
    snapshots (apiary.projectdata). Deleting one deletes its files too.
    """
    list_filter = ['project', 'kind', 'created_at']
    search_fields = ['label']
    readonly_fields = ['key', 'project', 'kind', 'label', 'created_at', 'created_by',
                       'row_counts', 'migrations', 'schema', 'sha256', 'size', 'used_by']
    actions_list = ['take_snapshot']
    actions_detail = ['restore', 'download']

    def get_list_display(self, request):
        # One migration lookup per project for this request, not one per row.
        applied = {project: projectdata.applied_migrations(project) for project in PROJECT_APPS}

        @admin.display(description='Schema')
        def schema(obj):
            return 'current' if obj.migrations == applied[obj.project] else 'older (not restorable)'

        return ['id', 'project', 'kind', 'label', 'created_at', 'created_by', 'rows', 'size_kb', schema]

    @admin.display(description='Rows')
    def rows(self, obj):
        return sum(obj.row_counts.values())

    @admin.display(description='Size (KB)')
    def size_kb(self, obj):
        return round(obj.size / 1024, 1)

    @admin.display(description='Schema')
    def schema(self, obj):
        current = projectdata.applied_migrations(obj.project)
        return 'current' if obj.migrations == current else 'older (not restorable)'

    @admin.display(description='Page versions built from it')
    def used_by(self, obj):
        versions = SiteVersion.objects.filter(data_snapshot=obj).select_related('site')
        return ', '.join(str(v) for v in versions) or '-'

    def delete_model(self, request, obj):
        projectdata.delete_snapshot(obj)

    def delete_queryset(self, request, queryset):
        for snapshot in queryset:
            projectdata.delete_snapshot(snapshot)

    def page(self, request, template, title, **context):
        return TemplateResponse(request, f'admin/apiary/datasnapshot/{template}', {
            **self.admin_site.each_context(request), 'opts': self.opts, 'title': title, **context,
        })

    # Unfold's action views only require staff; permissions applies has_superuser_permission.
    @action(description='Take snapshot', url_path='take', icon='add_a_photo', permissions=['superuser'])
    def take_snapshot(self, request):
        form = TakeSnapshotForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            snapshot, _ = projectdata.take_snapshot(form.cleaned_data['project'], kind='manual',
                                                    user=request.user, label=form.cleaned_data['label'])
            self.message_user(request, f'Took snapshot #{snapshot.pk} of {snapshot.project}.', messages.SUCCESS)
            return HttpResponseRedirect(reverse('admin:apiary_datasnapshot_change', args=[snapshot.pk]))
        return self.page(request, 'take.html', 'Take a data snapshot', form=form)

    @action(description='Restore', url_path='restore', icon='restore', variant=ActionVariant.DANGER,
            permissions=['superuser'])
    def restore(self, request, object_id):
        snapshot = self.get_object(request, object_id)
        current = projectdata.row_counts(snapshot.project)
        applied = projectdata.applied_migrations(snapshot.project)
        compatible = snapshot.migrations == applied
        form = RestoreForm(request.POST or None, project=snapshot.project)
        if request.method == 'POST' and compatible and form.is_valid():
            try:
                before = projectdata.restore_snapshot(snapshot, user=request.user)
            except projectdata.SnapshotError as error:
                self.message_user(request, str(error), messages.ERROR)
            else:
                self.message_user(request, f'Restored {snapshot.project} from snapshot #{snapshot.pk}. '
                                           f'Snapshot #{before.pk} holds the data it replaced.', messages.SUCCESS)
                return HttpResponseRedirect(reverse('admin:apiary_datasnapshot_change', args=[before.pk]))
        tables = sorted(set(snapshot.row_counts) | set(current))
        return self.page(request, 'restore.html', f'Restore {snapshot.project} from snapshot #{snapshot.pk}',
                         snapshot=snapshot, form=form, compatible=compatible,
                         applied=applied,
                         rows=[(t, snapshot.row_counts.get(t, 0), current.get(t, 0)) for t in tables],
                         used_by=self.used_by(snapshot))

    @action(description='Download data', url_path='download', icon='download', permissions=['superuser'])
    def download(self, request, object_id):
        snapshot = self.get_object(request, object_id)
        return FileResponse(storages['default'].open(snapshot.data_path, 'rb'), as_attachment=True,
                            filename=f'{snapshot.project}-snapshot-{snapshot.pk}.json.gz')


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


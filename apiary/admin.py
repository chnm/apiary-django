from django import forms
from django.contrib import admin, messages
from django.core.files.storage import storages
import tempfile

from django.http import FileResponse, HttpResponseRedirect
from django.utils.html import format_html
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
from unfold.widgets import UnfoldAdminFileFieldWidget, UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

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


class ImportSnapshotForm(forms.Form):
    bundle = forms.FileField(widget=UnfoldAdminFileFieldWidget,
                             help_text="A zip exported from Data snapshots (manifest.json and data.json.gz).")


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
    actions_list = ['take_snapshot', 'import_snapshot']
    actions_detail = ['restore', 'export', 'download']

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
        return TemplateResponse(request, f'admin/apiary/{template}', {
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
        return self.page(request, 'form_page.html', 'Take a data snapshot', form=form, submit_label='Take snapshot',
                         intro="Saves a copy of one project's data to object storage, read from a single moment. "
                               "It can be restored later from its page here.")

    @action(description='Import', url_path='import', icon='upload', permissions=['superuser'])
    def import_snapshot(self, request):
        form = ImportSnapshotForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            try:
                snapshot = projectdata.import_bundle(form.cleaned_data['bundle'], user=request.user)
            except projectdata.SnapshotError as error:
                form.add_error('bundle', str(error))
            else:
                self.message_user(request, f'Imported snapshot #{snapshot.pk} of {snapshot.project}. Restore it from '
                                           'its page; the schema must match.', messages.SUCCESS)
                return HttpResponseRedirect(reverse('admin:apiary_datasnapshot_change', args=[snapshot.pk]))
        return self.page(request, 'form_page.html', 'Import a data snapshot', form=form, submit_label='Import',
                         intro='Adds a snapshot exported from this or another Apiary. It is checked (checksum, '
                               'project, only that project\'s records) and restores like any other.')

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
        return self.page(request, 'datasnapshot/restore.html', f'Restore {snapshot.project} from snapshot #{snapshot.pk}',
                         snapshot=snapshot, form=form, compatible=compatible,
                         applied=applied,
                         rows=[(t, snapshot.row_counts.get(t, 0), current.get(t, 0)) for t in tables],
                         used_by=self.used_by(snapshot))

    @action(description='Export', url_path='export', icon='archive', permissions=['superuser'])
    def export(self, request, object_id):
        snapshot = self.get_object(request, object_id)
        bundle = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
        projectdata.export_bundle(snapshot, bundle)
        bundle.seek(0)
        return FileResponse(bundle, as_attachment=True, content_type='application/zip',
                            filename=f'{snapshot.project}-snapshot-{snapshot.pk}.zip')

    @action(description='Download data', url_path='download', icon='download', permissions=['superuser'])
    def download(self, request, object_id):
        snapshot = self.get_object(request, object_id)
        return FileResponse(storages['default'].open(snapshot.data_path, 'rb'), as_attachment=True,
                            filename=f'{snapshot.project}-snapshot-{snapshot.pk}.json.gz')


class SiteVersionInline(admin.TabularInline):
    model = SiteVersion
    extra = 0
    can_delete = False
    fields = ['number', 'status', 'published_at', 'created_by', 'file_list', 'datasets', 'snapshot']
    readonly_fields = fields

    @admin.display(description='Status')
    def status(self, obj):
        if obj.site.live_version_id == obj.pk:
            return 'live'
        return 'published' if obj.published_at else 'draft'

    @admin.display(description='Files')
    def file_list(self, obj):
        return format_html('<br>'.join(['{} ({} B)'] * len(obj.files)),
                           *[part for path, item in sorted(obj.files.items()) for part in (path, item['size'])]) or '-'

    @admin.display(description='Data snapshot')
    def snapshot(self, obj):
        if not obj.data_snapshot_id:
            return '-'
        url = reverse('admin:apiary_datasnapshot_change', args=[obj.data_snapshot_id])
        return format_html('<a href="{}">#{}</a>', url, obj.data_snapshot_id)

    def has_add_permission(self, request, obj=None):
        return False


class SiteForm(forms.ModelForm):
    class Meta:
        model = Site
        fields = ['project', 'name', 'title']
        widgets = {'project': UnfoldAdminSelectWidget(choices=[(p, p) for p in PROJECT_APPS])}

    def clean_name(self):
        name = self.cleaned_data['name']
        if not pages.SITE_NAME.match(name):
            raise forms.ValidationError('1-50 lowercase letters, digits and hyphens, starting with a letter or digit.')
        return name

    def clean_project(self):
        if self.cleaned_data['project'] not in PROJECT_APPS:
            raise forms.ValidationError('Unknown project.')
        return self.cleaned_data['project']


class UploadForm(forms.Form):
    archive = forms.FileField(label='Zip', widget=UnfoldAdminFileFieldWidget,
                              help_text='Its files are added to the draft, replacing ones at the same paths. '
                                        'A single top-level folder is stripped.')


class RollbackForm(forms.Form):
    version = forms.TypedChoiceField(coerce=int, widget=UnfoldAdminSelectWidget)

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['version'].choices = [
            (v.number, f'v{v.number}, published {v.published_at:%Y-%m-%d %H:%M}'
                       + (' (live)' if v.pk == site.live_version_id else ''))
            for v in site.versions.filter(published_at__isnull=False)
        ]


@admin.register(Site)
class SiteAdmin(SuperuserOnly, ModelAdmin):
    """
    Static sites on the pages host (apiary.pages). Staff build them over MCP;
    here superusers can also create one, upload a zip into its draft, publish,
    roll back, unpublish and delete (with its files).
    """
    form = SiteForm
    list_display = ['__str__', 'title', 'project', 'live', 'versions_count', 'link', 'created_by', 'created_at']
    list_filter = ['project']
    search_fields = ['name', 'title']
    inlines = [SiteVersionInline]
    actions = ['unpublish_selected']
    actions_detail = ['open_site', 'upload', 'publish', 'rollback', 'unpublish']

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def get_inlines(self, request, obj):
        return self.inlines if obj else []  # a new site has no versions yet

    def get_fields(self, request, obj=None):
        return ['project', 'name', 'title'] if obj is None else ['project', 'name', 'title', 'link', 'live_version',
                                                                 'created_by', 'created_at']

    def get_readonly_fields(self, request, obj=None):
        return [] if obj is None else ['project', 'name', 'link', 'live_version', 'created_by', 'created_at']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        pages.delete_site(obj)

    def delete_queryset(self, request, queryset):
        for site in queryset:
            pages.delete_site(site)

    @admin.display(description='Live version')
    def live(self, obj):
        return obj.live_version.number if obj.live_version else 'unpublished'

    @admin.display(description='Versions')
    def versions_count(self, obj):
        return obj.versions.count()

    @admin.display(description='URL')
    def link(self, obj):
        url = pages.site_url(obj)
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, url) if obj.live_version_id else url

    @admin.action(description='Unpublish selected sites')
    def unpublish_selected(self, request, queryset):
        for site in queryset:
            pages.unpublish(site)
        self.message_user(request, f'Unpublished {queryset.count()} site(s).')

    def page(self, request, title, **context):
        return TemplateResponse(request, 'admin/apiary/form_page.html', {
            **self.admin_site.each_context(request), 'opts': self.opts, 'title': title, **context,
        })

    def back(self, site):
        return HttpResponseRedirect(reverse('admin:apiary_site_change', args=[site.pk]))

    @action(description='Open site', url_path='open', icon='open_in_new', permissions=['superuser'])
    def open_site(self, request, object_id):
        return HttpResponseRedirect(pages.site_url(self.get_object(request, object_id)))

    @action(description='Upload zip', url_path='upload', icon='upload', permissions=['superuser'])
    def upload(self, request, object_id):
        site = self.get_object(request, object_id)
        form = UploadForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            try:
                version = pages.upload_zip(site, form.cleaned_data['archive'], user=request.user)
            except pages.PagesError as error:
                form.add_error('archive', str(error))
            else:
                self.message_user(request, f'Draft v{version.number} now holds {len(version.files)} files. '
                                           'Publish to make it live.', messages.SUCCESS)
                return self.back(site)
        return self.page(request, f'Upload files to {site}', form=form, submit_label='Upload to draft',
                         intro=f'Files go into the draft, which is not served until it is published. Allowed: '
                               f'{", ".join(sorted(pages.CONTENT_TYPES))}; 2 MB a file, 20 MB a site.')

    @action(description='Publish draft', url_path='publish', icon='publish', permissions=['superuser'])
    def publish(self, request, object_id):
        site = self.get_object(request, object_id)
        # Detail actions are links: change state only on the confirming POST.
        if request.method != 'POST':
            draft = site.versions.filter(published_at__isnull=True).first()
            return self.page(request, f'Publish {site}', form=forms.Form(), submit_label='Publish',
                             intro=(f'Publishes draft v{draft.number} ({len(draft.files)} files), capturing its '
                                    f'datasets with a snapshot of {site.project} from the same moment.')
                             if draft else 'There is no draft; publishing starts one from the live version.')
        try:
            version = pages.publish(site, user=request.user)
        except pages.PagesError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, f'Published v{version.number} at {pages.site_url(site)}', messages.SUCCESS)
        return self.back(site)

    @action(description='Roll back', url_path='rollback', icon='history', permissions=['superuser'])
    def rollback(self, request, object_id):
        site = self.get_object(request, object_id)
        form = RollbackForm(request.POST or None, site=site)
        if request.method == 'POST' and form.is_valid():
            version = pages.rollback(site, form.cleaned_data['version'])
            self.message_user(request, f'v{version.number} is live again.', messages.SUCCESS)
            return self.back(site)
        return self.page(request, f'Roll back {site}', form=form, submit_label='Make it live',
                         intro='Serve an earlier published version. Versions never change, so this is instant '
                               'and can itself be rolled back.')

    @action(description='Unpublish', url_path='unpublish', icon='visibility_off', variant=ActionVariant.DANGER,
            permissions=['superuser'])
    def unpublish(self, request, object_id):
        site = self.get_object(request, object_id)
        if request.method != 'POST':
            return self.page(request, f'Unpublish {site}', form=forms.Form(), submit_label='Unpublish',
                             variant='danger', intro=f'Takes {pages.site_url(site)} offline. Its versions are '
                                                     'kept; Roll back makes one live again.')
        pages.unpublish(site)
        self.message_user(request, f'{site} is offline; its versions are kept.', messages.SUCCESS)
        return self.back(site)

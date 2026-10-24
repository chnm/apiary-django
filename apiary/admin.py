from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.files.storage import storages
import json
from datetime import timedelta
import tempfile

from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.urls import path
from django.utils.html import format_html
from django.template.loader import render_to_string
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
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminFileFieldWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminTextInputWidget,
)

from . import changes, diffview, mcp_oauth, projectdata, sites
from .projects import PROJECT_APPS, has_role, project_alias, project_models
from .table_pages import TablePages
from .models import APIToken, Change, DataSnapshot, OAuthClient, OAuthToken, Project, Site, SiteVersion


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


class APITokenForm(forms.ModelForm):
    # Tokens expire unless someone chooses otherwise: a forgotten token stops working by itself.
    LIFETIMES = {'30': timedelta(days=30), '90': timedelta(days=90), '365': timedelta(days=365), 'never': None}
    valid_for = forms.ChoiceField(
        choices=[('30', '30 days'), ('90', '90 days'), ('365', '1 year'), ('never', 'Never expires')],
        initial='90', widget=UnfoldAdminSelectWidget,
        help_text='"Never expires" is for unattended clients (scheduled jobs, servers) that cannot be given a new '
                  'token in time; revoke it when they stop using it. People use OAuth instead of tokens.')
    scopes = forms.MultipleChoiceField(
        choices=[(name, f"{text} ({name})") for name, text in mcp_oauth.SCOPES.items()],
        initial=["mcp:read"], widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text="mcp:read is always included.")

    class Meta:
        model = APIToken
        fields = ['user', 'name', 'valid_for', 'scopes']

    def clean_scopes(self):
        return mcp_oauth.granted_scopes(self.cleaned_data['scopes'])

    def save(self, commit=True):
        lifetime = self.LIFETIMES[self.cleaned_data['valid_for']]
        self.instance.expires_at = timezone.now() + lifetime if lifetime else None
        return super().save(commit)


@admin.register(APIToken)
class APITokenAdmin(ModelAdmin):
    """
    Superusers create and revoke tokens for any user. A token acts as its
    user, so issuing one is as sensitive as setting their password.
    """
    list_display = ['name', 'user', 'hint', 'status', 'scopes', 'created_at', 'expires_at', 'last_used_at']
    list_filter = ['user']
    search_fields = ['name', 'hint', 'user__username']
    actions = ['revoke']
    add_form = APITokenForm

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs['form'] = self.add_form
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        if obj is None:
            return ['user', 'name', 'valid_for', 'scopes']
        return ['user', 'name', 'hint', 'scopes', 'created_at', 'expires_at', 'last_used_at', 'revoked_at']

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ['user', 'hint', 'scopes', 'created_at', 'last_used_at', 'revoked_at']

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


@admin.register(OAuthToken)
class OAuthTokenAdmin(SuperuserOnly, ModelAdmin):
    """MCP clients connected over OAuth (apiary.mcp_oauth). Deleting one ends it at once."""
    list_display = ['client', 'user', 'created_at', 'last_used_at', 'refresh_expires_at']
    list_filter = ['user']
    search_fields = ['client__name', 'user__username']
    fields = ['client', 'user', 'scopes', 'created_at', 'last_used_at', 'access_expires_at', 'refresh_expires_at']
    readonly_fields = fields


@admin.register(OAuthClient)
class OAuthClientAdmin(SuperuserOnly, ModelAdmin):
    """
    MCP clients that registered themselves. Registering grants nothing, and
    ones nobody connected are dropped after a day; deleting one also ends its
    connections.
    """
    list_display = ['__str__', 'client_id', 'redirect_uris', 'created_at']
    search_fields = ['name', 'client_id']
    fields = ['name', 'client_id', 'redirect_uris', 'created_at']
    readonly_fields = fields

    @admin.display(description='Redirect URIs')
    def redirect_uris(self, obj):
        return ', '.join(obj.info.get('redirect_uris') or [])


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
                       'row_counts', 'migrations', 'restorable', 'live_tables', 'sha256', 'size', 'used_by']
    actions_list = ['take_snapshot', 'import_snapshot']
    actions_detail = ['explore', 'restore', 'export', 'download']

    def get_list_display(self, request):
        # One migration lookup per project for this request, not one per row.
        applied = {project: projectdata.applied_migrations(project) for project in PROJECT_APPS}

        @admin.display(description='Restorable')
        def restorable(obj):
            return 'yes' if obj.migrations == applied[obj.project] else 'no: older migrations'

        return ['id', 'project', 'kind', 'label', 'created_at', 'created_by', 'rows', 'size_kb', restorable]

    @admin.display(description='Rows')
    def rows(self, obj):
        return sum(obj.row_counts.values())

    @admin.display(description='Size (KB)')
    def size_kb(self, obj):
        return round(obj.size / 1024, 1)

    @admin.display(description='Restorable')
    def restorable(self, obj):
        current = projectdata.applied_migrations(obj.project)
        return 'yes' if obj.migrations == current else 'no: taken with older migrations of its code models'

    @admin.display(description='Live tables')
    def live_tables(self, obj):
        # Restoring rebuilds these tables as they were.
        return ', '.join(f"{table['name']} ({len(table['columns'])} columns)" for table in obj.schema) or '-'

    @admin.display(description='Site versions built from it')
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
            snapshot = projectdata.take_snapshot(form.cleaned_data['project'], kind='manual',
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

    EXPLORE_PAGE_SIZE = 100

    def get_urls(self):
        return [
            path('<path:object_id>/explore/<slug:table>.ndjson', self.admin_site.admin_view(self.explore_data),
                 name='apiary_datasnapshot_explore_data'),
        ] + super().get_urls()

    def snapshot_or_404(self, request, object_id):
        snapshot = self.get_object(request, object_id)
        if snapshot is None:
            raise Http404
        return snapshot

    @staticmethod
    def relation_links(project, table, schema=()):
        """
        field -> related table: live tables' from the snapshot's own schema,
        code models' from the current model if it still has that field.
        """
        for spec in schema:
            if spec['name'] == table:
                return {column['name']: column['target'] for column in spec['columns'] if column['type'] == 'relation'}
        try:
            model = apps.get_model(project, table)
        except LookupError:
            return {}
        return {field.name: field.related_model._meta.model_name for field in model._meta.concrete_fields
                if field.is_relation and field.related_model._meta.app_label == project}

    @staticmethod
    def cell(value):
        if value is None:
            return ''
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    @action(description='Explore', url_path='explore', icon='table_view', permissions=['superuser'])
    def explore(self, request, object_id):
        snapshot = self.snapshot_or_404(request, object_id)
        tables = projectdata.snapshot_tables(snapshot)
        name = request.GET.get('table') or next(iter(tables), None)
        if name is not None and name not in tables:
            raise Http404
        everything = tables.get(name, [])
        columns = list(dict.fromkeys(column for row in everything for column in row))  # id first
        rows = everything
        wanted = request.GET.get('id', '').strip()
        if wanted:
            rows = [row for row in rows if str(row['id']) == wanted]
        search = request.GET.get('q', '').strip()
        if search:
            needle = search.lower()
            rows = [row for row in rows if any(needle in self.cell(value).lower() for value in row.values())]
        page = Paginator(rows, self.EXPLORE_PAGE_SIZE).get_page(request.GET.get('page'))
        links = self.relation_links(snapshot.project, name, snapshot.schema) if name else {}
        body = [[(self.cell(row.get(column)), links.get(column) if row.get(column) is not None else None)
                 for column in columns] for row in page]
        console = [{'name': table, 'rows': len(table_rows),
                    'url': reverse('admin:apiary_datasnapshot_explore_data', args=[snapshot.pk, table])}
                   for table, table_rows in tables.items()]
        return self.page(request, 'datasnapshot/explore.html', f'Explore snapshot #{snapshot.pk} of {snapshot.project}',
                         snapshot=snapshot, tables={t: len(r) for t, r in tables.items()}, table=name,
                         columns=columns, body=body, page=page, search=search, wanted=wanted,
                         matched=len(rows), console=console)

    def explore_data(self, request, object_id, table):
        """One snapshot table as newline-delimited JSON, for the SQL console."""
        if not self.has_superuser_permission(request):
            raise PermissionDenied
        rows = projectdata.snapshot_tables(self.snapshot_or_404(request, object_id)).get(table)
        if rows is None:
            raise Http404
        response = HttpResponse(''.join(json.dumps(row, cls=DjangoJSONEncoder) + '\n' for row in rows),
                                content_type='application/x-ndjson')
        response['Cache-Control'] = 'private, max-age=3600'  # snapshots never change
        return response

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
    fields = ['number', 'status', 'published_at', 'created_by', 'file_list', 'snapshot']
    readonly_fields = fields

    @admin.display(description='Status')
    def status(self, obj):
        if obj.site.live_version_id == obj.pk:
            return 'live'
        return 'published' if obj.published_at else 'draft'

    @admin.display(description='Files')
    def file_list(self, obj):
        if not obj.files:
            return '-'
        return format_html('<br>'.join(['{} ({} B)'] * len(obj.files)),
                           *[part for path, item in sorted(obj.files.items()) for part in (path, item['size'])])

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

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk is None:  # the same checks as MCP's create_site
            try:
                sites.check_new_site(cleaned.get('project'), cleaned.get('name'))
            except sites.SiteError as error:
                raise forms.ValidationError(str(error))
        return cleaned

    def validate_unique(self):
        # check_new_site reports a taken name, naming the site that has it.
        pass


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
    Static sites, each on its own host (apiary.sites). Staff build them over MCP;
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

    def get_deleted_objects(self, objs, request):
        # List the publish snapshots too, so the confirmation shows all that goes.
        deleted, counts, perms_needed, protected = super().get_deleted_objects(objs, request)
        snapshots = [snapshot for site in objs for snapshot in sites.publish_snapshots(site)]
        if snapshots:
            deleted.append([f"Data snapshot (taken to publish it, with its files): {snapshot}"
                            for snapshot in snapshots])
            counts[DataSnapshot._meta.verbose_name_plural] = len(snapshots)
        return deleted, counts, perms_needed, protected

    def delete_model(self, request, obj):
        sites.delete_site(obj)

    def delete_queryset(self, request, queryset):
        for site in queryset:
            sites.delete_site(site)

    @admin.display(description='Live version')
    def live(self, obj):
        return obj.live_version.number if obj.live_version else 'unpublished'

    @admin.display(description='Versions')
    def versions_count(self, obj):
        return obj.versions.count()

    @admin.display(description='URL')
    def link(self, obj):
        url = sites.site_url(obj)
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, url) if obj.live_version_id else url

    @admin.action(description='Unpublish selected sites')
    def unpublish_selected(self, request, queryset):
        for site in queryset:
            sites.unpublish(site)
        self.message_user(request, f'Unpublished {queryset.count()} site(s).')

    def page(self, request, title, **context):
        return TemplateResponse(request, 'admin/apiary/form_page.html', {
            **self.admin_site.each_context(request), 'opts': self.opts, 'title': title, **context,
        })

    def back(self, site):
        return HttpResponseRedirect(reverse('admin:apiary_site_change', args=[site.pk]))

    @action(description='Open site', url_path='open', icon='open_in_new', permissions=['superuser'])
    def open_site(self, request, object_id):
        return HttpResponseRedirect(sites.site_url(self.get_object(request, object_id)))

    @action(description='Upload zip', url_path='upload', icon='upload', permissions=['superuser'])
    def upload(self, request, object_id):
        site = self.get_object(request, object_id)
        form = UploadForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            try:
                version = sites.upload_zip(site, form.cleaned_data['archive'], user=request.user)
            except sites.SiteError as error:
                form.add_error('archive', str(error))
            else:
                self.message_user(request, f'Draft v{version.number} now holds {len(version.files)} files. '
                                           'Publish to make it live.', messages.SUCCESS)
                return self.back(site)
        return self.page(request, f'Upload files to {site}', form=form, submit_label='Upload to draft',
                         intro=f'Files go into the draft, which is not served until it is published. Allowed: '
                               f'{", ".join(sorted(sites.CONTENT_TYPES))}; 2 MB a file, 20 MB a site.')

    @action(description='Publish draft', url_path='publish', icon='publish', permissions=['superuser'])
    def publish(self, request, object_id):
        site = self.get_object(request, object_id)
        # Detail actions are links: change state only on the confirming POST.
        if request.method != 'POST':
            draft = site.versions.filter(published_at__isnull=True).first()
            return self.page(request, f'Publish {site}', form=forms.Form(), submit_label='Publish',
                             intro=f'Publishes draft v{draft.number} ({len(draft.files)} files).'
                             if draft else 'There is no draft; publishing starts one from the live version.')
        try:
            version = sites.publish(site, user=request.user)
        except sites.SiteError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, f'Published v{version.number} at {sites.site_url(site)}', messages.SUCCESS)
        return self.back(site)

    @action(description='Roll back', url_path='rollback', icon='history', permissions=['superuser'])
    def rollback(self, request, object_id):
        site = self.get_object(request, object_id)
        form = RollbackForm(request.POST or None, site=site)
        if request.method == 'POST' and form.is_valid():
            version = sites.rollback(site, form.cleaned_data['version'])
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
                             variant='danger', intro=f'Takes {sites.site_url(site)} offline. Its versions are '
                                                     'kept; Roll back makes one live again.')
        sites.unpublish(site)
        self.message_user(request, f'{site} is offline; its versions are kept.', messages.SUCCESS)
        return self.back(site)


@admin.register(Change)
class ChangeAdmin(SuperuserOnly, ModelAdmin):
    """
    Every change to project tables and records (apiary.changes): proposals to
    apply or reject, and applied records changes to revert. Never deleted.
    """
    list_display = ['id', 'project', 'kind', 'status', 'summary', 'proposed_by', 'proposed_via', 'proposed_at', 'decided_at']
    list_filter = ['project', 'kind', 'status']
    search_fields = ['summary', 'reason']
    readonly_fields = ['project', 'kind', 'status', 'summary', 'reason', 'rows_view', 'effects', 'sql',
                       'operations_json', 'proposed_by', 'proposed_via', 'proposed_at', 'expires_at', 'decided_by',
                       'decided_via', 'decided_at', 'schema_version', 'snapshot', 'reverts', 'error']
    fields = readonly_fields
    actions_detail = ['apply_change', 'reject_change', 'revert_change']
    VIA = 'admin'

    def has_delete_permission(self, request, obj=None):
        return False

    @staticmethod
    def pretty(value):
        return format_html('<pre class="whitespace-pre-wrap text-xs">{}</pre>', json.dumps(value, indent=2, cls=DjangoJSONEncoder))

    @admin.display(description='Operations')
    def operations_json(self, obj):
        return self.pretty(obj.operations)

    @admin.display(description='Effects')
    def effects(self, obj):
        effects = obj.preview.get('effects') or []
        return format_html('<br>'.join(['{}'] * len(effects)), *effects) if effects else '-'

    @admin.display(description='SQL')
    def sql(self, obj):
        statements = obj.preview.get('sql')
        return format_html('<pre class="whitespace-pre-wrap text-xs">{}</pre>', ';\n'.join(statements)) if statements else '-'

    @admin.display(description='Rows')
    def rows_view(self, obj):
        found = changes.diff(obj)
        if found is None:
            return '-'
        return render_to_string('admin/apiary/change_diff.html', {'diff': diffview.readable(obj, found)})

    def decide(self, request, object_id, verb, run):
        change = self.get_object(request, object_id)
        back = HttpResponseRedirect(reverse('admin:apiary_change_change', args=[object_id]))
        # Detail actions are links: change state only on the confirming POST.
        if request.method != 'POST':
            return TemplateResponse(request, 'admin/apiary/form_page.html', {
                **self.admin_site.each_context(request), 'opts': self.opts, 'title': f'{verb} change #{change.pk}',
                'intro': f'{change.summary}. {change.reason}'.strip(), 'form': forms.Form(), 'submit_label': verb,
            })
        try:
            run(change)
        except changes.ChangeError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, f'{verb}: change #{change.pk}.', messages.SUCCESS)
        return back

    @action(description='Apply', url_path='apply', icon='check', permissions=['superuser'])
    def apply_change(self, request, object_id):
        return self.decide(request, object_id, 'Apply', lambda change: changes.apply(change, request.user, via=self.VIA))

    @action(description='Reject', url_path='reject', icon='block', permissions=['superuser'])
    def reject_change(self, request, object_id):
        return self.decide(request, object_id, 'Reject', lambda change: changes.reject(change, request.user, via=self.VIA))

    @action(description='Revert', url_path='revert', icon='undo', permissions=['superuser'])
    def revert_change(self, request, object_id):
        return self.decide(request, object_id, 'Revert', lambda change: changes.revert(change, request.user, via=self.VIA))

    def get_urls(self):
        pages = TablePages(self.admin_site, {label: title for title, label in settings.PROJECTS})
        view = self.admin_site.admin_view
        return [
            path('tables/<slug:project>/', view(self.tables_view), name='apiary_project_tables'),
            path('tables/<slug:project>/<slug:table>/', view(pages.rows), name='apiary_project_table'),
            path('tables/<slug:project>/<slug:table>/add/', view(pages.edit), name='apiary_project_row_add'),
            path('tables/<slug:project>/<slug:table>/<int:pk>/', view(pages.edit), name='apiary_project_row'),
            path('tables/<slug:project>/<slug:table>/<int:pk>/delete/', view(pages.delete), name='apiary_project_row_delete'),
        ] + super().get_urls()

    TABLE_ROWS = 50

    def tables_view(self, request, project):
        """A project's tables, their columns and first rows, for anyone with a role there."""
        if project not in PROJECT_APPS:
            raise Http404
        if not has_role(request.user, project, 'viewer'):
            raise PermissionDenied
        alias = project_alias(project)
        tables = []
        for name, model in project_models(project).items():
            fields = model._meta.concrete_fields
            rows = model._default_manager.using(alias).order_by('pk')
            tables.append({
                'name': name,
                'label': model._meta.verbose_name,
                # Tables defined in code have their own admin pages.
                'url': f'/admin/{project}/{name}/' if model._meta.apps is apps else
                       reverse('admin:apiary_project_table', args=[project, name]),
                'columns': [{'name': f.name, 'type': changes_type(f), 'required': not f.null} for f in fields],
                'count': rows.count(),
                'rows': [[f.value_from_object(obj) for f in fields] for obj in rows[:self.TABLE_ROWS]],
            })
        name = {label: title for title, label in settings.PROJECTS}.get(project, project)
        heading = f"{name} · Data Models"
        return TemplateResponse(request, 'admin/apiary/tables.html', {
            # No opts: Unfold would head the page with the Changes admin it hangs off.
            **self.admin_site.each_context(request), 'title': heading, 'content_title': heading,
            'project': project, 'tables': tables, 'limit': self.TABLE_ROWS,
            'changes_url': reverse('admin:apiary_change_changelist') + f'?project__exact={project}',
        })


def changes_type(field):
    if field.is_relation:
        return f'link to {field.related_model._meta.model_name}'
    return type(field).__name__.removesuffix('Field').lower() or 'id'

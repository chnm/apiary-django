"""
A live table's own admin page: its rows, searched and paged, for anyone with
a role in the project; editors add and edit rows and admins delete them.
Every write goes through apiary.changes, as an agent's would, so it is checked
against the same roles and recorded under Changes.
"""
from django import forms
from django.apps import apps
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Q
from django.forms import modelform_factory
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from unfold.widgets import (
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminEmailInputWidget,
    UnfoldAdminIntegerFieldWidget,
    UnfoldAdminNullBooleanSelectWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminTextareaWidget,
    UnfoldAdminTextInputWidget,
    UnfoldAdminURLInputWidget,
    UnfoldBooleanSwitchWidget,
)

from apiary import changes
from apiary.projects import PROJECT_APPS, has_role, project_alias, project_models

PAGE_SIZE = 50
VIA = "admin"
SEARCHED = (models.CharField, models.TextField)


def picker(input_type, format=None):
    """The browser's own date and time pickers, styled as Unfold's inputs."""
    widget = forms.DateTimeInput(attrs={"class": UnfoldAdminTextInputWidget().attrs["class"]}, format=format)
    widget.input_type = input_type
    return widget


def widget_for(field):
    if field.is_relation:
        return UnfoldAdminSelectWidget()
    if isinstance(field, models.BooleanField):
        return UnfoldAdminNullBooleanSelectWidget() if field.null else UnfoldBooleanSwitchWidget()
    if isinstance(field, models.DateTimeField):
        return picker("datetime-local", "%Y-%m-%dT%H:%M")
    if isinstance(field, models.DateField):
        return picker("date", "%Y-%m-%d")
    if isinstance(field, models.TimeField):
        return picker("time", "%H:%M")
    for kind, widget in ((models.EmailField, UnfoldAdminEmailInputWidget), (models.URLField, UnfoldAdminURLInputWidget),
                         (models.CharField, UnfoldAdminTextInputWidget), (models.IntegerField, UnfoldAdminIntegerFieldWidget),
                         (models.FloatField, UnfoldAdminDecimalFieldWidget)):
        if isinstance(field, kind):
            return widget()
    return UnfoldAdminTextareaWidget()  # long text, JSON


class RowForm(forms.ModelForm):
    reason = forms.CharField(required=False, label="Why (shown under Changes)", widget=UnfoldAdminTextInputWidget())

    def validate_unique(self):
        pass  # changes.save checks it, inside the change's transaction


def row_form(model, alias):
    def formfield(field, **kwargs):
        kwargs["widget"] = widget_for(field)
        if field.is_relation:
            kwargs["queryset"] = field.related_model._default_manager.using(alias).order_by("pk")
        return field.formfield(**kwargs)

    columns = [f.name for f in model._meta.concrete_fields if not f.primary_key]
    return modelform_factory(model, form=RowForm, fields=columns, formfield_callback=formfield)


def shown(row, field):
    """A value as people read it: linked rows by name, yes/no as words."""
    if field.is_relation:
        return getattr(row, field.name)
    value = field.value_from_object(row)
    return {True: "Yes", False: "No"}.get(value, value) if isinstance(value, bool) else value


def values(form, names):
    """The form's values as a change takes them: plain JSON, linked rows by id."""
    return changes.jsonable({name: getattr(form.cleaned_data[name], "pk", form.cleaned_data[name]) for name in names})


class TablePages:
    def __init__(self, admin_site, project_titles):
        self.admin_site, self.project_titles = admin_site, project_titles

    def found(self, request, project, table):
        """The live table's model, or None for a table defined in code (it has its own admin pages)."""
        if project not in PROJECT_APPS:
            raise Http404
        if not has_role(request.user, project, "viewer"):
            raise PermissionDenied
        model = project_models(project).get(table)
        if model is None:
            raise Http404
        return None if model._meta.apps is apps else model

    def page(self, request, project, model, template, title=None, **context):
        heading = f"{self.project_titles.get(project, project)} · {str(model._meta.verbose_name_plural).capitalize()}"
        if title:
            heading = f"{heading} · {title}"
        # No opts: Unfold would head the page with the Changes admin it hangs off.
        return TemplateResponse(request, f"admin/apiary/{template}", {
            **self.admin_site.each_context(request), "title": heading, "content_title": heading,
            "project": project, "table": model._meta.model_name, **context,
        })

    def url(self, project, table, *rest):
        return reverse("admin:apiary_project_table", args=[project, table]) + "".join(f"{part}/" for part in rest)

    def rows(self, request, project, table):
        model = self.found(request, project, table)
        if model is None:
            return HttpResponseRedirect(f"/admin/{project}/{table}/")
        alias = project_alias(project)
        fields = model._meta.concrete_fields
        relations = [f.name for f in fields if f.is_relation]
        found = model._default_manager.using(alias).select_related(*relations).order_by("pk")
        search = request.GET.get("q", "").strip()
        if search:
            query = Q()
            for field in fields:
                if isinstance(field, SEARCHED):
                    query |= Q(**{f"{field.name}__icontains": search})
            if search.isdigit():
                query |= Q(pk=int(search))
            found = found.filter(query)
        page = Paginator(found, PAGE_SIZE).get_page(request.GET.get("page"))
        editor = has_role(request.user, project, "editor")
        return self.page(request, project, model, "table.html", page=page, search=search, editor=editor,
                         columns=[{"name": f.name, "label": str(f.verbose_name).capitalize(), "required": not f.null}
                                  for f in fields],
                         body=[(obj.pk, [shown(obj, f) for f in fields]) for obj in page],
                         add_url=self.url(project, table, "add"), changes_url=reverse("admin:apiary_change_changelist") + f"?project__exact={project}")

    def edit(self, request, project, table, pk=None):
        model = self.found(request, project, table)
        if model is None:
            raise Http404
        if not has_role(request.user, project, "editor"):
            raise PermissionDenied
        alias = project_alias(project)
        row = None
        if pk is not None:
            row = model._default_manager.using(alias).filter(pk=pk).first()
            if row is None:
                raise Http404
        form = row_form(model, alias)(request.POST or None, instance=row)
        if request.method == "POST" and form.is_valid():
            reason = form.cleaned_data["reason"]
            try:
                if row is None:
                    change = changes.create_records(request.user, project, table, [values(form, form._meta.fields)],
                                                    via=VIA, reason=reason)
                else:
                    edited = [name for name in form.changed_data if name != "reason"]
                    if not edited:
                        messages.info(request, "Nothing changed.")
                        return HttpResponseRedirect(self.url(project, table))
                    change = changes.change_records(request.user, project, table, "update", ids=[row.pk],
                                                    values=values(form, edited), via=VIA, reason=reason)
            except changes.ChangeError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, f"Saved as change #{change.pk}.")
                return HttpResponseRedirect(self.url(project, table))
        admin = has_role(request.user, project, "admin")
        return self.page(request, project, model, "row_form.html", "New row" if row is None else f"Row {row.pk}",
                         form=form, row=row, back_url=self.url(project, table),
                         delete_url=self.url(project, table, row.pk, "delete") if row is not None and admin else None)

    def delete(self, request, project, table, pk):
        model = self.found(request, project, table)
        if model is None:
            raise Http404
        if not has_role(request.user, project, "admin"):
            raise PermissionDenied
        row = model._default_manager.using(project_alias(project)).filter(pk=pk).first()
        if row is None:
            raise Http404
        if request.method == "POST":
            try:
                change = changes.change_records(request.user, project, table, "delete", ids=[pk], via=VIA,
                                                reason=request.POST.get("reason", ""))
            except changes.ChangeError as error:
                messages.error(request, str(error))
                return HttpResponseRedirect(self.url(project, table, pk))
            messages.success(request, f"Deleted as change #{change.pk}; it can be reverted from there.")
            return HttpResponseRedirect(self.url(project, table))
        return self.page(request, project, model, "row_delete.html", f"Delete row {pk}", row=row,
                         back_url=self.url(project, table, pk))

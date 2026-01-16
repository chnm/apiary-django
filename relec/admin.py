from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Denomination, Schedule


@admin.register(Denomination)
class DenominationAdmin(ModelAdmin):
    list_display = ['name', 'family']
    list_filter = ['family']
    search_fields = ['name', 'family']
    ordering = ['family', 'name']


@admin.register(Schedule)
class ScheduleAdmin(ModelAdmin):
    list_display = ['title', 'box', 'status', 'transcriber', 'reviewer']
    list_filter = ['status', 'box']
    search_fields = ['title', 'box', 'transcriber', 'reviewer']
    ordering = ['box', 'title']

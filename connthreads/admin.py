from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Textile


@admin.register(Textile)
class TextileAdmin(ModelAdmin):
    list_display = ['year', 'type', 'subtype', 'circulation']
    list_filter = ['year', 'type', 'subtype']
    search_fields = ['type', 'subtype']
    ordering = ['-year', 'type']

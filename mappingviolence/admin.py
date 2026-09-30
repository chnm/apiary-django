from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Witness


@admin.register(Witness)
class WitnessAdmin(ModelAdmin):
    list_display = ['name', 'testimony_date', 'crime']
    list_filter = ['testimony_date', 'crime']
    search_fields = ['name', 'crime', 'claim', 'notes']
    ordering = ['-testimony_date', 'name']
    date_hierarchy = 'testimony_date'

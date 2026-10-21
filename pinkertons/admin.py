from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Activity, Location


@admin.register(Activity)
class ActivityAdmin(ModelAdmin):
    list_display = ['date', 'investigation', 'operative', 'activity', 'subject', 'location']
    list_filter = ['investigation', 'activity']
    search_fields = ['operative', 'subject', 'notes']
    ordering = ['date', 'time']


@admin.register(Location)
class LocationAdmin(ModelAdmin):
    list_display = ['location_name', 'street_address', 'locality', 'location_type']
    list_filter = ['location_type', 'locality']
    search_fields = ['location_name', 'street_address', 'locality']

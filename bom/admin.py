from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import MortalityBill


@admin.register(MortalityBill)
class MortalityBillAdmin(ModelAdmin):
    list_display = ['year', 'type', 'count']
    list_filter = ['year', 'type']
    search_fields = ['type']
    ordering = ['-year', 'type']

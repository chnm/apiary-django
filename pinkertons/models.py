from django.db import models
from apiary.models import BaseModel


class Location(BaseModel):
    locality = models.CharField(max_length=200, blank=True)
    street_address = models.CharField(max_length=200, blank=True)
    location_name = models.CharField(max_length=200, blank=True)
    location_type = models.CharField(max_length=100, blank=True)
    specific_location_type = models.CharField(max_length=100, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    def __str__(self):
        return self.location_name or self.street_address or self.locality


class Activity(BaseModel):
    investigation = models.CharField(max_length=50)
    date = models.DateField(null=True, blank=True)
    time = models.TimeField(null=True, blank=True)
    operative = models.CharField(max_length=200, blank=True)
    activity = models.CharField(max_length=100, blank=True)
    mode = models.CharField(max_length=100, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name_plural = "Activities"

    def __str__(self):
        return f"{self.date} - {self.operative}: {self.activity}"

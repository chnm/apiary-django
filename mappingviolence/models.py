from django.db import models
from apiary.models import BaseModel


class Witness(BaseModel):
    name = models.CharField(max_length=200)
    testimony_date = models.DateField()
    crime = models.CharField(max_length=200)
    claim = models.TextField()
    notes = models.TextField(blank=True)

    class Meta:
        db_table_comment = "Witness testimony records for Mapping Violence project"
        verbose_name_plural = "Witnesses"

    def __str__(self):
        return f"{self.name} - {self.testimony_date}"

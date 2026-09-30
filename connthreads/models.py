from django.db import models
from apiary.models import BaseModel


class Textile(BaseModel):
    year = models.IntegerField()
    type = models.CharField(max_length=100)
    subtype = models.CharField(max_length=100)
    circulation = models.IntegerField()

    class Meta:
        db_table_comment = "Textile records for Connecting Threads project"

    def __str__(self):
        return f"{self.year} - {self.type} ({self.subtype})"

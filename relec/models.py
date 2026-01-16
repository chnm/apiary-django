from django.db import models
from apiary.models import BaseModel


class Denomination(BaseModel):
    name = models.CharField(max_length=200)
    family = models.CharField(max_length=200)

    class Meta:
        db_table_comment = "Demonination records for Religious Ecologies project"

    def __str__(self):
        return self.name


class Schedule(BaseModel):
    title = models.CharField(max_length=200)
    box = models.CharField(max_length=100)
    status = models.CharField(max_length=50)
    transcriber = models.CharField(max_length=200, blank=True)
    reviewer = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table_comment = "Schedule records for Religious Ecologies project"

    def __str__(self):
        return f"{self.title} (Box {self.box})"

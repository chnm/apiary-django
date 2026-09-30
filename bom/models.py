from django.db import models
from apiary.models import BaseModel

class MortalityBill(BaseModel):
    year = models.IntegerField()
    type = models.CharField(max_length=100)
    count = models.IntegerField()

    def __str__(self):
        return f"{self.year} - {self.type}: {self.count}"

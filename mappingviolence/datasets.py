from datetime import date

from django.db.models import Count
from django.db.models.functions import TruncMonth
from ninja import Router, Schema

from .models import Witness

router = Router()


class MonthlyTestimonies(Schema):
    month: date
    crime: str
    testimonies: int


@router.get('/testimonies-by-month/', response=list[MonthlyTestimonies])
def testimonies_by_month(request, crime: str | None = None):
    """Witness testimonies per month and crime: a time series."""
    witnesses = Witness.objects.all()
    if crime:
        witnesses = witnesses.filter(crime=crime)
    return (
        witnesses.annotate(month=TruncMonth('testimony_date'))
        .values('month', 'crime')
        .annotate(testimonies=Count('pk'))
        .order_by('month', 'crime')
    )

from django.db.models import Sum
from ninja import Router, Schema

from .models import MortalityBill

router = Router()


class YearlyDeaths(Schema):
    year: int
    type: str
    deaths: int


@router.get('/deaths-by-year/', response=list[YearlyDeaths])
def deaths_by_year(request, type: str | None = None, start: int | None = None, end: int | None = None):
    """Deaths per year and cause: a time series."""
    bills = MortalityBill.objects.all()
    if type:
        bills = bills.filter(type=type)
    if start is not None:
        bills = bills.filter(year__gte=start)
    if end is not None:
        bills = bills.filter(year__lte=end)
    return bills.values('year', 'type').annotate(deaths=Sum('count')).order_by('year', 'type')

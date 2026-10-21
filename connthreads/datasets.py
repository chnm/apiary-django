from django.db.models import Sum
from ninja import Router, Schema

from .models import Textile

router = Router()


class YearlyCirculation(Schema):
    year: int
    type: str
    circulation: int


@router.get('/circulation-by-year/', response=list[YearlyCirculation])
def circulation_by_year(request, type: str | None = None):
    """Textile circulation per year and type: a time series."""
    textiles = Textile.objects.all()
    if type:
        textiles = textiles.filter(type=type)
    return textiles.values('year', 'type').annotate(circulation=Sum('circulation')).order_by('year', 'type')

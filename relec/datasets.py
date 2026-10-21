from django.db.models import Count
from ninja import Router, Schema

from .models import Denomination, Schedule

router = Router()


class FamilyCount(Schema):
    family: str
    denominations: int


class StatusCount(Schema):
    status: str
    schedules: int


@router.get('/denominations-by-family/', response=list[FamilyCount])
def denominations_by_family(request):
    """Denominations per family: an aggregate."""
    return (
        Denomination.objects.values('family')
        .annotate(denominations=Count('pk'))
        .order_by('-denominations', 'family')
    )


@router.get('/schedules-by-status/', response=list[StatusCount])
def schedules_by_status(request):
    """Transcription progress: schedules per status."""
    return Schedule.objects.values('status').annotate(schedules=Count('pk')).order_by('status')

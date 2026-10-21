from datetime import date

from django.db.models import Count
from django.db.models.functions import TruncMonth
from ninja import Router, Schema

from .models import Activity

router = Router()


class MonthlyActivities(Schema):
    month: date
    investigation: str
    activities: int


class ActivityPoint(Schema):
    id: int
    date: date | None
    investigation: str
    activity: str
    operative: str
    location: str
    latitude: float
    longitude: float


@router.get('/activities-by-month/', response=list[MonthlyActivities])
def activities_by_month(request, investigation: str | None = None):
    """Operative activities per month and investigation: a time series."""
    activities = Activity.objects.filter(date__isnull=False)
    if investigation:
        activities = activities.filter(investigation=investigation)
    return (
        activities.annotate(month=TruncMonth('date'))
        .values('month', 'investigation')
        .annotate(activities=Count('pk'))
        .order_by('month', 'investigation')
    )


@router.get('/activity-points/', response=list[ActivityPoint])
def activity_points(request, investigation: str | None = None):
    """Activities joined to their geocoded locations, for maps."""
    activities = Activity.objects.filter(location__latitude__isnull=False, location__longitude__isnull=False)
    if investigation:
        activities = activities.filter(investigation=investigation)
    return [
        {
            'id': a.pk, 'date': a.date, 'investigation': a.investigation,
            'activity': a.activity, 'operative': a.operative, 'location': str(a.location),
            'latitude': a.location.latitude, 'longitude': a.location.longitude,
        }
        for a in activities.select_related('location').order_by('date', 'pk')
    ]

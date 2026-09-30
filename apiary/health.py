from django.db import DatabaseError, connections
from django.http import JsonResponse


def health(request):
    try:
        for alias in connections:
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "database unavailable"}, status=503)
    return JsonResponse({"status": "ok"})

from django.db import connection
from django.http import HttpResponse, HttpResponseServerError


def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return HttpResponse("ok")
    except Exception:
        return HttpResponseServerError("db error")

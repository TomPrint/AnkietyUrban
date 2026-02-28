from django.http import HttpRequest, HttpResponseNotFound
from django.shortcuts import render


def custom_404(request: HttpRequest, exception) -> HttpResponseNotFound:
    return render(request, "404.html", status=404)

from django.urls import path
from django.views.generic import RedirectView

from .views import scraper_home

urlpatterns = [
    path("scraper/", scraper_home, name="scraper-home"),
    path("portal/scraper/", RedirectView.as_view(pattern_name="scraper-home", permanent=False)),
]


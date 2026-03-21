from django.urls import path

from .views import portal_home, portal_scraper

urlpatterns = [
    path("portal/", portal_home, name="portal-home"),
    path("portal/scraper/", portal_scraper, name="portal-scraper"),
]


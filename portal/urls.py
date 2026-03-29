from django.urls import path

from .views import portal_home

urlpatterns = [
    path("portal/", portal_home, name="portal-home"),
]

from django.contrib.auth import views as auth_views
from django.urls import path

from accounts.views import StaffLoginView, portal_home, portal_scraper
from surveys.views import management_dashboard

urlpatterns = [
    path("", StaffLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("portal/", portal_home, name="portal-home"),
    path("portal/scraper/", portal_scraper, name="portal-scraper"),
    path("management/", management_dashboard, name="management-dashboard"),
]

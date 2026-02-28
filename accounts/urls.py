from django.contrib.auth import views as auth_views
from django.urls import path

from surveys.views import management_dashboard

urlpatterns = [
    path("", auth_views.LoginView.as_view(template_name="management/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("management/", management_dashboard, name="management-dashboard"),
]

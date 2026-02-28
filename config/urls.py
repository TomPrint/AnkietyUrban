"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Internal IT Admin"
admin.site.site_title = "IT Admin"
admin.site.index_title = "Internal Control Panel"
admin.site.has_permission = lambda request: bool(
    request.user.is_active and request.user.is_superuser
)

urlpatterns = [
    path("", include("accounts.urls")),
    path("", include("surveys.urls")),
    path("it-secret-admin-portal-uv/", admin.site.urls),
]

handler404 = "config.views.custom_404"

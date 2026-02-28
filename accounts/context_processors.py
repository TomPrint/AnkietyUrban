from django.conf import settings


def app_settings(request):
    return {
        "IDLE_LOGOUT_SECONDS": getattr(settings, "IDLE_LOGOUT_SECONDS", 900),
    }

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone


LAST_ACTIVITY_SESSION_KEY = "last_activity_ts"
EXEMPT_URL_NAMES = {"login", "logout"}


class IdleLogoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_logout(request):
            logout(request)
            request.session.pop(LAST_ACTIVITY_SESSION_KEY, None)
            messages.warning(request, "Sesja wygasła z powodu bezczynności.")
            return redirect(settings.LOGIN_URL)

        response = self.get_response(request)

        if self._should_track_activity(request):
            request.session[LAST_ACTIVITY_SESSION_KEY] = int(timezone.now().timestamp())

        return response

    def _should_logout(self, request):
        if not self._should_track_activity(request):
            return False

        last_activity = request.session.get(LAST_ACTIVITY_SESSION_KEY)
        if not last_activity:
            return False

        idle_seconds = int(timezone.now().timestamp()) - int(last_activity)
        return idle_seconds >= int(getattr(settings, "IDLE_LOGOUT_SECONDS", 900))

    def _should_track_activity(self, request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        resolver_match = getattr(request, "resolver_match", None)
        url_name = resolver_match.url_name if resolver_match else ""
        if url_name in EXEMPT_URL_NAMES:
            return False

        return True

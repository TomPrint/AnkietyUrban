from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.middleware import LAST_ACTIVITY_SESSION_KEY


@override_settings(IDLE_LOGOUT_SECONDS=900)
class IdleLogoutMiddlewareTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="staff",
            password="secret123",
            is_staff=True,
        )

    def test_logs_out_when_idle_timeout_is_exceeded(self):
        self.client.force_login(self.user)
        session = self.client.session
        session[LAST_ACTIVITY_SESSION_KEY] = int(timezone.now().timestamp()) - 901
        session.save()

        response = self.client.get(reverse("portal-home"))

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_keeps_user_logged_in_and_refreshes_activity_when_not_timed_out(self):
        self.client.force_login(self.user)
        old_timestamp = int(timezone.now().timestamp()) - 30
        session = self.client.session
        session[LAST_ACTIVITY_SESSION_KEY] = old_timestamp
        session.save()

        response = self.client.get(reverse("portal-home"))

        self.assertEqual(response.status_code, 200)
        refreshed_session = self.client.session
        self.assertIn(LAST_ACTIVITY_SESSION_KEY, refreshed_session)
        self.assertGreaterEqual(int(refreshed_session[LAST_ACTIVITY_SESSION_KEY]), old_timestamp)

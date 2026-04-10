import uuid

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.middleware import LAST_ACTIVITY_SESSION_KEY
from crm.models import Customer
from scraper.models import LeadCandidate


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


class RouteAccessTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(company_name="Test Customer")
        self.candidate = LeadCandidate.objects.create(
            company_name="Lead Testowy",
            normalized_name="LEAD TESTOWY",
        )
        self.user = User.objects.create_user(
            username="managed-user",
            password="secret123",
            is_staff=True,
        )

    def test_anonymous_user_is_redirected_from_staff_pages(self):
        protected_urls = [
            reverse("portal-home"),
            reverse("portal-users"),
            reverse("portal-user-create"),
            reverse("portal-customers"),
            reverse("portal-customer-create"),
            reverse("management-dashboard"),
            reverse("management-assignments"),
            reverse("management-questions"),
            reverse("management-templates"),
            reverse("scraper-home"),
            reverse("scraper-gemini-import"),
            reverse("scraper-candidates"),
            reverse("portal-customer-detail", args=[self.customer.id]),
            reverse("portal-user-edit", args=[self.user.id]),
        ]

        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response.url)

    def test_anonymous_user_is_redirected_from_staff_post_actions(self):
        protected_post_urls = [
            reverse("portal-customer-delete", args=[self.customer.id]),
            reverse("portal-user-delete", args=[self.user.id]),
            reverse("scraper-candidate-approve", args=[self.candidate.id]),
            reverse("scraper-candidate-reject", args=[self.candidate.id]),
            reverse("scraper-candidate-reopen", args=[self.candidate.id]),
            reverse("scraper-candidate-delete", args=[self.candidate.id]),
        ]

        for url in protected_post_urls:
            with self.subTest(url=url):
                response = self.client.post(url, {"reason": "test"})
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response.url)

    def test_public_survey_thanks_page_is_available_without_login(self):
        response = self.client.get(reverse("survey-thanks", args=[uuid.uuid4()]))

        self.assertNotEqual(response.status_code, 302)

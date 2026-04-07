import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.forms import CustomerForm
from crm.models import Customer

from .gemini import _extract_json_text, _format_api_error
from .tavily import _format_api_error as format_tavily_api_error
from .models import LeadCandidate
from .services import import_gemini_candidates, import_tavily_candidates, normalize_company_name


class ScraperLeadCandidateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="reviewer",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_normalize_company_name_removes_case_and_polish_differences(self):
        self.assertEqual(
            normalize_company_name("SpĂłĹ‚dzielnia  Mieszkaniowa Ĺ»oliborz"),
            "SPOLDZIELNIA MIESZKANIOWA ZOLIBORZ",
        )

    def test_extract_json_text_handles_code_fences(self):
        wrapped = '```json\n{"candidates": [], "notes": "ok"}\n```'
        self.assertEqual(_extract_json_text(wrapped), '{"candidates": [], "notes": "ok"}')

    def test_format_api_error_for_quota_is_user_friendly(self):
        message = _format_api_error(Exception("429 RESOURCE_EXHAUSTED. Please retry in 23.1s"))
        self.assertIn("24 godziny", message)
        self.assertIn("23.1", message)
        self.assertIn("p\u0142atn\u0105 wersj\u0119", message)

    def test_format_tavily_api_error_for_monthly_quota_is_user_friendly(self):
        message = format_tavily_api_error(Exception("429 quota exceeded"))
        self.assertIn("na ten miesi\u0105c", message)
        self.assertIn("nast\u0119pnego miesi\u0105ca", message)
        self.assertIn("p\u0142atny plan", message)

    def test_customer_form_accepts_optional_district_and_website(self):
        form = CustomerForm(
            data={
                "company_name": "SpĂłĹ‚dzielnia Mieszkaniowa Test",
                "district": "MokotĂłw",
                "address": "ul. Testowa 4, Warszawa",
                "website": "https://test.example.pl",
                "contact_person": "",
                "email": "",
                "telephone": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    @patch("scraper.views.generate_candidates_payload")
    def test_gemini_generate_imports_candidates(self, mocked_generate):
        mocked_generate.return_value = {
            "payload": json.dumps(
                [
                    {
                        "nazwa": "SpĂłĹ‚dzielnia Mieszkaniowa Orion",
                        "dzielnica": "UrsynĂłw",
                        "adres": "ul. PrzykĹ‚adowa 10, 02-001 Warszawa",
                        "email": "kontakt@orion.example.pl",
                        "telefon": "+48 22 123 45 67",
                        "powod": "Oficjalna strona wskazuje na spĂłĹ‚dzielniÄ™ mieszkaniowÄ….",
                        "strona_www": "https://orion.example.pl",
                        "confidence": 0.91,
                    }
                ],
                ensure_ascii=False,
            ),
            "candidates": [],
            "notes": "",
            "model": "gemini-2.5-flash-lite",
            "raw_response": {},
        }

        response = self.client.post(
            reverse("scraper-gemini-generate"),
            {
                "search_goal": "ZnajdĹş potencjalne spĂłĹ‚dzielnie mieszkaniowe",
                "city": "Warszawa",
                "district": "UrsynĂłw",
                "max_candidates": 5,
                "use_google_search": "on",
                "extra_instructions": "",
            },
        )

        self.assertRedirects(response, reverse("scraper-candidates"))
        lead = LeadCandidate.objects.get(source="gemini")
        self.assertEqual(lead.district, "UrsynĂłw")
        self.assertEqual(lead.address, "ul. PrzykĹ‚adowa 10, 02-001 Warszawa")
        self.assertEqual(lead.email, "kontakt@orion.example.pl")
        self.assertEqual(lead.telephone, "+48 22 123 45 67")
        self.assertEqual(lead.website, "https://orion.example.pl")
        mocked_generate.assert_called_once()

    @patch("scraper.views.search_tavily_candidates")
    def test_tavily_generate_imports_candidates(self, mocked_search):
        mocked_search.return_value = {
            "payload": json.dumps(
                [
                    {
                        "nazwa": "SpĂłĹ‚dzielnia Mieszkaniowa Nova",
                        "dzielnica": "Bemowo",
                        "adres": "ul. Nova 5, 01-234 Warszawa",
                        "email": "biuro@nova.pl",
                        "telefon": "22 123 45 67",
                        "powod": "Wynik Tavily z oficjalnej strony kontaktowej.",
                        "strona_www": "https://nova.pl",
                        "confidence": 0.87,
                    }
                ],
                ensure_ascii=False,
            ),
            "candidates": [],
            "query": "spoldzielnie mieszkaniowe Warszawa Bemowo",
            "response_time": 0.5,
            "request_id": "req_123",
            "raw_response": {},
        }

        response = self.client.post(
            reverse("scraper-tavily-generate"),
            {
                "search_goal": "spoldzielnie mieszkaniowe",
                "city": "Warszawa",
                "district": "Bemowo",
                "max_candidates": 5,
                "search_depth": "advanced",
                "extra_instructions": "",
            },
        )

        self.assertRedirects(response, reverse("scraper-candidates"))
        lead = LeadCandidate.objects.get(source="tavily")
        self.assertEqual(lead.company_name, "SpĂłĹ‚dzielnia Mieszkaniowa Nova")
        self.assertEqual(lead.website, "https://nova.pl")
        mocked_search.assert_called_once()

    def test_import_marks_duplicate_customer_and_candidate_across_sources(self):
        Customer.objects.create(company_name="Spoldzielnia Mieszkaniowa Alfa")
        gemini_payload = json.dumps(
            [
                {
                    "nazwa": "SpĂłĹ‚dzielnia Mieszkaniowa Alfa",
                    "dzielnica": "MokotĂłw",
                    "adres": "ul. Testowa 1, Warszawa",
                    "email": "biuro@alfa.pl",
                    "telefon": "22 000 11 22",
                    "powĂłd": "Nazwa wskazuje na spĂłĹ‚dzielniÄ™.",
                    "confidence": 0.9,
                }
            ],
            ensure_ascii=False,
        )
        tavily_payload = json.dumps(
            [
                {
                    "nazwa": "SpĂłĹ‚dzielnia Mieszkaniowa Alfa",
                    "dzielnica": "MokotĂłw",
                    "adres": "ul. Testowa 1, Warszawa",
                    "email": "biuro@alfa.pl",
                    "telefon": "22 000 11 22",
                    "powod": "Powtorzenie z Tavily.",
                    "strona_www": "https://alfa.pl",
                    "confidence": 0.8,
                }
            ],
            ensure_ascii=False,
        )

        import_gemini_candidates(gemini_payload)
        import_tavily_candidates(tavily_payload)

        leads = list(LeadCandidate.objects.order_by("id"))
        self.assertEqual(len(leads), 2)
        self.assertEqual(leads[0].source, "gemini")
        self.assertEqual(leads[1].source, "tavily")
        self.assertIsNotNone(leads[0].duplicate_customer)
        self.assertEqual(leads[1].duplicate_candidate_id, leads[0].id)

    def test_approving_candidate_creates_customer_with_contact_fields_and_website(self):
        lead = LeadCandidate.objects.create(
            source="tavily",
            company_name="SpĂłĹ‚dzielnia Mieszkaniowa Beta",
            normalized_name="SPOLDZIELNIA MIESZKANIOWA BETA",
            district="Wola",
            address="ul. Beta 12, Warszawa",
            website="https://beta.pl",
            email="kontakt@beta.pl",
            telephone="22 700 80 90",
            reason="Pasuje do wzorca.",
        )

        response = self.client.post(reverse("scraper-candidate-approve", args=[lead.id]))

        self.assertRedirects(response, reverse("scraper-candidates"))
        lead.refresh_from_db()
        customer = lead.approved_customer
        self.assertEqual(lead.status, LeadCandidate.STATUS_APPROVED)
        self.assertIsNotNone(customer)
        self.assertEqual(customer.district, "Wola")
        self.assertEqual(customer.address, "ul. Beta 12, Warszawa")
        self.assertEqual(customer.website, "https://beta.pl")
        self.assertEqual(customer.email, "kontakt@beta.pl")
        self.assertEqual(customer.telephone, "22 700 80 90")

    def test_approving_duplicate_candidate_reuses_existing_customer_and_fills_missing_data(self):
        customer = Customer.objects.create(company_name="SpĂłĹ‚dzielnia Mieszkaniowa Gamma")
        lead = LeadCandidate.objects.create(
            source="gemini",
            company_name="SpĂłĹ‚dzielnia Mieszkaniowa Gamma",
            normalized_name="SPOLDZIELNIA MIESZKANIOWA GAMMA",
            duplicate_customer=customer,
            district="ĹšrĂłdmieĹ›cie",
            address="ul. Gamma 3, Warszawa",
            website="https://gamma.pl",
            email="biuro@gamma.pl",
            telephone="22 999 88 77",
        )

        self.client.post(reverse("scraper-candidate-approve", args=[lead.id]))

        lead.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual(lead.approved_customer_id, customer.id)
        self.assertEqual(customer.district, "ĹšrĂłdmieĹ›cie")
        self.assertEqual(customer.address, "ul. Gamma 3, Warszawa")
        self.assertEqual(customer.website, "https://gamma.pl")
        self.assertEqual(customer.email, "biuro@gamma.pl")
        self.assertEqual(customer.telephone, "22 999 88 77")
        self.assertEqual(Customer.objects.filter(company_name="SpĂłĹ‚dzielnia Mieszkaniowa Gamma").count(), 1)

    def test_reject_candidate_marks_status(self):
        lead = LeadCandidate.objects.create(
            company_name="SpĂłĹ‚dzielnia Mieszkaniowa Delta",
            normalized_name="SPOLDZIELNIA MIESZKANIOWA DELTA",
        )

        response = self.client.post(
            reverse("scraper-candidate-reject", args=[lead.id]),
            {"reason": "To nie jest spĂłĹ‚dzielnia mieszkaniowa."},
        )

        self.assertRedirects(response, reverse("scraper-candidates"))
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadCandidate.STATUS_REJECTED)
        self.assertEqual(lead.rejection_reason, "To nie jest spĂłĹ‚dzielnia mieszkaniowa.")


    def test_reopen_rejected_candidate_sets_pending_status(self):
        lead = LeadCandidate.objects.create(
            company_name="SpĂłĹ‚dzielnia Mieszkaniowa Reopen",
            normalized_name="SPOLDZIELNIA MIESZKANIOWA REOPEN",
            status=LeadCandidate.STATUS_REJECTED,
            rejection_reason="Bledny rekord",
            reviewed_by=self.user,
            reviewed_at="2026-01-01T10:00:00Z",
        )

        response = self.client.post(reverse("scraper-candidate-reopen", args=[lead.id]))

        self.assertRedirects(response, reverse("scraper-candidates"))
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadCandidate.STATUS_PENDING)
        self.assertEqual(lead.rejection_reason, "")
        self.assertIsNone(lead.reviewed_by)
        self.assertIsNone(lead.reviewed_at)

    def test_delete_rejected_candidate_removes_record(self):
        lead = LeadCandidate.objects.create(
            company_name="SpĂłĹ‚dzielnia Mieszkaniowa Delete",
            normalized_name="SPOLDZIELNIA MIESZKANIOWA DELETE",
            status=LeadCandidate.STATUS_REJECTED,
        )

        response = self.client.post(reverse("scraper-candidate-delete", args=[lead.id]))

        self.assertRedirects(response, reverse("scraper-candidates"))
        self.assertFalse(LeadCandidate.objects.filter(id=lead.id).exists())

    def test_candidate_list_all_supports_case_insensitive_search_filters_and_sorting(self):
        first = LeadCandidate.objects.create(
            source="gemini",
            company_name="CHOMIK Alfa",
            normalized_name="CHOMIK ALFA",
            status=LeadCandidate.STATUS_PENDING,
            email="alfa@example.com",
        )
        second = LeadCandidate.objects.create(
            source="tavily",
            company_name="Chomik Beta",
            normalized_name="CHOMIK BETA",
            status=LeadCandidate.STATUS_REJECTED,
            duplicate_candidate=first,
            email="beta@example.com",
        )

        response = self.client.get(
            reverse("scraper-candidates"),
            {"status": "all", "q": "chomik", "duplicate": "yes", "sort": "company_name", "dir": "desc"},
        )

        self.assertEqual(response.status_code, 200)
        names = [candidate.company_name for candidate in response.context["page_obj"].object_list]
        self.assertEqual(names, ["Chomik Beta"])
        self.assertEqual(response.context["header_columns"][0]["key"], "company_name")




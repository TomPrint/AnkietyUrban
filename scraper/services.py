import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from crm.models import Customer

from .models import LeadCandidate

POLISH_TRANSLATION = str.maketrans(
    {
        "\u0105": "a",
        "\u0107": "c",
        "\u0119": "e",
        "\u0142": "l",
        "\u0144": "n",
        "\u00f3": "o",
        "\u015b": "s",
        "\u017a": "z",
        "\u017c": "z",
        "\u0104": "A",
        "\u0106": "C",
        "\u0118": "E",
        "\u0141": "L",
        "\u0143": "N",
        "\u00d3": "O",
        "\u015a": "S",
        "\u0179": "Z",
        "\u017b": "Z",
    }
)
MOJIBAKE_MARKERS = ("\u00c3", "\u00c4", "\u00c5", "\u0102", "\u0104", "\u0139", "\u00e2")


def _repair_mojibake(value: str) -> str:
    if not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value

    for encoding in ("cp1250", "cp1252", "latin1"):
        try:
            repaired = value.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired and repaired != value:
            return repaired
    return value


def normalize_company_name(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    value = _repair_mojibake(unicodedata.normalize("NFKC", value))
    value = value.translate(POLISH_TRANSLATION)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.upper()
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _first_present(data, keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def _parse_confidence(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_candidate_payload(payload_text: str):
    raw = json.loads(payload_text)
    if isinstance(raw, dict):
        raw = raw.get("candidates") or raw.get("items") or raw.get("results") or []
    if not isinstance(raw, list):
        raise ValueError("JSON musi zawierac liste kandydatow.")

    parsed_candidates = []
    skipped = 0
    for item in raw:
        if not isinstance(item, dict):
            skipped += 1
            continue

        company_name = str(_first_present(item, ["nazwa", "name", "company_name", "title"])).strip()
        if not company_name:
            skipped += 1
            continue

        candidate = {
            "company_name": company_name,
            "district": str(_first_present(item, ["dzielnica", "district"])).strip(),
            "address": str(_first_present(item, ["adres", "address"])).strip(),
            "email": str(_first_present(item, ["email", "adres_email", "e_mail"])).strip(),
            "telephone": str(_first_present(item, ["telefon", "phone", "telephone"])).strip(),
            "reason": str(_first_present(item, ["powod", "pow\u00f3d", "reason"])).strip(),
            "website": str(_first_present(item, ["strona_www", "stronaWWW", "website", "url"])).strip(),
            "confidence": _parse_confidence(_first_present(item, ["confidence", "score"])),
            "raw_payload": item,
        }
        parsed_candidates.append(candidate)

    return parsed_candidates, skipped


def import_candidates(payload_text: str, source: str):
    candidates, skipped = parse_candidate_payload(payload_text)

    existing_customers = {
        normalize_company_name(customer.company_name): customer
        for customer in Customer.objects.filter(is_archived=False)
    }
    existing_candidates = {
        candidate.normalized_name: candidate
        for candidate in LeadCandidate.objects.all().order_by("created_at", "pk")
    }

    created = []
    for item in candidates:
        normalized_name = normalize_company_name(item["company_name"])
        duplicate_customer = existing_customers.get(normalized_name)
        duplicate_candidate = existing_candidates.get(normalized_name)

        lead = LeadCandidate.objects.create(
            source=source,
            company_name=item["company_name"],
            normalized_name=normalized_name,
            district=item["district"],
            address=item["address"],
            email=item["email"],
            telephone=item["telephone"],
            reason=item["reason"],
            website=item["website"],
            confidence=item["confidence"],
            raw_payload=item["raw_payload"],
            duplicate_customer=duplicate_customer,
            duplicate_candidate=duplicate_candidate,
        )
        existing_candidates.setdefault(normalized_name, lead)
        created.append(lead)

    return {
        "created_count": len(created),
        "skipped_count": skipped,
        "duplicates_count": sum(1 for lead in created if lead.has_duplicate),
        "created_ids": [lead.pk for lead in created],
    }


def import_gemini_candidates(payload_text: str):
    return import_candidates(payload_text, source="gemini")


def import_tavily_candidates(payload_text: str):
    return import_candidates(payload_text, source="tavily")


def approve_candidate(candidate: LeadCandidate, user):
    customer = candidate.duplicate_customer
    if customer is None:
        customer = Customer.objects.create(
            company_name=candidate.company_name,
            district=candidate.district,
            address=candidate.address,
            website=candidate.website,
            email=candidate.email,
            telephone=candidate.telephone,
        )
    else:
        updated_fields = []
        if candidate.district and not customer.district:
            customer.district = candidate.district
            updated_fields.append("district")
        if candidate.address and not customer.address:
            customer.address = candidate.address
            updated_fields.append("address")
        if candidate.website and not customer.website:
            customer.website = candidate.website
            updated_fields.append("website")
        if candidate.email and not customer.email:
            customer.email = candidate.email
            updated_fields.append("email")
        if candidate.telephone and not customer.telephone:
            customer.telephone = candidate.telephone
            updated_fields.append("telephone")
        if updated_fields:
            customer.save(update_fields=updated_fields)

    candidate.status = LeadCandidate.STATUS_APPROVED
    candidate.approved_customer = customer
    candidate.reviewed_by = user
    candidate.reviewed_at = timezone.now()
    candidate.rejection_reason = ""
    candidate.save(
        update_fields=[
            "status",
            "approved_customer",
            "reviewed_by",
            "reviewed_at",
            "rejection_reason",
            "updated_at",
        ]
    )
    return customer

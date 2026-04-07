import json
import os
import re
from urllib import error, request


class TavilyCandidateGenerationError(RuntimeError):
    pass


DEFAULT_SEARCH_DEPTH = os.getenv("TAVILY_SEARCH_DEPTH", "advanced")
SEARCH_ENDPOINT = "https://api.tavily.com/search"
EMAIL_RE = re.compile(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?48[\s-]?)?(?:\(?\d{2}\)?[\s-]?)?\d{3}[\s-]?\d{2,3}[\s-]?\d{2,3}")
ADDRESS_PATTERNS = [
    re.compile(r"((?:ul\.|ulica|al\.|aleja|pl\.|plac|os\.|osiedle)\s+[^\n,]{3,120}(?:,\s*\d{2}-\d{3}\s+[^\n,]{2,80})?)", re.IGNORECASE),
    re.compile(r"(\d{2}-\d{3}\s+[^\n,]{2,80},\s*(?:ul\.|ulica|al\.|aleja|pl\.|plac|os\.|osiedle)\s+[^\n,]{3,120})", re.IGNORECASE),
]


def _clean_company_name(title: str, url: str) -> str:
    title = (title or "").strip()
    if not title:
        return ""
    cleaned = re.split(r"\s+(?:\-|\u2013|\u2014|\||:)\s+", title, maxsplit=1)[0].strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) < 4:
        return title
    domain = re.sub(r"^https?://", "", (url or "")).split("/")[0]
    if cleaned.lower() == domain.lower():
        return title
    return cleaned


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 9:
        return ""
    if digits.startswith("48") and len(digits) >= 11:
        digits = digits[-9:]
    if len(digits) == 9:
        return f"{digits[0:3]} {digits[3:6]} {digits[6:9]}"
    return (value or "").strip()


def _extract_first(patterns, text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text or "")
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,.;")
    return ""


def _build_query(search_goal: str, city: str, district: str, extra_instructions: str) -> str:
    parts = [search_goal.strip(), city.strip()]
    if district.strip():
        parts.append(district.strip())
    query = " ".join(part for part in parts if part)
    if extra_instructions.strip():
        query = f"{query}. {extra_instructions.strip()}"
    return query.strip()


def _extract_candidate(result: dict, district_hint: str) -> dict | None:
    title = str(result.get("title") or "").strip()
    url = str(result.get("url") or "").strip()
    content = str(result.get("content") or "").strip()
    raw_content = str(result.get("raw_content") or "").strip()
    merged = "\n".join(part for part in [content, raw_content] if part)

    company_name = _clean_company_name(title, url)
    if not company_name:
        return None

    email = _extract_first([EMAIL_RE], merged)
    phone_match = PHONE_RE.search(merged)
    telephone = _normalize_phone(phone_match.group(0)) if phone_match else ""
    address = _extract_first(ADDRESS_PATTERNS, merged)
    reason_snippet = re.sub(r"\s+", " ", content or raw_content).strip()
    if len(reason_snippet) > 260:
        reason_snippet = f"{reason_snippet[:257]}..."
    reason = reason_snippet or "Wynik Tavily odpowiada zadanej frazie i lokalizacji."

    return {
        "nazwa": company_name,
        "dzielnica": district_hint.strip(),
        "adres": address,
        "email": email,
        "telefon": telephone,
        "powod": reason,
        "strona_www": url,
        "confidence": result.get("score") or 0,
        "source_url": url,
        "title": title,
        "content": content,
    }


def _format_api_error(exc: Exception) -> str:
    raw_message = str(exc)
    if "429" in raw_message:
        return "Wykorzystano kredyty Tavily na ten miesi\u0105c. Poczekaj do nast\u0119pnego miesi\u0105ca lub przejd\u017a na p\u0142atny plan."
    if "401" in raw_message or "403" in raw_message:
        return "Tavily odrzucil autoryzacje. Sprawdz TAVILY_API_KEY."
    return f"Blad Tavily API: {raw_message}"


def search_tavily_candidates(*, search_goal: str, city: str, district: str, max_candidates: int, search_depth: str, extra_instructions: str):
    api_key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not api_key:
        raise TavilyCandidateGenerationError("Brakuje TAVILY_API_KEY w konfiguracji srodowiska.")

    query = _build_query(search_goal=search_goal, city=city, district=district, extra_instructions=extra_instructions)
    body = {
        "query": query,
        "topic": "general",
        "search_depth": search_depth or DEFAULT_SEARCH_DEPTH,
        "max_results": max_candidates,
        "include_raw_content": "markdown",
        "include_answer": False,
        "include_images": False,
        "include_image_descriptions": False,
        "include_favicon": False,
        "country": "poland",
    }

    req = request.Request(
        SEARCH_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        raise TavilyCandidateGenerationError(_format_api_error(Exception(f"{exc.code} {details}"))) from exc
    except error.URLError as exc:
        raise TavilyCandidateGenerationError(f"Nie udalo sie polaczyc z Tavily: {exc.reason}") from exc
    except Exception as exc:  # pragma: no cover
        raise TavilyCandidateGenerationError(_format_api_error(exc)) from exc

    candidates = []
    for result in payload.get("results", []):
        candidate = _extract_candidate(result, district)
        if candidate:
            candidates.append(candidate)

    return {
        "payload": json.dumps(candidates, ensure_ascii=False),
        "candidates": candidates,
        "query": query,
        "response_time": payload.get("response_time"),
        "request_id": payload.get("request_id"),
        "raw_response": payload,
    }


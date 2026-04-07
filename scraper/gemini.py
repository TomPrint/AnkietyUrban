import json
import os
import re

from google import genai
from google.genai import types


class GeminiCandidateGenerationError(RuntimeError):
    pass


DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
DEFAULT_USE_GOOGLE_SEARCH = os.getenv("GEMINI_USE_GOOGLE_SEARCH", "True").lower() in ("1", "true", "yes", "on")


def _candidate_schema(max_candidates: int):
    candidate_schema = {
        "type": "object",
        "properties": {
            "nazwa": {
                "type": "string",
                "description": "Pelna nazwa organizacji lub podmiotu.",
            },
            "dzielnica": {
                "type": "string",
                "description": "Dzielnica, obszar albo lokalizacja w miescie. Puste, jesli nieznane.",
            },
            "adres": {
                "type": "string",
                "description": "Adres siedziby lub adres kontaktowy, jesli zostal znaleziony. Pusty string jesli nieznany.",
            },
            "email": {
                "type": "string",
                "description": "Adres e-mail kontaktowy, jesli zostal znaleziony. Pusty string jesli nieznany.",
            },
            "telefon": {
                "type": "string",
                "description": "Numer telefonu kontaktowego, jesli zostal znaleziony. Pusty string jesli nieznany.",
            },
            "powod": {
                "type": "string",
                "description": "Krotki powod, dlaczego ten podmiot moze byc spoldzielnia mieszkaniowa.",
            },
            "strona_www": {
                "type": "string",
                "description": "Oficjalna strona WWW, jesli zostala znaleziona. W przeciwnym razie pusty string.",
            },
            "confidence": {
                "type": "number",
                "description": "Pewnosc od 0 do 1.",
                "minimum": 0,
                "maximum": 1,
            },
        },
        "required": ["nazwa", "dzielnica", "adres", "email", "telefon", "powod", "strona_www", "confidence"],
        "propertyOrdering": ["nazwa", "dzielnica", "adres", "email", "telefon", "powod", "strona_www", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "description": "Lista kandydatow do dalszej weryfikacji przez operatora.",
                "items": candidate_schema,
                "maxItems": max_candidates,
            },
            "notes": {
                "type": "string",
                "description": "Krotka notatka o jakosci wynikow lub ograniczeniach wyszukiwania.",
            },
        },
        "required": ["candidates", "notes"],
        "propertyOrdering": ["candidates", "notes"],
        "additionalProperties": False,
    }


def _build_prompt(search_goal: str, city: str, district: str, max_candidates: int, extra_instructions: str):
    location_parts = [city.strip()]
    if district.strip():
        location_parts.append(district.strip())
    location_text = ", ".join(part for part in location_parts if part)

    extra_block = ""
    if extra_instructions.strip():
        extra_block = f"Dodatkowe instrukcje: {extra_instructions.strip()}\n"

    schema = json.dumps(_candidate_schema(max_candidates), ensure_ascii=False, indent=2)

    return (
        "Wyszukaj rzeczywiste organizacje lub podmioty, ktore moga pasowac do wskazanego segmentu. "
        "Zwracaj tylko rekordy, dla ktorych istnieja konkretne przeslanki w publicznie dostepnych zrodlach. "
        "Nie wymyslaj nazw, adresow, e-maili, telefonow ani stron WWW. Jesli nie masz wiarygodnej informacji, ustaw pusty string.\n\n"
        f"Cel wyszukiwania: {search_goal.strip()}\n"
        f"Lokalizacja: {location_text or 'Polska'}\n"
        f"Maksymalna liczba kandydatow: {max_candidates}\n"
        f"{extra_block}"
        "Preferuj oficjalne strony organizacji i zrodla potwierdzajace, ze podmiot moze byc zwiazany ze spoldzielnia mieszkaniowa. "
        "Jesli znajdziesz adres siedziby, e-mail kontaktowy lub telefon kontaktowy, zwroc je. Pomijaj firmy deweloperskie, agencje nieruchomosci i przypadkowe wyniki niepasujace do segmentu.\n\n"
        "Zwroc WYLACZNIE poprawny JSON zgodny z ponizszym schematem. "
        "Nie uzywaj markdown, nie dodawaj ```json, nie dodawaj komentarzy ani tekstu przed lub po JSON.\n\n"
        f"Schemat JSON:\n{schema}"
    )


def _extract_json_text(response_text: str) -> str:
    text = (response_text or "").strip()
    if not text:
        raise GeminiCandidateGenerationError("Gemini nie zwrocil tresci odpowiedzi.")

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start_positions = [pos for pos in (text.find("{"), text.find("[")) if pos != -1]
    if not start_positions:
        return text

    start = min(start_positions)
    end_object = text.rfind("}")
    end_array = text.rfind("]")
    end = max(end_object, end_array)
    if end >= start:
        return text[start : end + 1].strip()
    return text[start:].strip()


def _quota_error_message(raw_message: str) -> str:
    retry_match = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", raw_message, flags=re.IGNORECASE)
    retry_delay = None
    if retry_match:
        retry_delay = retry_match.group(1)
    else:
        retry_info_match = re.search(r"'retryDelay': '([^']+)'", raw_message)
        if retry_info_match:
            retry_delay = retry_info_match.group(1)

    message = "Wykorzystano limit free tier Gemini. Poczekaj 24 godziny na odnowienie kredyt\u00f3w Gemini lub rozszerz konto o p\u0142atn\u0105 wersj\u0119."
    if retry_delay:
        message = f"{message} Szacowany czas ponowienia: {retry_delay}."
    return message


def _format_api_error(exc: Exception) -> str:
    raw_message = str(exc)
    upper = raw_message.upper()
    if "RESOURCE_EXHAUSTED" in upper or "429" in upper:
        return _quota_error_message(raw_message)
    return f"Blad Gemini API: {raw_message}"


def generate_candidates_payload(
    *,
    search_goal: str,
    city: str,
    district: str = "",
    max_candidates: int = 10,
    extra_instructions: str = "",
    use_google_search: bool | None = None,
):
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise GeminiCandidateGenerationError("Brak GEMINI_API_KEY lub GOOGLE_API_KEY w srodowisku.")

    use_google_search = DEFAULT_USE_GOOGLE_SEARCH if use_google_search is None else use_google_search
    prompt = _build_prompt(search_goal, city, district, max_candidates, extra_instructions)

    client = genai.Client(api_key=api_key)
    config_kwargs = {
        "temperature": 0.2,
    }
    if use_google_search:
        config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    else:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_json_schema"] = _candidate_schema(max_candidates)

    config = types.GenerateContentConfig(**config_kwargs)

    try:
        response = client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=prompt,
            config=config,
        )
    except Exception as exc:
        raise GeminiCandidateGenerationError(_format_api_error(exc)) from exc

    try:
        data = json.loads(_extract_json_text(getattr(response, "text", "") or ""))
    except json.JSONDecodeError as exc:
        raise GeminiCandidateGenerationError("Gemini zwrocil odpowiedz, ktorej nie udalo sie sparsowac jako JSON.") from exc

    if isinstance(data, list):
        data = {"candidates": data, "notes": ""}
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list):
        raise GeminiCandidateGenerationError("Gemini nie zwrocil listy kandydatow w oczekiwanym formacie.")

    payload = json.dumps(candidates, ensure_ascii=False)
    notes = str(data.get("notes", "")).strip() if isinstance(data, dict) else ""

    return {
        "payload": payload,
        "candidates": candidates,
        "notes": notes,
        "model": DEFAULT_MODEL,
        "raw_response": data,
    }


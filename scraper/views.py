from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import GeminiGenerateForm, LeadCandidateFilterForm, LeadCandidateRejectForm, TavilySearchForm
from .gemini import GeminiCandidateGenerationError, generate_candidates_payload
from .models import LeadCandidate
from .services import approve_candidate, import_gemini_candidates, import_tavily_candidates
from .tavily import TavilyCandidateGenerationError, search_tavily_candidates


staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_staff)


SORTABLE_COLUMNS = {
    "company_name": ["company_name", "id"],
    "source": ["source", "id"],
    "created_at": ["created_at", "id"],
    "reviewed_at": ["reviewed_at", "id"],
    "district": ["district", "company_name", "id"],
    "email": ["email", "company_name", "id"],
    "telephone": ["telephone", "company_name", "id"],
    "reason": ["reason", "company_name", "id"],
    "website": ["website", "company_name", "id"],
    "confidence": ["confidence", "id"],
    "duplicate_sort": ["duplicate_sort", "company_name", "id"],
    "status_sort": ["status_sort", "company_name", "id"],
}


HEADER_COLUMNS = [
    ("company_name", "Nazwa"),
    ("source", "Źródło"),
    ("created_at", "Data wyszukania"),
    ("reviewed_at", "Data decyzji"),
    ("district", "Lokalizacja"),
    ("email", "Email"),
    ("telephone", "Telefon"),
    ("reason", "Powód"),
    ("website", "WWW"),
    ("confidence", "Trafność"),
    ("duplicate_sort", "Duplikaty"),
    ("status_sort", "Akcje"),
]


def _querystring_with(request, **updates):
    query = request.GET.copy()
    for key, value in updates.items():
        if value in (None, ""):
            query.pop(key, None)
        else:
            query[key] = value
    encoded = query.urlencode()
    return f"?{encoded}" if encoded else "?"


def _build_ordering(sort_key: str, sort_dir: str) -> list[str]:
    fields = SORTABLE_COLUMNS.get(sort_key, SORTABLE_COLUMNS["created_at"])
    prefix = "-" if sort_dir == "desc" else ""
    return [f"{prefix}{field}" for field in fields]


@staff_required
def scraper_home(request):
    stats = LeadCandidate.objects.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=LeadCandidate.STATUS_PENDING)),
        approved=Count("id", filter=Q(status=LeadCandidate.STATUS_APPROVED)),
        rejected=Count("id", filter=Q(status=LeadCandidate.STATUS_REJECTED)),
    )
    recent_candidates = LeadCandidate.objects.select_related("duplicate_customer", "approved_customer")[:6]
    return render(
        request,
        "scraper/home.html",
        {
            "stats": stats,
            "recent_candidates": recent_candidates,
        },
    )


@staff_required
def gemini_import(request):
    gemini_form = GeminiGenerateForm()
    tavily_form = TavilySearchForm()
    return render(
        request,
        "scraper/gemini_import.html",
        {
            "gemini_form": gemini_form,
            "tavily_form": tavily_form,
            "active_form": "gemini",
        },
    )


@staff_required
@require_POST
def gemini_generate(request):
    gemini_form = GeminiGenerateForm(request.POST)
    tavily_form = TavilySearchForm()
    if gemini_form.is_valid():
        try:
            generated = generate_candidates_payload(
                search_goal=gemini_form.cleaned_data["search_goal"],
                city=gemini_form.cleaned_data["city"],
                district=gemini_form.cleaned_data["district"],
                max_candidates=gemini_form.cleaned_data["max_candidates"],
                extra_instructions=gemini_form.cleaned_data["extra_instructions"],
                use_google_search=gemini_form.cleaned_data["use_google_search"],
            )
            result = import_gemini_candidates(generated["payload"])
        except GeminiCandidateGenerationError as exc:
            gemini_form.add_error(None, str(exc))
        else:
            message = (
                f"Gemini ({generated['model']}) wygenerowal i zaimportowal {result['created_count']} kandydatow. "
                f"Pominieto {result['skipped_count']} pozycji. Oznaczono {result['duplicates_count']} duplikatow."
            )
            if generated["notes"]:
                message = f"{message} Notatka modelu: {generated['notes']}"
            messages.success(request, message)
            return redirect("scraper-candidates")

    return render(
        request,
        "scraper/gemini_import.html",
        {
            "gemini_form": gemini_form,
            "tavily_form": tavily_form,
            "active_form": "gemini",
        },
    )


@staff_required
@require_POST
def tavily_generate(request):
    gemini_form = GeminiGenerateForm()
    tavily_form = TavilySearchForm(request.POST)
    if tavily_form.is_valid():
        try:
            generated = search_tavily_candidates(
                search_goal=tavily_form.cleaned_data["search_goal"],
                city=tavily_form.cleaned_data["city"],
                district=tavily_form.cleaned_data["district"],
                max_candidates=tavily_form.cleaned_data["max_candidates"],
                search_depth=tavily_form.cleaned_data["search_depth"],
                extra_instructions=tavily_form.cleaned_data["extra_instructions"],
            )
            result = import_tavily_candidates(generated["payload"])
        except TavilyCandidateGenerationError as exc:
            tavily_form.add_error(None, str(exc))
        else:
            message = (
                f"Tavily zaimportowal {result['created_count']} kandydatow dla zapytania '{generated['query']}'. "
                f"Pominieto {result['skipped_count']} pozycji. Oznaczono {result['duplicates_count']} duplikatow."
            )
            messages.success(request, message)
            return redirect("scraper-candidates")

    return render(
        request,
        "scraper/gemini_import.html",
        {
            "gemini_form": gemini_form,
            "tavily_form": tavily_form,
            "active_form": "tavily",
        },
    )


@staff_required
def candidate_list(request):
    status = request.GET.get("status", LeadCandidate.STATUS_PENDING)
    if status not in {
        LeadCandidate.STATUS_PENDING,
        LeadCandidate.STATUS_APPROVED,
        LeadCandidate.STATUS_REJECTED,
        "all",
    }:
        status = LeadCandidate.STATUS_PENDING

    filter_form = LeadCandidateFilterForm(request.GET or None)
    filter_form.is_valid()
    q = (filter_form.cleaned_data.get("q") or "").strip() if hasattr(filter_form, "cleaned_data") else ""
    source = (filter_form.cleaned_data.get("source") or "").strip() if hasattr(filter_form, "cleaned_data") else ""
    duplicate = (filter_form.cleaned_data.get("duplicate") or "").strip() if hasattr(filter_form, "cleaned_data") else ""

    sort = request.GET.get("sort", "created_at").strip()
    direction = request.GET.get("dir", "desc").strip()
    if sort not in SORTABLE_COLUMNS:
        sort = "created_at"
    if direction not in {"asc", "desc"}:
        direction = "desc"

    queryset = LeadCandidate.objects.select_related(
        "duplicate_customer",
        "duplicate_candidate",
        "approved_customer",
        "reviewed_by",
    ).annotate(
        duplicate_sort=Case(
            When(Q(duplicate_customer__isnull=False) | Q(duplicate_candidate__isnull=False), then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        status_sort=Case(
            When(status=LeadCandidate.STATUS_PENDING, then=Value(1)),
            When(status=LeadCandidate.STATUS_APPROVED, then=Value(2)),
            When(status=LeadCandidate.STATUS_REJECTED, then=Value(3)),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )

    if status != "all":
        queryset = queryset.filter(status=status).order_by("-created_at", "-id")
    else:
        if q:
            queryset = queryset.filter(
                Q(company_name__icontains=q)
                | Q(district__icontains=q)
                | Q(address__icontains=q)
                | Q(email__icontains=q)
                | Q(telephone__icontains=q)
                | Q(reason__icontains=q)
                | Q(website__icontains=q)
                | Q(source__icontains=q)
            )
        if source:
            queryset = queryset.filter(source=source)
        if duplicate == "yes":
            queryset = queryset.filter(Q(duplicate_customer__isnull=False) | Q(duplicate_candidate__isnull=False))
        elif duplicate == "no":
            queryset = queryset.filter(duplicate_customer__isnull=True, duplicate_candidate__isnull=True)
        queryset = queryset.order_by(*_build_ordering(sort, direction))

    paginator = Paginator(queryset, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    status_options = [
        ("pending", "Oczekujący"),
        ("approved", "Zatwierdzeni"),
        ("rejected", "Odrzuceni"),
        ("all", "Wszyscy"),
    ]

    header_columns = []
    for key, label in HEADER_COLUMNS:
        is_active = key == sort
        next_dir = "asc"
        if is_active and direction == "asc":
            next_dir = "desc"
        header_columns.append(
            {
                "key": key,
                "label": label,
                "active": is_active,
                "direction": direction if is_active else "",
                "url": _querystring_with(request, sort=key, dir=next_dir, page=None),
            }
        )

    context = {
        "page_obj": page_obj,
        "status": status,
        "status_options": [
            {
                "value": value,
                "label": label,
                "url": _querystring_with(request, status=value, page=None),
            }
            for value, label in status_options
        ],
        "header_columns": header_columns,
        "reject_form": LeadCandidateRejectForm(),
        "filter_form": filter_form,
        "show_filters": status == "all",
        "reset_filters_url": _querystring_with(request, status="all", q=None, source=None, duplicate=None, sort=None, dir=None, page=None),
        "previous_page_url": _querystring_with(request, page=page_obj.previous_page_number()) if page_obj.has_previous() else "",
        "next_page_url": _querystring_with(request, page=page_obj.next_page_number()) if page_obj.has_next() else "",
    }
    return render(request, "scraper/candidate_list.html", context)


@staff_required
@require_POST
def candidate_approve(request, candidate_id: int):
    candidate = get_object_or_404(LeadCandidate, pk=candidate_id)
    if candidate.status == LeadCandidate.STATUS_APPROVED:
        messages.success(request, "Ten kandydat jest juz zatwierdzony.")
        return redirect("scraper-candidates")

    approve_candidate(candidate, request.user)
    messages.success(request, f"Zatwierdzono kandydata: {candidate.company_name}.")
    return redirect("scraper-candidates")


@staff_required
@require_POST
def candidate_reject(request, candidate_id: int):
    candidate = get_object_or_404(LeadCandidate, pk=candidate_id)
    form = LeadCandidateRejectForm(request.POST)
    if form.is_valid():
        candidate.mark_rejected(request.user, form.cleaned_data["reason"])
        messages.success(request, f"Odrzucono kandydata: {candidate.company_name}.")
    else:
        messages.error(request, "Nie udalo sie odrzucic kandydata.")
    return redirect("scraper-candidates")


@staff_required
@require_POST
def candidate_reopen(request, candidate_id: int):
    candidate = get_object_or_404(LeadCandidate, pk=candidate_id)
    if candidate.status != LeadCandidate.STATUS_REJECTED:
        messages.error(request, "Tylko odrzucony kandydat moze trafic ponownie do oczekujacych.")
        return redirect("scraper-candidates")

    candidate.mark_pending()
    messages.success(request, f"Kandydat ponownie trafil do oczekujacych: {candidate.company_name}.")
    return redirect("scraper-candidates")


@staff_required
@require_POST
def candidate_delete(request, candidate_id: int):
    candidate = get_object_or_404(LeadCandidate, pk=candidate_id)
    if candidate.status != LeadCandidate.STATUS_REJECTED:
        messages.error(request, "Usunac mozna tylko rekord odrzucony.")
        return redirect("scraper-candidates")

    company_name = candidate.company_name
    candidate.delete()
    messages.success(request, f"Usunieto odrzuconego kandydata: {company_name}.")
    return redirect("scraper-candidates")


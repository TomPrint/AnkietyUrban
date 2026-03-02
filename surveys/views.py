import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import View

from .forms import CustomerForm, DynamicQuestionForm, QuestionManageForm, SurveyAssignmentForm, SurveyTemplateForm, UserManageForm
from .models import (
    Customer,
    Question,
    SurveyAnswer,
    SurveySession,
    SurveySessionEvent,
    SurveySubmissionSnapshot,
    SurveyTemplate,
    TemplateNode,
)

SYSTEM_START_QUESTION_TITLE = "Imie i nazwisko osoby wypelniajacej ankiete"
SYSTEM_START_QUESTION_HELP = "Podaj imie i nazwisko."
staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_staff)


def _start_node(template: SurveyTemplate):
    return template.start_node or template.nodes.order_by("id").first()


def _template_is_live(template: SurveyTemplate) -> bool:
    return template.survey_sessions.exists()


def _normalize_live_templates_to_ready():
    SurveyTemplate.objects.filter(
        status=SurveyTemplate.Status.DRAFT,
        survey_sessions__isnull=False,
    ).update(status=SurveyTemplate.Status.READY)


def _get_or_create_system_start_question() -> Question:
    question = Question.objects.filter(is_system=True).order_by("id").first()
    if question is None:
        question = Question.objects.create(
            title=SYSTEM_START_QUESTION_TITLE,
            question_type=Question.QuestionType.OPEN,
            help_text=SYSTEM_START_QUESTION_HELP,
            required=True,
            is_system=True,
        )
        return question

    updates = []
    if question.title != SYSTEM_START_QUESTION_TITLE:
        question.title = SYSTEM_START_QUESTION_TITLE
        updates.append("title")
    if question.question_type != Question.QuestionType.OPEN:
        question.question_type = Question.QuestionType.OPEN
        updates.append("question_type")
    if question.help_text != SYSTEM_START_QUESTION_HELP:
        question.help_text = SYSTEM_START_QUESTION_HELP
        updates.append("help_text")
    if not question.required:
        question.required = True
        updates.append("required")
    if not question.is_system:
        question.is_system = True
        updates.append("is_system")
    if updates:
        question.save(update_fields=updates + ["updated_at"])
    return question


def _ensure_forced_start_node(template: SurveyTemplate) -> TemplateNode:
    system_question = _get_or_create_system_start_question()
    current_start_id = template.start_node_id
    forced_node = template.nodes.filter(is_forced_start=True).order_by("id").first()

    if forced_node is None:
        forced_node = TemplateNode.objects.create(
            template=template,
            question=system_question,
            is_forced_start=True,
            x=60,
            y=30,
            next_node_id=current_start_id if current_start_id else None,
        )
    else:
        updates = []
        if forced_node.question_id != system_question.id:
            forced_node.question = system_question
            updates.append("question")
        if not forced_node.is_forced_start:
            forced_node.is_forced_start = True
            updates.append("is_forced_start")
        if forced_node.ends_survey:
            forced_node.ends_survey = False
            updates.append("ends_survey")
        if forced_node.end_on_yes:
            forced_node.end_on_yes = False
            updates.append("end_on_yes")
        if forced_node.end_on_no:
            forced_node.end_on_no = False
            updates.append("end_on_no")
        if forced_node.yes_node_id is not None:
            forced_node.yes_node = None
            updates.append("yes_node")
        if forced_node.no_node_id is not None:
            forced_node.no_node = None
            updates.append("no_node")
        if forced_node.next_node_id is None and current_start_id and current_start_id != forced_node.id:
            forced_node.next_node_id = current_start_id
            updates.append("next_node")
        if updates:
            forced_node.save(update_fields=updates)

    if template.start_node_id != forced_node.id:
        template.start_node_id = forced_node.id
        template.save(update_fields=["start_node", "updated_at"])
    return forced_node


def _effective_next_nodes(node: TemplateNode):
    if node.question.question_type == Question.QuestionType.YES_NO:
        result = []
        if not node.end_on_yes and node.yes_node_id:
            result.append(node.yes_node_id)
        if not node.end_on_no and node.no_node_id:
            result.append(node.no_node_id)
        return result
    if node.ends_survey or not node.next_node_id:
        return []
    return [node.next_node_id]


def _validate_template_graph(template: SurveyTemplate):
    errors = []
    _ensure_forced_start_node(template)
    nodes = list(template.nodes.select_related("question", "next_node", "yes_node", "no_node"))
    node_map = {n.id: n for n in nodes}

    if not nodes:
        errors.append("Template has no nodes.")
        return errors

    start = _start_node(template)
    if not start:
        errors.append("Template has no start node.")
        return errors

    for n in nodes:
        if n.question.is_archived:
            errors.append(f"Node #{n.id}: question is archived. Replace it before saving as Ready.")
            continue
        if n.question.question_type == Question.QuestionType.YES_NO:
            has_yes = bool(n.end_on_yes or n.yes_node_id)
            has_no = bool(n.end_on_no or n.no_node_id)
            if not has_yes:
                errors.append(f"Node #{n.id}: YES path is missing (add link or mark end).")
            if not has_no:
                errors.append(f"Node #{n.id}: NO path is missing (add link or mark end).")
        else:
            if not (n.ends_survey or n.next_node_id):
                errors.append(f"Node #{n.id}: NEXT path is missing (add link or mark end).")

    reachable = set()
    stack = [start.id]
    while stack:
        nid = stack.pop()
        if nid in reachable:
            continue
        reachable.add(nid)
        node = node_map.get(nid)
        if not node:
            continue
        stack.extend(_effective_next_nodes(node))

    unreachable = [n.id for n in nodes if n.id not in reachable]
    if unreachable:
        errors.append(
            "Some nodes are not reachable from start: " + ", ".join(f"#{nid}" for nid in sorted(unreachable))
        )

    visiting = set()
    visited = set()
    cycle_hits = set()

    def dfs(nid):
        if nid in visiting:
            cycle_hits.add(nid)
            return
        if nid in visited:
            return
        visiting.add(nid)
        node = node_map.get(nid)
        if node:
            for nxt in _effective_next_nodes(node):
                if nxt in node_map:
                    dfs(nxt)
        visiting.remove(nid)
        visited.add(nid)

    dfs(start.id)
    if cycle_hits:
        errors.append(
            "Loop detected in survey flow (returning to same node is not allowed): "
            + ", ".join(f"#{nid}" for nid in sorted(cycle_hits))
        )

    return errors


def _resolve_next_node(node: TemplateNode, answer_data):
    question_type = node.question.question_type
    if question_type == Question.QuestionType.YES_NO:
        if answer_data == "yes" and node.end_on_yes:
            return None
        if answer_data == "no" and node.end_on_no:
            return None
        if answer_data == "yes":
            if node.yes_node:
                return node.yes_node
            return None
        if answer_data == "no":
            if node.no_node:
                return node.no_node
            return None
        return None
    if node.ends_survey:
        return None
    if node.next_node:
        return node.next_node
    return None


def _start_node_or_404(template: SurveyTemplate, *, require_ready: bool = True):
    _ensure_forced_start_node(template)
    if require_ready and template.status != SurveyTemplate.Status.READY:
        raise Http404("Template is not survey-ready.")
    start = _start_node(template)
    if not start:
        raise Http404("Template has no nodes.")
    return start


def _validate_target_node(template: SurveyTemplate, node_id: str | None, source_node: TemplateNode | None = None):
    if node_id in (None, ""):
        return None
    target = get_object_or_404(TemplateNode, pk=node_id, template=template)
    if target.is_forced_start:
        raise Http404("Cannot link to forced start node.")
    incoming_qs = template.nodes.filter(
        Q(next_node=target) | Q(yes_node=target) | Q(no_node=target)
    )
    if source_node is not None:
        incoming_qs = incoming_qs.exclude(pk=source_node.pk)
    if incoming_qs.exists():
        raise ValueError(f"Node #{target.id} already has an incoming connection.")
    return target


def _serialize_node(template: SurveyTemplate, n: TemplateNode):
    return {
        "id": n.id,
        "question_id": n.question_id,
        "question_title": n.question.title,
        "question_type": n.question.question_type,
        "x": n.x,
        "y": n.y,
        "next_id": n.next_node_id,
        "yes_id": n.yes_node_id,
        "no_id": n.no_node_id,
        "ends_survey": n.ends_survey,
        "end_on_yes": n.end_on_yes,
        "end_on_no": n.end_on_no,
        "is_start": template.start_node_id == n.id,
        "is_forced_start": n.is_forced_start,
    }


def _ensure_active_session(session: SurveySession):
    if session.status in (SurveySession.Status.CLOSED, SurveySession.Status.SAVED_AGAIN):
        session.mark_open()
        session.current_node = _start_node(session.template)
        session.save(
            update_fields=[
                "status",
                "submitted_at",
                "current_node",
                "updated_at",
            ]
        )


def _touch_session_activity(session: SurveySession) -> bool:
    now = timezone.now()
    just_opened = False
    update_fields = ["last_activity_at", "updated_at"]
    if session.first_opened_at is None:
        session.first_opened_at = now
        just_opened = True
        update_fields.append("first_opened_at")
    if session.last_activity_at is not None:
        delta = max(0, int((now - session.last_activity_at).total_seconds()))
        # Cap a single inactivity window to avoid counting long idle tab time as active usage.
        capped_delta = min(delta, 300)
        if capped_delta > 0:
            session.active_seconds += capped_delta
            update_fields.append("active_seconds")
    session.last_activity_at = now
    session.save(update_fields=update_fields)
    return just_opened


def _log_session_event(
    session: SurveySession,
    event_type: str,
    *,
    node: TemplateNode | None = None,
    request: HttpRequest | None = None,
    details: dict | None = None,
):
    payload = details.copy() if details else {}
    if request is not None:
        client_ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")
        if client_ip:
            payload["ip"] = client_ip
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        if user_agent:
            payload["user_agent"] = user_agent[:400]
    SurveySessionEvent.objects.create(
        session=session,
        event_type=event_type,
        node=node,
        details=payload,
    )


def _format_seconds(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _is_session_completed(session: SurveySession) -> bool:
    return session.status in (SurveySession.Status.CLOSED, SurveySession.Status.SAVED_AGAIN)


def _build_or_get_answer(session: SurveySession, node: TemplateNode):
    answer, _ = SurveyAnswer.objects.get_or_create(session=session, node=node, defaults={"question": node.question})
    if answer.question_id != node.question_id:
        answer.question = node.question
        answer.save(update_fields=["question", "updated_at"])
    return answer


def _persist_answer(answer: SurveyAnswer, node: TemplateNode, value):
    answer.yes_no_answer = None
    answer.open_answer = ""
    answer.complex_answer = []
    answer.save()
    answer.selected_choices.clear()

    q_type = node.question.question_type
    if q_type == Question.QuestionType.YES_NO:
        answer.yes_no_answer = value == "yes"
        answer.save(update_fields=["yes_no_answer", "updated_at"])
        return
    if q_type == Question.QuestionType.MULTI_CHOICE:
        answer.save(update_fields=["updated_at"])
        if value:
            answer.selected_choices.set(value)
        return
    if q_type == Question.QuestionType.COMPLEX:
        answer.complex_answer = value or []
        answer.save(update_fields=["complex_answer", "updated_at"])
        return
    answer.open_answer = value or ""
    answer.save(update_fields=["open_answer", "updated_at"])


def _answer_value_display(answer: SurveyAnswer) -> str:
    q_type = answer.question.question_type
    if q_type == Question.QuestionType.YES_NO:
        if answer.yes_no_answer is True:
            return "Yes"
        if answer.yes_no_answer is False:
            return "No"
        return "-"
    if q_type == Question.QuestionType.MULTI_CHOICE:
        labels = list(answer.selected_choices.values_list("label", flat=True))
        return ", ".join(labels) if labels else "-"
    if q_type == Question.QuestionType.COMPLEX:
        items = answer.complex_answer or []
        if not items:
            return "-"
        rendered = []
        for item in items:
            label = item.get("label", "Item")
            value = item.get("value")
            item_type = item.get("type")
            if isinstance(value, list):
                if item_type == Question.QuestionType.MULTI_CHOICE:
                    options = item.get("options", [])
                    selected_labels = []
                    for v in value:
                        try:
                            index = int(v)
                        except (TypeError, ValueError):
                            continue
                        if 0 <= index < len(options):
                            selected_labels.append(options[index])
                    value_text = ", ".join(selected_labels) if selected_labels else "-"
                else:
                    value_text = ", ".join(str(v) for v in value) if value else "-"
            else:
                value_text = str(value).strip() if value is not None and str(value).strip() else "-"
            rendered.append(f"{label}: {value_text}")
        return " | ".join(rendered)
    value = (answer.open_answer or "").strip()
    return value if value else "-"


def _capture_submission_snapshot(session: SurveySession):
    max_version = session.snapshots.aggregate(m=Max("version_number")).get("m") or 0
    answers_qs = (
        session.answers.select_related("question", "node")
        .prefetch_related("selected_choices")
        .order_by("node_id", "id")
    )
    serialized_answers = []
    for answer in answers_qs:
        serialized_answers.append(
            {
                "node_id": answer.node_id,
                "question_id": answer.question_id,
                "question_title": answer.question.title,
                "question_type": answer.question.question_type,
                "value": _answer_value_display(answer),
            }
        )

    SurveySubmissionSnapshot.objects.create(
        session=session,
        version_number=max_version + 1,
        status=session.status,
        answers=serialized_answers,
    )


@staff_required
def management_dashboard(request: HttpRequest) -> HttpResponse:
    context = {
        "customers_count": Customer.objects.filter(is_archived=False).count(),
        "questions_count": Question.objects.filter(is_system=False, is_archived=False).count(),
        "templates_count": SurveyTemplate.objects.filter(is_archived=False).count(),
        "sessions_open": SurveySession.objects.filter(status=SurveySession.Status.OPEN, is_archived=False).count(),
        "generated_links_count": SurveySession.objects.filter(is_archived=False).count(),
        "sessions_closed": SurveySession.objects.filter(
            status__in=[SurveySession.Status.CLOSED, SurveySession.Status.SAVED_AGAIN],
            is_archived=False,
        )
        .values("id")
        .distinct()
        .count(),
        "latest_sessions": SurveySession.objects.select_related("customer", "template").filter(is_archived=False)[:10],
    }
    return render(request, "management/dashboard.html", context)


@staff_required
def user_list(request: HttpRequest) -> HttpResponse:
    users = User.objects.annotate(
        generated_links_count=Count(
            "created_survey_sessions",
            filter=Q(created_survey_sessions__is_archived=False),
        )
    ).order_by("username")
    return render(request, "management/users/list.html", {"users": users})


@staff_required
def user_create(request: HttpRequest) -> HttpResponse:
    form = UserManageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("management-users")
    return render(request, "management/users/form.html", {"form": form, "title": "Create User"})


@staff_required
def user_edit(request: HttpRequest, user_id: int) -> HttpResponse:
    managed_user = get_object_or_404(User, pk=user_id)
    form = UserManageForm(request.POST or None, instance=managed_user)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("management-users")
    return render(request, "management/users/form.html", {"form": form, "title": "Edit User"})


@staff_required
@require_POST
def user_delete(request: HttpRequest, user_id: int) -> HttpResponse:
    managed_user = get_object_or_404(User, pk=user_id)
    if managed_user.id == request.user.id:
        messages.error(request, "You cannot delete your own account.")
        return redirect("management-users")
    managed_user.delete()
    return redirect("management-users")


@staff_required
def customer_list(request: HttpRequest) -> HttpResponse:
    customers = Customer.objects.filter(is_archived=False)
    return render(request, "management/customers/list.html", {"customers": customers})


@staff_required
def customer_create(request: HttpRequest) -> HttpResponse:
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("management-customers")
    return render(request, "management/customers/form.html", {"form": form, "title": "Create Customer"})


@staff_required
def customer_edit(request: HttpRequest, customer_id: int) -> HttpResponse:
    customer = get_object_or_404(Customer, pk=customer_id, is_archived=False)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("management-customers")
    return render(request, "management/customers/form.html", {"form": form, "title": "Edit Customer"})


@staff_required
@require_POST
def customer_delete(request: HttpRequest, customer_id: int) -> HttpResponse:
    customer = get_object_or_404(Customer, pk=customer_id, is_archived=False)
    customer.archive()
    return redirect("management-customers")


@staff_required
def survey_assignment_portal(request: HttpRequest) -> HttpResponse:
    form = SurveyAssignmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        session = form.save(commit=False)
        _ensure_forced_start_node(session.template)
        _start_node_or_404(session.template, require_ready=True)
        if request.user.is_authenticated:
            session.created_by = request.user
            session.created_by_name = request.user.username
        session.status = SurveySession.Status.OPEN
        session.current_node = _start_node(session.template)
        session.save()
        return redirect("management-assignments")

    sort_map = {
        "customer": "customer__company_name",
        "template": "template__name",
        "status": "status",
        "first_open": "started_at",
        "first_save": "first_saved_at",
        "saved_again": "last_saved_again_at",
        "link": "is_link_active",
    }
    sort = request.GET.get("sort", "first_open")
    direction = request.GET.get("dir", "desc")
    if sort not in sort_map:
        sort = "first_open"
    if direction not in ("asc", "desc"):
        direction = "desc"

    order_field = sort_map[sort]
    if direction == "desc":
        order_field = f"-{order_field}"

    search_query = request.GET.get("q", "").strip()
    sessions = SurveySession.objects.select_related("customer", "template").filter(is_archived=False)
    if search_query:
        filters = (
            Q(customer__company_name__icontains=search_query)
            | Q(template__name__icontains=search_query)
            | Q(status__icontains=search_query)
        )
        try:
            token_value = uuid.UUID(search_query)
            filters = filters | Q(token=token_value)
        except ValueError:
            pass
        sessions = sessions.filter(filters)
    sessions = sessions.order_by(order_field, "-updated_at")
    return render(
        request,
        "management/assignments/list.html",
        {"form": form, "sessions": sessions, "sort": sort, "dir": direction, "q": search_query},
    )


@staff_required
@require_POST
def survey_link_toggle(request: HttpRequest, session_id: int) -> HttpResponse:
    session = get_object_or_404(SurveySession, pk=session_id, is_archived=False)
    action = request.POST.get("action")
    if action == "deactivate":
        session.is_link_active = False
    elif action == "activate":
        session.is_link_active = True
    session.save(update_fields=["is_link_active", "updated_at"])
    return redirect("management-assignments")


@staff_required
@require_POST
def survey_session_delete(request: HttpRequest, session_id: int) -> HttpResponse:
    session = get_object_or_404(SurveySession, pk=session_id, is_archived=False)
    session.archive()
    return redirect("management-assignments")


@staff_required
def survey_session_detail(request: HttpRequest, session_id: int) -> HttpResponse:
    session = get_object_or_404(
        SurveySession.objects.select_related("customer", "template", "current_node", "current_node__question"),
        pk=session_id,
        is_archived=False,
    )
    snapshots = session.snapshots.all()
    total_nodes = session.template.nodes.count()
    answered_nodes = session.answers.values("node_id").distinct().count()
    is_completed = session.status in (SurveySession.Status.CLOSED, SurveySession.Status.SAVED_AGAIN)
    completion_percent = 100 if is_completed else int((answered_nodes / total_nodes) * 100) if total_nodes else 0
    drop_off_question = session.current_node.display_title if session.current_node_id else "-"
    drop_off_time = session.last_activity_at if not is_completed and session.current_node_id else None
    activity_events = session.events.select_related("node")[:200]
    context = {
        "session": session,
        "snapshots": snapshots,
        "link_opened": bool(session.first_opened_at),
        "active_time_display": _format_seconds(session.active_seconds),
        "completion_percent": completion_percent,
        "drop_off_question": drop_off_question,
        "drop_off_time": drop_off_time,
        "activity_events": activity_events,
    }
    return render(request, "management/assignments/detail.html", context)


@staff_required
def question_list(request: HttpRequest) -> HttpResponse:
    search_query = request.GET.get("q", "").strip()
    sort_map = {
        "title": "title",
        "type": "question_type",
        "options": "choices_count",
        "updated": "updated_at",
    }
    sort = request.GET.get("sort", "updated")
    direction = request.GET.get("dir", "desc")
    if sort not in sort_map:
        sort = "updated"
    if direction not in ("asc", "desc"):
        direction = "desc"

    questions = (
        Question.objects.filter(is_system=False, is_archived=False)
        .prefetch_related("choices")
        .annotate(choices_count=Count("choices"))
    )
    if search_query:
        questions = questions.filter(
            Q(title__icontains=search_query)
            | Q(help_text__icontains=search_query)
            | Q(question_type__icontains=search_query)
        )

    order_by = sort_map[sort]
    if direction == "desc":
        order_by = f"-{order_by}"
    questions = questions.order_by(order_by, "-updated_at")

    return render(
        request,
        "management/questions/list.html",
        {"questions": questions, "q": search_query, "sort": sort, "dir": direction},
    )


@staff_required
def question_create(request: HttpRequest) -> HttpResponse:
    form = QuestionManageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("management-questions")
    return render(request, "management/questions/form.html", {"form": form, "title": "Create Question"})


@staff_required
def question_edit(request: HttpRequest, question_id: int) -> HttpResponse:
    question = get_object_or_404(Question, pk=question_id, is_system=False, is_archived=False)
    form = QuestionManageForm(request.POST or None, instance=question)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("management-questions")
    return render(request, "management/questions/form.html", {"form": form, "title": "Edit Question"})


@staff_required
def question_detail(request: HttpRequest, question_id: int) -> HttpResponse:
    question = get_object_or_404(Question, pk=question_id, is_system=False, is_archived=False)
    templates = (
        SurveyTemplate.objects.filter(nodes__question=question)
        .distinct()
        .order_by("name")
    )
    sessions = (
        SurveySession.objects.select_related("customer", "template")
        .filter(template__nodes__question=question)
        .distinct()
        .order_by("-updated_at")
    )
    active_sessions = sessions.filter(is_link_active=True)
    context = {
        "question": question,
        "templates": templates,
        "sessions": sessions[:50],
        "templates_count": templates.count(),
        "sessions_count": sessions.count(),
        "active_sessions_count": active_sessions.count(),
    }
    return render(request, "management/questions/detail.html", context)


@staff_required
@require_POST
def question_delete(request: HttpRequest, question_id: int) -> HttpResponse:
    question = get_object_or_404(Question, pk=question_id, is_system=False, is_archived=False)
    question.archive()
    return redirect("management-questions")


@staff_required
def template_list(request: HttpRequest) -> HttpResponse:
    _normalize_live_templates_to_ready()
    search_query = request.GET.get("q", "").strip()
    sort_map = {
        "name": "name",
        "status": "status",
        "nodes": "nodes_count",
        "updated": "updated_at",
        "live": "live_sessions_count",
    }
    sort = request.GET.get("sort", "live")
    direction = request.GET.get("dir", "desc")
    if sort not in sort_map:
        sort = "live"
    if direction not in ("asc", "desc"):
        direction = "desc"

    templates = (
        SurveyTemplate.objects.filter(is_archived=False)
        .prefetch_related("nodes")
        .annotate(
            live_sessions_count=Count("survey_sessions", distinct=True),
            nodes_count=Count("nodes", distinct=True),
        )
    )
    if search_query:
        templates = templates.filter(
            Q(name__icontains=search_query)
            | Q(description__icontains=search_query)
            | Q(status__icontains=search_query)
        )

    order_by = sort_map[sort]
    if direction == "desc":
        order_by = f"-{order_by}"
    templates = templates.order_by(order_by, "-live_sessions_count", "-updated_at")
    return render(
        request,
        "management/templates/list.html",
        {"templates": templates, "q": search_query, "sort": sort, "dir": direction},
    )


@staff_required
def template_copy(request: HttpRequest) -> HttpResponse:
    templates = (
        SurveyTemplate.objects.filter(is_archived=False)
        .annotate(live_sessions_count=Count("survey_sessions"))
        .order_by("name")
    )
    initial_source = request.GET.get("source", "")

    if request.method == "POST":
        source_id = request.POST.get("source_template", "").strip()
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()

        if not source_id:
            messages.error(request, "Choose a source template to copy.")
            return render(
                request,
                "management/templates/copy.html",
                {"templates": templates, "initial_source": initial_source},
            )

        source_template = get_object_or_404(SurveyTemplate, pk=source_id, is_archived=False)
        if source_template.nodes.filter(question__is_archived=True).exists():
            messages.error(
                request,
                "Source template contains archived questions. Update the source template before copying.",
            )
            return render(
                request,
                "management/templates/copy.html",
                {"templates": templates, "initial_source": source_id},
            )
        if not name:
            name = f"{source_template.name} (copy)"

        with transaction.atomic():
            new_template = SurveyTemplate.objects.create(
                name=name,
                description=description or source_template.description,
                status=SurveyTemplate.Status.DRAFT,
            )
            forced_start = _ensure_forced_start_node(new_template)

            source_nodes = list(source_template.nodes.select_related("question").all())
            node_map = {}

            for old in source_nodes:
                if old.is_forced_start:
                    continue
                node_map[old.id] = TemplateNode.objects.create(
                    template=new_template,
                    question=old.question,
                    title_override=old.title_override,
                    is_forced_start=False,
                    x=old.x,
                    y=old.y,
                    ends_survey=old.ends_survey,
                    end_on_yes=old.end_on_yes,
                    end_on_no=old.end_on_no,
                )

            for old in source_nodes:
                if old.is_forced_start:
                    continue
                new_node = node_map[old.id]
                new_node.next_node = node_map.get(old.next_node_id)
                new_node.yes_node = node_map.get(old.yes_node_id)
                new_node.no_node = node_map.get(old.no_node_id)
                new_node.save(update_fields=["next_node", "yes_node", "no_node"])

            source_start = source_template.start_node or source_template.nodes.order_by("id").first()
            start_target_old_id = None
            if source_start:
                start_target_old_id = source_start.next_node_id if source_start.is_forced_start else source_start.id
            forced_start.next_node = node_map.get(start_target_old_id)
            forced_start.save(update_fields=["next_node"])

        messages.success(request, f"Template copied: {new_template.name}")
        return redirect("management-template-builder", template_id=new_template.id)

    return render(
        request,
        "management/templates/copy.html",
        {"templates": templates, "initial_source": initial_source},
    )


@staff_required
def template_create(request: HttpRequest) -> HttpResponse:
    form = SurveyTemplateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        template = form.save()
        _ensure_forced_start_node(template)
        return redirect("management-template-builder", template_id=template.id)
    return render(request, "management/templates/form.html", {"form": form, "title": "Create Template"})


@staff_required
def template_edit(request: HttpRequest, template_id: int) -> HttpResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    if _template_is_live(template):
        messages.error(request, "Template is live (assigned to surveys) and cannot be edited.")
        return redirect("management-templates")
    form = SurveyTemplateForm(request.POST or None, instance=template)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("management-template-builder", template_id=template.id)
    return render(request, "management/templates/form.html", {"form": form, "title": "Edit Template"})


@staff_required
@require_POST
def template_delete(request: HttpRequest, template_id: int) -> HttpResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    template.archive()
    return redirect("management-templates")


@staff_required
def template_builder(request: HttpRequest, template_id: int) -> HttpResponse:
    template = get_object_or_404(SurveyTemplate.objects.select_related("start_node"), pk=template_id, is_archived=False)
    if _template_is_live(template):
        messages.error(request, "Template is live (assigned to surveys). Builder is locked.")
        return redirect("management-templates")
    _ensure_forced_start_node(template)
    nodes = list(template.nodes.select_related("question", "next_node", "yes_node", "no_node").all())
    questions = list(Question.objects.filter(is_system=False, is_archived=False).prefetch_related("choices").all())

    context = {
        "template": template,
        "questions": questions,
        "nodes_data": [_serialize_node(template, n) for n in nodes],
    }
    return render(request, "management/templates/builder.html", context)


@staff_required
def template_preview(request: HttpRequest, template_id: int) -> HttpResponse:
    template = get_object_or_404(SurveyTemplate.objects.select_related("start_node"), pk=template_id, is_archived=False)
    _ensure_forced_start_node(template)
    nodes = list(template.nodes.select_related("question", "next_node", "yes_node", "no_node").all())
    context = {
        "template": template,
        "nodes_data": [_serialize_node(template, n) for n in nodes],
    }
    return render(request, "management/templates/preview.html", context)


@staff_required
@require_GET
def template_builder_data(request: HttpRequest, template_id: int) -> JsonResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    if _template_is_live(template):
        return JsonResponse({"ok": False, "error": "Template is live and locked."}, status=403)
    _ensure_forced_start_node(template)
    nodes = template.nodes.select_related("question", "next_node", "yes_node", "no_node")
    return JsonResponse(
        {
            "template_id": template.id,
            "status": template.status,
            "start_node_id": template.start_node_id,
            "nodes": [_serialize_node(template, n) for n in nodes],
        }
    )


@staff_required
@require_POST
def template_node_create(request: HttpRequest, template_id: int) -> JsonResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    if _template_is_live(template):
        return JsonResponse({"ok": False, "error": "Template is live and locked."}, status=403)
    _ensure_forced_start_node(template)
    question_id = request.POST.get("question_id")
    x = int(request.POST.get("x", 80))
    y = int(request.POST.get("y", 80))
    question = get_object_or_404(Question, pk=question_id, is_archived=False)
    node = TemplateNode.objects.create(template=template, question=question, x=x, y=y)
    template.status = SurveyTemplate.Status.DRAFT
    if template.start_node_id is None:
        template.start_node = node
        template.save(update_fields=["start_node", "status", "updated_at"])
    else:
        template.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True, "node_id": node.id})


@staff_required
@require_POST
def template_node_update(request: HttpRequest, template_id: int, node_id: int) -> JsonResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    if _template_is_live(template):
        return JsonResponse({"ok": False, "error": "Template is live and locked."}, status=403)
    _ensure_forced_start_node(template)
    node = get_object_or_404(TemplateNode, pk=node_id, template=template)

    x = request.POST.get("x")
    y = request.POST.get("y")
    set_start = request.POST.get("set_start")
    next_id = request.POST.get("next_id")
    yes_id = request.POST.get("yes_id")
    no_id = request.POST.get("no_id")
    ends_survey = request.POST.get("ends_survey")
    end_on_yes = request.POST.get("end_on_yes")
    end_on_no = request.POST.get("end_on_no")

    if x is not None:
        node.x = int(x)
    if y is not None:
        node.y = int(y)

    try:
        if next_id is not None:
            node.next_node = _validate_target_node(template, next_id, source_node=node)
        if yes_id is not None:
            node.yes_node = _validate_target_node(template, yes_id, source_node=node)
        if no_id is not None:
            node.no_node = _validate_target_node(template, no_id, source_node=node)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    if ends_survey is not None:
        node.ends_survey = ends_survey == "true"
    if end_on_yes is not None:
        node.end_on_yes = end_on_yes == "true"
    if end_on_no is not None:
        node.end_on_no = end_on_no == "true"

    # Enforce consistent branching semantics on save.
    if node.question.question_type == Question.QuestionType.YES_NO:
        node.next_node = None
        node.ends_survey = False
    else:
        node.yes_node = None
        node.no_node = None
        node.end_on_yes = False
        node.end_on_no = False
    if node.is_forced_start:
        node.ends_survey = False

    node.save()
    template.status = SurveyTemplate.Status.DRAFT
    if set_start == "true":
        if node.is_forced_start:
            template.start_node = node
            template.save(update_fields=["start_node", "status", "updated_at"])
        else:
            forced_node = template.nodes.filter(is_forced_start=True).first()
            if forced_node:
                template.start_node = forced_node
                template.save(update_fields=["start_node", "status", "updated_at"])
            else:
                template.start_node = node
                template.save(update_fields=["start_node", "status", "updated_at"])
    else:
        forced_node = template.nodes.filter(is_forced_start=True).first()
        if forced_node and template.start_node_id != forced_node.id:
            template.start_node = forced_node
            template.save(update_fields=["start_node", "status", "updated_at"])
        else:
            template.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True})


@staff_required
@require_POST
def template_node_delete(request: HttpRequest, template_id: int, node_id: int) -> JsonResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    if _template_is_live(template):
        return JsonResponse({"ok": False, "error": "Template is live and locked."}, status=403)
    node = get_object_or_404(TemplateNode, pk=node_id, template=template)
    if node.is_forced_start:
        return JsonResponse({"ok": False, "error": "Forced start node cannot be deleted."}, status=400)

    if template.start_node_id == node.id:
        template.start_node = None
        template.status = SurveyTemplate.Status.DRAFT
        template.save(update_fields=["start_node", "status", "updated_at"])

    template.nodes.filter(next_node=node).update(next_node=None)
    template.nodes.filter(yes_node=node).update(yes_node=None)
    template.nodes.filter(no_node=node).update(no_node=None)
    node.delete()
    template.status = SurveyTemplate.Status.DRAFT

    if template.start_node_id is None:
        first_node = template.nodes.order_by("id").first()
        if first_node:
            template.start_node = first_node
            template.save(update_fields=["start_node", "status", "updated_at"])
        else:
            template.save(update_fields=["status", "updated_at"])
    else:
        template.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True})


@staff_required
@require_POST
def template_check_errors(request: HttpRequest, template_id: int) -> JsonResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    if _template_is_live(template):
        return JsonResponse({"ok": False, "error": "Template is live and locked."}, status=403)
    _ensure_forced_start_node(template)
    errors = _validate_template_graph(template)
    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    return JsonResponse({"ok": True})


@staff_required
@require_POST
def template_save_ready(request: HttpRequest, template_id: int) -> JsonResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    if _template_is_live(template):
        return JsonResponse({"ok": False, "error": "Template is live and locked."}, status=403)
    _ensure_forced_start_node(template)
    errors = _validate_template_graph(template)
    if errors:
        template.status = SurveyTemplate.Status.DRAFT
        template.save(update_fields=["status", "updated_at"])
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    template.status = SurveyTemplate.Status.READY
    template.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True})


class SurveyByTokenView(View):
    template_name = "survey/fill_survey.html"

    def get_object(self, token):
        return get_object_or_404(
            SurveySession.objects.select_related("customer", "template", "current_node", "current_node__question")
            .prefetch_related("current_node__question__choices"),
            token=token,
        )

    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        session = self.get_object(token)
        just_opened = _touch_session_activity(session)
        if just_opened:
            _log_session_event(session, SurveySessionEvent.EventType.LINK_OPENED, request=request)
        if not session.is_link_active:
            return render(request, "survey/link_inactive.html", {"session": session}, status=403)
        if session.is_archived:
            return render(request, "survey/link_inactive.html", {"session": session}, status=403)
        wants_edit = request.GET.get("edit") == "1"
        if _is_session_completed(session) and not wants_edit:
            return redirect("survey-thanks", token=token)
        if wants_edit:
            _ensure_active_session(session)
            _log_session_event(session, SurveySessionEvent.EventType.SURVEY_REOPENED, request=request)
        # Existing token links should remain usable even if template later moves back to draft.
        start = _start_node_or_404(session.template, require_ready=False)

        if session.current_node is None:
            session.current_node = start
            session.save(update_fields=["current_node", "updated_at"])

        node = session.current_node
        _log_session_event(session, SurveySessionEvent.EventType.QUESTION_VIEWED, node=node, request=request)
        answer = SurveyAnswer.objects.filter(session=session, node=node).first()
        form = DynamicQuestionForm(node=node)
        form.fill_initial_from_answer(answer)

        return render(
            request, self.template_name, {"session": session, "node": node, "question": node.question, "form": form}
        )

    @transaction.atomic
    def post(self, request: HttpRequest, token: str) -> HttpResponse:
        session = self.get_object(token)
        just_opened = _touch_session_activity(session)
        if just_opened:
            _log_session_event(session, SurveySessionEvent.EventType.LINK_OPENED, request=request)
        if not session.is_link_active:
            return render(request, "survey/link_inactive.html", {"session": session}, status=403)
        if session.is_archived:
            return render(request, "survey/link_inactive.html", {"session": session}, status=403)
        wants_edit = request.GET.get("edit") == "1"
        if _is_session_completed(session) and not wants_edit:
            return redirect("survey-thanks", token=token)
        _ensure_active_session(session)

        if session.current_node is None:
            return redirect("survey-thanks", token=token)

        node = session.current_node
        form = DynamicQuestionForm(node=node, data=request.POST)
        if not form.is_valid():
            return render(
                request, self.template_name, {"session": session, "node": node, "question": node.question, "form": form}
            )

        answer = _build_or_get_answer(session, node)
        value = form.get_answer_payload()
        _persist_answer(answer, node, value)
        _log_session_event(
            session,
            SurveySessionEvent.EventType.ANSWER_SAVED,
            node=node,
            request=request,
            details={"question_id": node.question_id},
        )

        next_node = _resolve_next_node(node, value)
        if next_node is None:
            if session.first_saved_at is not None:
                session.mark_saved_again()
                session.current_node = None
                session.save(
                    update_fields=[
                        "status",
                        "saved_again_count",
                        "last_saved_again_at",
                        "submitted_at",
                        "current_node",
                        "updated_at",
                    ]
                )
                _capture_submission_snapshot(session)
            else:
                session.mark_closed()
                if session.first_saved_at is None:
                    session.first_saved_at = session.submitted_at
                session.save(
                    update_fields=["status", "first_saved_at", "submitted_at", "current_node", "updated_at"]
                )
                _capture_submission_snapshot(session)
            _log_session_event(session, SurveySessionEvent.EventType.SURVEY_SUBMITTED, node=node, request=request)
            return redirect("survey-thanks", token=token)

        session.current_node = next_node
        session.save(update_fields=["current_node", "updated_at"])
        return redirect(reverse("survey-by-token", kwargs={"token": token}))


def survey_saved(request: HttpRequest, token: str) -> HttpResponse:
    session = get_object_or_404(SurveySession.objects.select_related("customer"), token=token)
    if session.is_archived or not session.is_link_active:
        return render(request, "survey/link_inactive.html", {"session": session}, status=403)
    return render(request, "survey/saved.html", {"session": session})


def survey_thanks(request: HttpRequest, token: str) -> HttpResponse:
    session = get_object_or_404(SurveySession.objects.select_related("customer"), token=token)
    if session.is_archived or not session.is_link_active:
        return render(request, "survey/link_inactive.html", {"session": session}, status=403)
    return render(request, "survey/thanks.html", {"session": session})

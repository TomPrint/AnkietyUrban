import json
import json
import uuid
import csv
from decimal import Decimal
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import BooleanField, Case, Count, Max, Q, Value, When
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
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

SYSTEM_START_QUESTION_TITLE = "Dane podmiotu i osoby kontaktowej"
SYSTEM_START_QUESTION_HELP = "Uzupelnij wszystkie pola kontaktowe przed rozpoczeciem ankiety."
SYSTEM_START_COMPLEX_ITEMS = [
    {
        "type": Question.QuestionType.OPEN,
        "label": "Nazwa Firmy",
        "options": [],
        "placeholder": "Nazwa firmy",
        "input_kind": "text",
        "required": True,
    },
    {
        "type": Question.QuestionType.MULTI_CHOICE,
        "label": "Forma Organizacyjna",
        "options": [
            "Spoldzielnia Mieszkaniowa",
            "Wspolnota Mieszkaniowa",
            "Firma Zarzadzajaca Nieruchomosciami",
            "Inna",
        ],
        "required": True,
    },
    {
        "type": Question.QuestionType.OPEN,
        "label": "Osoba kontaktowa",
        "options": [],
        "placeholder": "Imie Nazwisko",
        "input_kind": "text",
        "required": True,
    },
    {
        "type": Question.QuestionType.OPEN,
        "label": "Telefon Kontaktowy",
        "options": [],
        "placeholder": "+48 600 000 000",
        "input_kind": "phone",
        "required": True,
    },
    {
        "type": Question.QuestionType.OPEN,
        "label": "Email",
        "options": [],
        "placeholder": "kontakt@firma.pl",
        "input_kind": "email",
        "required": True,
    },
    {
        "type": Question.QuestionType.OPEN,
        "label": "Strona WWW",
        "options": [],
        "placeholder": "https://twojafirma.pl",
        "input_kind": "text",
        "required": False,
    },
]
staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_staff)


def _start_node(template: SurveyTemplate):
    return template.start_node or template.nodes.order_by("id").first()


def _template_is_live(template: SurveyTemplate) -> bool:
    return template.survey_sessions.filter(is_archived=False).exists()


def _normalize_live_templates_to_ready():
    SurveyTemplate.objects.filter(
        status=SurveyTemplate.Status.DRAFT,
        survey_sessions__is_archived=False,
    ).update(status=SurveyTemplate.Status.READY)


def _get_or_create_system_start_question() -> Question:
    question = Question.objects.filter(is_system=True).order_by("id").first()
    if question is None:
        question = Question.objects.create(
            title=SYSTEM_START_QUESTION_TITLE,
            question_type=Question.QuestionType.COMPLEX,
            help_text=SYSTEM_START_QUESTION_HELP,
            complex_items=SYSTEM_START_COMPLEX_ITEMS,
            required=True,
            is_system=True,
        )
        return question

    updates = []
    if question.title != SYSTEM_START_QUESTION_TITLE:
        question.title = SYSTEM_START_QUESTION_TITLE
        updates.append("title")
    if question.question_type != Question.QuestionType.COMPLEX:
        question.question_type = Question.QuestionType.COMPLEX
        updates.append("question_type")
    if question.help_text != SYSTEM_START_QUESTION_HELP:
        question.help_text = SYSTEM_START_QUESTION_HELP
        updates.append("help_text")
    if question.complex_items != SYSTEM_START_COMPLEX_ITEMS:
        question.complex_items = SYSTEM_START_COMPLEX_ITEMS
        updates.append("complex_items")
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
        if n.question.is_finishing and n.question.question_type == Question.QuestionType.YES_NO:
            errors.append(
                f"Node #{n.id}: finishing question cannot be Yes / No. Use Yes / No (no condition)."
            )
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
        if n.question.is_finishing:
            if n.yes_node_id or n.no_node_id or n.end_on_yes or n.end_on_no:
                errors.append(f"Node #{n.id}: finishing node can only use NEXT path.")
            if n.next_node_id:
                next_node = node_map.get(n.next_node_id)
                if next_node and not next_node.question.is_finishing:
                    errors.append(f"Node #{n.id}: finishing node NEXT can point only to finishing node.")

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


def _resolve_previous_node(session: SurveySession, current_node: TemplateNode) -> TemplateNode | None:
    template = session.template
    start = _start_node(template)
    if not start:
        return None
    if current_node.id == start.id:
        return None

    nodes = {n.id: n for n in template.nodes.select_related("question").all()}
    answers_by_node = {a.node_id: a for a in session.answers.only("node_id", "yes_no_answer")}
    seen = set()
    prev_id = None
    cursor_id = start.id

    while cursor_id and cursor_id in nodes and cursor_id not in seen:
        if cursor_id == current_node.id:
            return nodes.get(prev_id) if prev_id else None
        seen.add(cursor_id)
        node = nodes[cursor_id]
        answer = answers_by_node.get(cursor_id)
        if not answer:
            break

        next_id = None
        if node.question.question_type == Question.QuestionType.YES_NO:
            if answer.yes_no_answer is None:
                break
            if answer.yes_no_answer is True:
                if node.end_on_yes:
                    break
                next_id = node.yes_node_id
            else:
                if node.end_on_no:
                    break
                next_id = node.no_node_id
        else:
            if node.ends_survey:
                break
            next_id = node.next_node_id

        prev_id = cursor_id
        cursor_id = next_id
    return None


def _start_node_or_404(template: SurveyTemplate, *, require_ready: bool = True):
    _ensure_forced_start_node(template)
    if require_ready and template.status != SurveyTemplate.Status.READY:
        raise Http404("Template is not survey-ready.")
    start = _start_node(template)
    if not start:
        raise Http404("Template has no nodes.")
    return start


def _template_demo_session_key(template_id: int) -> str:
    return f"template_demo_state_{template_id}"


def _json_safe_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_json_safe_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe_value(v) for k, v in value.items()}
    return value


def _apply_demo_answer_initial(form: DynamicQuestionForm, node: TemplateNode, stored_value):
    if node.question.question_type == Question.QuestionType.COMPLEX and isinstance(stored_value, list):
        for idx, item in enumerate(stored_value):
            field_name = f"complex_{idx}"
            if field_name not in form.fields:
                continue
            value = item.get("value")
            if str(item.get("type", "")).strip().lower() == Question.QuestionType.OPEN_NUMBER_LIST and not isinstance(value, str):
                value = json.dumps(value or [], ensure_ascii=False)
            form.initial[field_name] = value
        return
    form.initial["answer"] = stored_value


def _validate_target_node(template: SurveyTemplate, node_id: str | None, source_node: TemplateNode | None = None):
    if node_id in (None, ""):
        return None
    target = get_object_or_404(TemplateNode, pk=node_id, template=template)
    if target.is_forced_start:
        raise Http404("Cannot link to forced start node.")
    if source_node is not None and source_node.question.is_finishing and not target.question.is_finishing:
        raise ValueError("Finishing node can connect only to another finishing node.")
    incoming_qs = template.nodes.filter(
        Q(next_node=target) | Q(yes_node=target) | Q(no_node=target)
    )
    if source_node is not None:
        incoming_qs = incoming_qs.exclude(pk=source_node.pk)
    if (not target.question.is_finishing) and incoming_qs.exists():
        raise ValueError(f"Node #{target.id} already has an incoming connection.")
    return target


def _serialize_node(template: SurveyTemplate, n: TemplateNode):
    return {
        "id": n.id,
        "question_id": n.question_id,
        "question_title": n.question.title,
        "question_type": n.question.question_type,
        "is_finishing_question": bool(n.question.is_finishing),
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


def _session_customer_name(session: SurveySession) -> str:
    return (session.customer_company_name_snapshot or "").strip() or session.customer.company_name


def _session_customer_address(session: SurveySession) -> str:
    return (session.customer_address_snapshot or "").strip() or (session.customer.address or "")


def _session_template_name(session: SurveySession) -> str:
    return (session.template_name_snapshot or "").strip() or session.template.name


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
    if q_type in (Question.QuestionType.YES_NO, Question.QuestionType.YES_NO_NEXT):
        answer.yes_no_answer = value == "yes"
        answer.save(update_fields=["yes_no_answer", "updated_at"])
        return
    if q_type == Question.QuestionType.MULTI_CHOICE:
        answer.save(update_fields=["updated_at"])
        if value:
            answer.selected_choices.set(value)
        return
    if q_type == Question.QuestionType.MULTI_ONE:
        answer.save(update_fields=["updated_at"])
        if value:
            answer.selected_choices.set([value])
        return
    if q_type == Question.QuestionType.COMPLEX:
        answer.complex_answer = _json_safe_value(value or [])
        answer.save(update_fields=["complex_answer", "updated_at"])
        return
    answer.open_answer = "" if value is None else str(value)
    answer.save(update_fields=["open_answer", "updated_at"])


def _answer_value_display(answer: SurveyAnswer) -> str:
    q_type = answer.question.question_type
    if q_type in (Question.QuestionType.YES_NO, Question.QuestionType.YES_NO_NEXT):
        if answer.yes_no_answer is True:
            return "Yes"
        if answer.yes_no_answer is False:
            return "No"
        return "-"
    if q_type == Question.QuestionType.MULTI_CHOICE:
        labels = list(answer.selected_choices.values_list("label", flat=True))
        return ", ".join(labels) if labels else "-"
    if q_type == Question.QuestionType.MULTI_ONE:
        label = answer.selected_choices.values_list("label", flat=True).first()
        return label or "-"
    if q_type == Question.QuestionType.OPEN_NUMBER_LIST:
        raw = (answer.open_answer or "").strip()
        if not raw:
            return "-"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if not isinstance(payload, list):
            return raw
        rendered = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            text = str(item.get("option", "")).strip()
            number = str(item.get("number", "")).strip()
            if not text and not number:
                continue
            rendered.append(f"{text}: {number}")
        return " | ".join(rendered) if rendered else "-"
    if q_type == Question.QuestionType.COMPLEX:
        items = answer.complex_answer or []
        if not items:
            return "-"
        rendered = []
        for item in items:
            label = item.get("label", "Item")
            value = item.get("value")
            item_type = item.get("type")
            if item_type == Question.QuestionType.MULTI_ONE:
                options = item.get("options", [])
                selected_labels = []
                value_iter = value if isinstance(value, list) else [value]
                for v in value_iter:
                    try:
                        index = int(v)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= index < len(options):
                        selected_labels.append(options[index])
                value_text = ", ".join(selected_labels) if selected_labels else "-"
            elif item_type == Question.QuestionType.OPEN_NUMBER_LIST:
                parsed_rows = []
                if isinstance(value, str):
                    try:
                        parsed = json.loads(value or "[]")
                    except json.JSONDecodeError:
                        parsed = []
                    if isinstance(parsed, list):
                        parsed_rows = parsed
                elif isinstance(value, list):
                    parsed_rows = value
                row_texts = []
                for row in parsed_rows:
                    if not isinstance(row, dict):
                        continue
                    row_name = str(row.get("option", "")).strip()
                    row_number = str(row.get("number", "")).strip()
                    if not row_name and not row_number:
                        continue
                    row_texts.append(f"{row_name}: {row_number}")
                value_text = " | ".join(row_texts) if row_texts else "-"
            elif item_type == Question.QuestionType.OPEN_WITH_LIST:
                parsed_rows = []
                if isinstance(value, str):
                    try:
                        parsed = json.loads(value or "[]")
                    except json.JSONDecodeError:
                        parsed = []
                    if isinstance(parsed, list):
                        parsed_rows = parsed
                elif isinstance(value, list):
                    parsed_rows = value
                row_texts = []
                for row in parsed_rows:
                    if not isinstance(row, dict):
                        continue
                    row_prefix = str(row.get("prefix", "")).strip()
                    row_text = str(row.get("text", "")).strip()
                    if not row_prefix and not row_text:
                        continue
                    row_texts.append(f"{row_prefix} {row_text}".strip())
                value_text = " | ".join(row_texts) if row_texts else "-"
            elif isinstance(value, list):
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


def _session_branch_completion_percent(session: SurveySession) -> int:
    template = session.template
    start = _start_node(template)
    if not start:
        return 0

    nodes = {
        n.id: n
        for n in template.nodes.select_related("question").all()
    }
    answers_by_node = {
        a.node_id: a
        for a in session.answers.only("node_id", "yes_no_answer")
    }

    path_node_ids = []
    answered_on_path = 0
    seen = set()
    current_id = start.id
    stop_at_current_node_id = session.current_node_id if not _is_session_completed(session) else None

    while current_id and current_id in nodes and current_id not in seen:
        seen.add(current_id)
        path_node_ids.append(current_id)
        node = nodes[current_id]
        if stop_at_current_node_id and current_id == stop_at_current_node_id:
            break
        answer = answers_by_node.get(current_id)
        if not answer:
            break
        answered_on_path += 1

        if node.question.question_type == Question.QuestionType.YES_NO:
            if answer.yes_no_answer is None:
                break
            if answer.yes_no_answer is True:
                if node.end_on_yes:
                    break
                current_id = node.yes_node_id
            else:
                if node.end_on_no:
                    break
                current_id = node.no_node_id
            continue

        if node.ends_survey:
            break
        current_id = node.next_node_id

    if not path_node_ids:
        return 0
    return int((answered_on_path / len(path_node_ids)) * 100)


def _node_can_end_survey(node: TemplateNode) -> bool:
    if node.question.question_type == Question.QuestionType.YES_NO:
        return bool(node.end_on_yes or node.end_on_no)
    return bool(node.ends_survey)


def _consent_state_from_session(session: SurveySession):
    return {
        "consent_personal_data": bool(session.consent_personal_data),
        "consent_data_administration": bool(session.consent_data_administration),
        "consent_contact_results": bool(session.consent_contact_results),
        "consent_marketing": bool(session.consent_marketing),
    }


def _consent_state_from_post(request: HttpRequest):
    return {
        "consent_personal_data": request.POST.get("consent_personal_data") == "on",
        "consent_data_administration": request.POST.get("consent_data_administration") == "on",
        "consent_contact_results": request.POST.get("consent_contact_results") == "on",
        "consent_marketing": request.POST.get("consent_marketing") == "on",
    }


@staff_required
def management_dashboard(request: HttpRequest) -> HttpResponse:
    generated_links_count = SurveySession.objects.filter(is_archived=False).count()
    sessions_open = SurveySession.objects.filter(status=SurveySession.Status.OPEN, is_archived=False).count()
    sessions_closed = (
        SurveySession.objects.filter(
            status__in=[SurveySession.Status.CLOSED, SurveySession.Status.SAVED_AGAIN],
            is_archived=False,
        )
        .values("id")
        .distinct()
        .count()
    )
    open_percent = round((sessions_open / generated_links_count) * 100, 1) if generated_links_count else 0
    closed_percent = round((sessions_closed / generated_links_count) * 100, 1) if generated_links_count else 0
    context = {
        "customers_count": Customer.objects.filter(is_archived=False).count(),
        "questions_count": Question.objects.filter(is_system=False, is_archived=False).count(),
        "templates_count": SurveyTemplate.objects.filter(is_archived=False).count(),
        "sessions_open": sessions_open,
        "generated_links_count": generated_links_count,
        "sessions_closed": sessions_closed,
        "sessions_open_percent": open_percent,
        "sessions_closed_percent": closed_percent,
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
    customers = Customer.objects.filter(is_archived=False).annotate(
        survays_count=Count(
            "survey_sessions",
            filter=Q(survey_sessions__is_archived=False),
        )
    )
    return render(request, "management/customers/list.html", {"customers": customers})


@staff_required
def customer_detail(request: HttpRequest, customer_id: int) -> HttpResponse:
    customer = get_object_or_404(Customer, pk=customer_id, is_archived=False)
    sessions = (
        SurveySession.objects.select_related("template")
        .filter(customer=customer, is_archived=False)
        .order_by("-updated_at")
    )

    session_rows = []
    for session in sessions:
        session_rows.append(
            {
                "session": session,
                "score_percent": _session_branch_completion_percent(session),
                "saved_versions_count": session.snapshots.count(),
            }
        )

    context = {
        "customer": customer,
        "session_rows": session_rows,
    }
    return render(request, "management/customers/detail.html", context)


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
        session.customer_company_name_snapshot = session.customer.company_name
        session.customer_address_snapshot = session.customer.address or ""
        session.template_name_snapshot = session.template.name
        session.status = SurveySession.Status.OPEN
        session.current_node = _start_node(session.template)
        session.save()
        return redirect("management-assignments")

    sort_map = {
        "customer": "customer__company_name",
        "template": "template__name",
        "created_by": "created_by_name",
        "status": "status",
        "int_ext": "is_internal",
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
    is_completed = session.status in (SurveySession.Status.CLOSED, SurveySession.Status.SAVED_AGAIN)
    completion_percent = _session_branch_completion_percent(session)
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
@require_GET
def survey_snapshot_csv(request: HttpRequest, session_id: int, snapshot_id: int) -> HttpResponse:
    snapshot = get_object_or_404(
        SurveySubmissionSnapshot.objects.select_related("session", "session__customer", "session__template"),
        pk=snapshot_id,
        session_id=session_id,
        session__is_archived=False,
    )
    session = snapshot.session
    company_part = slugify(_session_customer_name(session)) or "customer"
    template_part = slugify(_session_template_name(session)) or "template"
    filename = f"{company_part}_{template_part}_session_{session.id}_version_{snapshot.version_number}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response, delimiter=";")
    writer.writerow(["Session ID", session.id])
    writer.writerow(["Customer", _session_customer_name(session)])
    writer.writerow(["Template", _session_template_name(session)])
    writer.writerow(["Version", snapshot.version_number])
    writer.writerow(["Saved At", snapshot.saved_at])
    writer.writerow(["Status", snapshot.status or ""])
    writer.writerow([])
    writer.writerow(["Node", "Question", "Answer"])
    for item in snapshot.answers or []:
        writer.writerow(
            [
                item.get("node_id", ""),
                item.get("question_title", ""),
                item.get("value", ""),
            ]
        )
    return response


@staff_required
def question_list(request: HttpRequest) -> HttpResponse:
    search_query = request.GET.get("q", "").strip()
    sort_map = {
        "title": "title",
        "type": "question_type",
        "special": "is_finishing",
        "link": "has_link",
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
        .annotate(
            choices_count=Count("choices"),
            has_link=Case(
                When(source_url="", then=Value(False)),
                default=Value(True),
                output_field=BooleanField(),
            ),
        )
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
        {
            "questions": questions,
            "q": search_query,
            "sort": sort,
            "dir": direction,
        },
    )


@staff_required
def question_create(request: HttpRequest) -> HttpResponse:
    form = QuestionManageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("management-questions")
    return render(request, "management/questions/form.html", {"form": form, "title": "Create Question"})


def _detach_question_for_live_templates(question: Question) -> bool:
    live_node_ids = list(
        TemplateNode.objects.filter(
            question=question,
            template__survey_sessions__isnull=False,
        )
        .values_list("id", flat=True)
        .distinct()
    )
    if not live_node_ids:
        return False

    frozen = Question.objects.create(
        title=question.title,
        question_type=question.question_type,
        help_text=question.help_text,
        source_url=question.source_url,
        promotional_text=question.promotional_text,
        complex_items=question.complex_items,
        required=question.required,
        is_system=False,
        is_finishing=question.is_finishing,
        is_archived=True,
        archived_at=timezone.now(),
    )
    choices = list(question.choices.order_by("order", "id").values_list("label", "order"))
    if choices:
        from .models import QuestionChoice  # local import to avoid circulars in tooling

        QuestionChoice.objects.bulk_create(
            [QuestionChoice(question=frozen, label=label, order=order) for label, order in choices]
        )
    TemplateNode.objects.filter(pk__in=live_node_ids).update(question=frozen)
    return True


@staff_required
def question_edit(request: HttpRequest, question_id: int) -> HttpResponse:
    question = get_object_or_404(Question, pk=question_id, is_system=False, is_archived=False)
    form = QuestionManageForm(request.POST or None, instance=question)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            _detach_question_for_live_templates(question)
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
    with transaction.atomic():
        _detach_question_for_live_templates(question)
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
            live_sessions_count=Count("survey_sessions", filter=Q(survey_sessions__is_archived=False), distinct=True),
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
        .annotate(live_sessions_count=Count("survey_sessions", filter=Q(survey_sessions__is_archived=False)))
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
def template_demo(request: HttpRequest, template_id: int) -> HttpResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    _ensure_forced_start_node(template)
    start = _start_node_or_404(template, require_ready=True)
    state_key = _template_demo_session_key(template.id)

    if request.GET.get("restart") == "1":
        request.session.pop(state_key, None)

    state = request.session.get(state_key, {})
    current_node_id = state.get("current_node_id") or start.id
    node = get_object_or_404(
        template.nodes.select_related("question").prefetch_related("question__choices"),
        pk=current_node_id,
    )

    if request.method == "POST":
        action = request.POST.get("action", "next")
        if action == "prev":
            path = list(state.get("path", []))
            if path:
                state["current_node_id"] = path.pop()
                state["path"] = path
                request.session[state_key] = state
                request.session.modified = True
            return redirect("management-template-demo", template_id=template.id)

        form = DynamicQuestionForm(node=node, data=request.POST)
        if form.is_valid():
            value = form.get_answer_payload()
            answers = dict(state.get("answers", {}))
            answers[str(node.id)] = _json_safe_value(value)
            next_node = _resolve_next_node(node, value)
            if next_node is None:
                request.session.pop(state_key, None)
                return redirect("management-template-demo-done", template_id=template.id)

            path = list(state.get("path", []))
            if not path or path[-1] != node.id:
                path.append(node.id)
            state = {
                "current_node_id": next_node.id,
                "path": path,
                "answers": answers,
            }
            request.session[state_key] = state
            request.session.modified = True
            return redirect("management-template-demo", template_id=template.id)
    else:
        form = DynamicQuestionForm(node=node)
        stored = state.get("answers", {}).get(str(node.id))
        if stored is not None:
            _apply_demo_answer_initial(form, node, stored)

    has_previous_node = bool(state.get("path"))
    demo_customer = SimpleNamespace(company_name=f"DEMO: {template.name}")
    demo_session = SimpleNamespace(
        customer=demo_customer,
        get_status_display=lambda: "Demo",
    )
    context = {
        "session": demo_session,
        "node": node,
        "question": node.question,
        "form": form,
        "consent_required_on_this_node": False,
        "consent_error": "",
        "consent_state": {
            "consent_personal_data": False,
            "consent_data_administration": False,
            "consent_contact_results": False,
            "consent_marketing": False,
        },
        "gdpr_admin_name": "Demo",
        "gdpr_admin_city": "Demo",
        "has_previous_node": has_previous_node,
        "session_customer_name": f"DEMO: {template.name}",
    }
    return render(request, "survey/fill_survey.html", context)


@staff_required
def template_demo_done(request: HttpRequest, template_id: int) -> HttpResponse:
    template = get_object_or_404(SurveyTemplate, pk=template_id, is_archived=False)
    return render(request, "management/templates/demo_done.html", {"template": template})


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
    is_decision_yes_no = node.question.question_type == Question.QuestionType.YES_NO and not node.question.is_finishing
    if is_decision_yes_no:
        node.next_node = None
        node.ends_survey = False
    else:
        node.yes_node = None
        node.no_node = None
        node.end_on_yes = False
        node.end_on_no = False
        if node.next_node_id is not None:
            node.ends_survey = False
    if node.is_forced_start:
        node.ends_survey = False
    if node.question.is_finishing:
        if node.question.question_type == Question.QuestionType.YES_NO:
            return JsonResponse(
                {"ok": False, "error": "Finishing question cannot use Yes / No. Use Yes / No (no condition)."},
                status=400,
            )
        if node.next_node and not node.next_node.question.is_finishing:
            return JsonResponse(
                {"ok": False, "error": "Finishing node can connect only to another finishing node."},
                status=400,
            )

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

    def _render_form(
        self,
        request: HttpRequest,
        *,
        session: SurveySession,
        node: TemplateNode,
        form: DynamicQuestionForm,
        consent_error: str = "",
        consent_state: dict | None = None,
    ) -> HttpResponse:
        admin_name = _session_customer_name(session) or "[NAZWA FIRMY]"
        admin_city = _session_customer_address(session) or "[miasto]"
        if consent_state is None:
            consent_state = _consent_state_from_session(session)
        previous_node = _resolve_previous_node(session, node)
        return render(
            request,
            self.template_name,
            {
                "session": session,
                "node": node,
                "question": node.question,
                "form": form,
                "consent_required_on_this_node": _node_can_end_survey(node),
                "consent_error": consent_error,
                "consent_state": consent_state,
                "gdpr_admin_name": admin_name,
                "gdpr_admin_city": admin_city,
                "has_previous_node": previous_node is not None,
                "session_customer_name": _session_customer_name(session),
            },
        )

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
            return render(
                request,
                "survey/link_inactive.html",
                {"session": session, "session_customer_name": _session_customer_name(session)},
                status=403,
            )
        if session.is_archived:
            return render(
                request,
                "survey/link_inactive.html",
                {"session": session, "session_customer_name": _session_customer_name(session)},
                status=403,
            )
        wants_edit = request.GET.get("edit") == "1"
        if _is_session_completed(session) and not wants_edit:
            return redirect("survey-thanks", token=token)
        if wants_edit and _is_session_completed(session):
            session.mark_reopened()
            session.current_node = _start_node(session.template)
            session.save(
                update_fields=[
                    "status",
                    "reopened_count",
                    "last_reopened_at",
                    "submitted_at",
                    "current_node",
                    "updated_at",
                ]
            )
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
        return self._render_form(request, session=session, node=node, form=form)

    @transaction.atomic
    def post(self, request: HttpRequest, token: str) -> HttpResponse:
        session = self.get_object(token)
        just_opened = _touch_session_activity(session)
        if just_opened:
            _log_session_event(session, SurveySessionEvent.EventType.LINK_OPENED, request=request)
        if not session.is_link_active:
            return render(
                request,
                "survey/link_inactive.html",
                {"session": session, "session_customer_name": _session_customer_name(session)},
                status=403,
            )
        if session.is_archived:
            return render(
                request,
                "survey/link_inactive.html",
                {"session": session, "session_customer_name": _session_customer_name(session)},
                status=403,
            )
        wants_edit = request.GET.get("edit") == "1"
        if _is_session_completed(session) and not wants_edit:
            return redirect("survey-thanks", token=token)
        _ensure_active_session(session)

        if session.current_node is None:
            return redirect("survey-thanks", token=token)

        node = session.current_node
        action = request.POST.get("action", "next")
        if action == "prev":
            previous_node = _resolve_previous_node(session, node)
            if previous_node is not None:
                session.current_node = previous_node
                session.save(update_fields=["current_node", "updated_at"])
            return redirect(reverse("survey-by-token", kwargs={"token": token}))

        form = DynamicQuestionForm(node=node, data=request.POST)
        if not form.is_valid():
            return self._render_form(request, session=session, node=node, form=form)

        value = form.get_answer_payload()
        next_node = _resolve_next_node(node, value)
        if next_node is None:
            consent_state = _consent_state_from_post(request)
            if not (
                consent_state["consent_personal_data"]
                and consent_state["consent_data_administration"]
                and consent_state["consent_contact_results"]
            ):
                return self._render_form(
                    request,
                    session=session,
                    node=node,
                    form=form,
                    consent_error="Przed zapisaniem ankiety należy zaakceptować wymagane zgody.",
                    consent_state=consent_state,
                )

        answer = _build_or_get_answer(session, node)
        _persist_answer(answer, node, value)
        _log_session_event(
            session,
            SurveySessionEvent.EventType.ANSWER_SAVED,
            node=node,
            request=request,
            details={"question_id": node.question_id},
        )

        if next_node is None:
            consent_state = _consent_state_from_post(request)
            session.consent_personal_data = consent_state["consent_personal_data"]
            session.consent_data_administration = consent_state["consent_data_administration"]
            session.consent_contact_results = consent_state["consent_contact_results"]
            session.consent_marketing = consent_state["consent_marketing"]
            session.consent_submitted_at = timezone.now()
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
                        "consent_personal_data",
                        "consent_data_administration",
                        "consent_contact_results",
                        "consent_marketing",
                        "consent_submitted_at",
                        "updated_at",
                    ]
                )
                _capture_submission_snapshot(session)
            else:
                session.mark_closed()
                if session.first_saved_at is None:
                    session.first_saved_at = session.submitted_at
                session.save(
                    update_fields=[
                        "status",
                        "first_saved_at",
                        "submitted_at",
                        "current_node",
                        "consent_personal_data",
                        "consent_data_administration",
                        "consent_contact_results",
                        "consent_marketing",
                        "consent_submitted_at",
                        "updated_at",
                    ]
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
        return render(
            request,
            "survey/link_inactive.html",
            {"session": session, "session_customer_name": _session_customer_name(session)},
            status=403,
        )
    return render(request, "survey/saved.html", {"session": session, "session_customer_name": _session_customer_name(session)})


def survey_thanks(request: HttpRequest, token: str) -> HttpResponse:
    session = get_object_or_404(SurveySession.objects.select_related("customer"), token=token)
    if session.is_archived or not session.is_link_active:
        return render(
            request,
            "survey/link_inactive.html",
            {"session": session, "session_customer_name": _session_customer_name(session)},
            status=403,
        )
    return render(request, "survey/thanks.html", {"session": session, "session_customer_name": _session_customer_name(session)})

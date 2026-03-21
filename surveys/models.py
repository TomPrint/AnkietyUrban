import uuid

from crm.models import Customer
from django.conf import settings
from django.db import models
from django.utils import timezone


class Question(models.Model):
    class QuestionType(models.TextChoices):
        YES_NO = "yes_no", "Yes / No"
        YES_NO_NEXT = "yes_no_next", "Yes / No (no condition)"
        MULTI_CHOICE = "multi_choice", "Multi-many"
        MULTI_ONE = "multi_one", "Multi-one"
        OPEN_WITH_LIST = "open_with_list", "Adress List"
        OPEN_NUMBER_LIST = "open_number_list", "Checkbox/Number"
        OPEN_NUMERIC = "open_numeric", "Numeric"
        OPEN = "open", "Open question"
        COMPLEX = "complex", "Complex"

    title = models.CharField(max_length=500)
    question_type = models.CharField(max_length=20, choices=QuestionType.choices)
    help_text = models.CharField(max_length=500, blank=True)
    source_url = models.URLField(blank=True)
    promotional_text = models.CharField(max_length=255, blank=True)
    complex_items = models.JSONField(default=list, blank=True)
    required = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    is_finishing = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def archive(self):
        self.is_archived = True
        self.archived_at = timezone.now()
        self.save(update_fields=["is_archived", "archived_at", "updated_at"])


class QuestionChoice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    label = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.label


class SurveyTemplate(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    finishing_question_ids = models.JSONField(default=list, blank=True)
    start_node = models.ForeignKey(
        "TemplateNode",
        on_delete=models.SET_NULL,
        related_name="start_for_templates",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def archive(self):
        self.is_archived = True
        self.archived_at = timezone.now()
        self.save(update_fields=["is_archived", "archived_at", "updated_at"])


class TemplateNode(models.Model):
    template = models.ForeignKey(SurveyTemplate, on_delete=models.CASCADE, related_name="nodes")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="template_nodes")
    title_override = models.CharField(max_length=500, blank=True)
    is_forced_start = models.BooleanField(default=False)
    is_finishing_injected = models.BooleanField(default=False)
    x = models.IntegerField(default=80)
    y = models.IntegerField(default=80)

    # For OPEN and MULTI nodes.
    next_node = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="incoming_next"
    )
    # For YES/NO nodes.
    yes_node = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="incoming_yes"
    )
    no_node = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="incoming_no"
    )
    ends_survey = models.BooleanField(default=False)
    end_on_yes = models.BooleanField(default=False)
    end_on_no = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.template.name} | {self.display_title}"

    @property
    def display_title(self):
        return self.title_override or self.question.title

    def save(self, *args, **kwargs):
        if self.question.question_type != Question.QuestionType.YES_NO:
            self.yes_node = None
            self.no_node = None
            self.end_on_yes = False
            self.end_on_no = False
        else:
            self.next_node = None
            self.ends_survey = False
        super().save(*args, **kwargs)


class SurveySession(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        REOPENED = "reopened", "Reopened"
        SAVED_AGAIN = "saved_again", "Saved Again"

    customer = models.ForeignKey("crm.Customer", on_delete=models.CASCADE, related_name="survey_sessions")
    template = models.ForeignKey(SurveyTemplate, on_delete=models.CASCADE, related_name="survey_sessions")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_survey_sessions",
    )
    created_by_name = models.CharField(max_length=150, blank=True, default="")
    customer_company_name_snapshot = models.CharField(max_length=255, blank=True, default="")
    customer_address_snapshot = models.CharField(max_length=500, blank=True, default="")
    template_name_snapshot = models.CharField(max_length=255, blank=True, default="")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_internal = models.BooleanField(default=False)
    is_link_active = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    reopened_count = models.PositiveIntegerField(default=0)
    saved_again_count = models.PositiveIntegerField(default=0)
    current_node = models.ForeignKey(
        TemplateNode, on_delete=models.SET_NULL, null=True, blank=True, related_name="active_sessions"
    )
    first_opened_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    active_seconds = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    first_saved_at = models.DateTimeField(null=True, blank=True)
    last_reopened_at = models.DateTimeField(null=True, blank=True)
    last_saved_again_at = models.DateTimeField(null=True, blank=True)
    consent_personal_data = models.BooleanField(default=False)
    consent_data_administration = models.BooleanField(default=False)
    consent_contact_results = models.BooleanField(default=False)
    consent_marketing = models.BooleanField(default=False)
    consent_submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.customer.company_name} | {self.template.name}"

    def mark_closed(self):
        self.status = self.Status.CLOSED
        self.submitted_at = timezone.now()
        self.current_node = None

    def mark_reopened(self):
        self.status = self.Status.REOPENED
        self.reopened_count += 1
        self.last_reopened_at = timezone.now()
        self.submitted_at = None

    def mark_open(self):
        self.status = self.Status.OPEN
        self.submitted_at = None

    def mark_saved_again(self):
        now = timezone.now()
        self.status = self.Status.SAVED_AGAIN
        self.saved_again_count += 1
        self.last_saved_again_at = now
        self.submitted_at = now

    def archive(self):
        self.is_archived = True
        self.archived_at = timezone.now()
        self.is_link_active = False
        self.save(update_fields=["is_archived", "archived_at", "is_link_active", "updated_at"])

    def restore_from_archive(self):
        self.is_archived = False
        self.archived_at = None
        self.is_link_active = False
        self.save(update_fields=["is_archived", "archived_at", "is_link_active", "updated_at"])


class ArchivedSurveySession(SurveySession):
    class Meta:
        proxy = True
        verbose_name = "Archived Survey"
        verbose_name_plural = "Archived Surveys"


class SurveyAnswer(models.Model):
    session = models.ForeignKey(SurveySession, on_delete=models.CASCADE, related_name="answers")
    node = models.ForeignKey(TemplateNode, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    yes_no_answer = models.BooleanField(null=True, blank=True)
    open_answer = models.TextField(blank=True)
    complex_answer = models.JSONField(default=list, blank=True)
    selected_choices = models.ManyToManyField(QuestionChoice, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "node"], name="unique_answer_per_session_node")
        ]

    def __str__(self):
        return f"{self.session} | {self.question.title[:35]}"


class SurveySubmissionSnapshot(models.Model):
    session = models.ForeignKey(SurveySession, on_delete=models.CASCADE, related_name="snapshots")
    version_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, blank=True)
    answers = models.JSONField(default=list)
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version_number", "-saved_at"]
        constraints = [
            models.UniqueConstraint(fields=["session", "version_number"], name="unique_snapshot_version_per_session")
        ]

    def __str__(self):
        return f"{self.session} | v{self.version_number}"


class SurveySessionEvent(models.Model):
    class EventType(models.TextChoices):
        LINK_OPENED = "link_opened", "Link opened"
        QUESTION_VIEWED = "question_viewed", "Question viewed"
        ANSWER_SAVED = "answer_saved", "Answer saved"
        SURVEY_SUBMITTED = "survey_submitted", "Survey submitted"
        SURVEY_REOPENED = "survey_reopened", "Survey reopened"

    session = models.ForeignKey(SurveySession, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    node = models.ForeignKey(TemplateNode, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.session} | {self.event_type} | {self.created_at}"

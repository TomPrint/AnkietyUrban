import uuid

from django.db import models
from django.utils import timezone


class Customer(models.Model):
    company_name = models.CharField(max_length=255)
    address = models.CharField(max_length=500, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["company_name"]

    def __str__(self):
        return self.company_name


class Question(models.Model):
    class QuestionType(models.TextChoices):
        YES_NO = "yes_no", "Yes / No"
        MULTI_CHOICE = "multi_choice", "Multi choice"
        OPEN = "open", "Open question"

    title = models.CharField(max_length=500)
    question_type = models.CharField(max_length=20, choices=QuestionType.choices)
    help_text = models.CharField(max_length=500, blank=True)
    required = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


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
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
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


class TemplateNode(models.Model):
    template = models.ForeignKey(SurveyTemplate, on_delete=models.CASCADE, related_name="nodes")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="template_nodes")
    title_override = models.CharField(max_length=500, blank=True)
    is_forced_start = models.BooleanField(default=False)
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

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="survey_sessions")
    template = models.ForeignKey(SurveyTemplate, on_delete=models.CASCADE, related_name="survey_sessions")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_link_active = models.BooleanField(default=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    reopened_count = models.PositiveIntegerField(default=0)
    saved_again_count = models.PositiveIntegerField(default=0)
    current_node = models.ForeignKey(
        TemplateNode, on_delete=models.SET_NULL, null=True, blank=True, related_name="active_sessions"
    )
    started_at = models.DateTimeField(auto_now_add=True)
    first_saved_at = models.DateTimeField(null=True, blank=True)
    last_reopened_at = models.DateTimeField(null=True, blank=True)
    last_saved_again_at = models.DateTimeField(null=True, blank=True)
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


class SurveyAnswer(models.Model):
    session = models.ForeignKey(SurveySession, on_delete=models.CASCADE, related_name="answers")
    node = models.ForeignKey(TemplateNode, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    yes_no_answer = models.BooleanField(null=True, blank=True)
    open_answer = models.TextField(blank=True)
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

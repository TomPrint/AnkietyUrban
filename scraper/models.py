from django.conf import settings
from django.db import models
from django.utils import timezone

from crm.models import Customer


class LeadCandidate(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Oczekuje"),
        (STATUS_APPROVED, "Zatwierdzony"),
        (STATUS_REJECTED, "Odrzucony"),
    ]

    source = models.CharField(max_length=50, default="gemini")
    company_name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, db_index=True)
    district = models.CharField(max_length=255, blank=True)
    address = models.CharField(max_length=500, blank=True)
    email = models.EmailField(blank=True)
    telephone = models.CharField(max_length=50, blank=True)
    reason = models.TextField(blank=True)
    website = models.URLField(blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    duplicate_customer = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lead_candidates",
    )
    duplicate_candidate = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicate_children",
    )
    approved_customer = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_from_leads",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_lead_candidates",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-confidence", "company_name"]

    def __str__(self):
        return self.company_name

    @property
    def has_duplicate(self):
        return bool(self.duplicate_customer_id or self.duplicate_candidate_id)

    def mark_rejected(self, user, reason=""):
        self.status = self.STATUS_REJECTED
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.rejection_reason = reason.strip()
        self.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "rejection_reason",
                "updated_at",
            ]
        )

    def mark_pending(self):
        self.status = self.STATUS_PENDING
        self.reviewed_by = None
        self.reviewed_at = None
        self.rejection_reason = ""
        self.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "rejection_reason",
                "updated_at",
            ]
        )

from django.contrib import admin

from .models import LeadCandidate


@admin.register(LeadCandidate)
class LeadCandidateAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "status",
        "confidence",
        "district",
        "duplicate_customer",
        "duplicate_candidate",
        "created_at",
    )
    list_filter = ("status", "source", "district")
    search_fields = ("company_name", "normalized_name", "district", "reason", "website")

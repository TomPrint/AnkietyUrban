from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone


def normalize_nip(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def validate_nip(value: str) -> None:
    if value and not (value.isdigit() and len(value) == 10):
        raise ValidationError("NIP musi składać się z 10 cyfr.")


class Customer(models.Model):
    company_name = models.CharField(max_length=255)
    district = models.CharField(max_length=255, blank=True)
    address = models.CharField(max_length=500, blank=True)
    website = models.URLField(blank=True)
    nip = models.CharField(max_length=10, blank=True, db_index=True, validators=[validate_nip])
    contact_person = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    telephone = models.CharField(max_length=50, blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["company_name"]
        db_table = "surveys_customer"

    def __str__(self):
        return self.company_name

    def archive(self):
        self.is_archived = True
        self.archived_at = timezone.now()
        self.save(update_fields=["is_archived", "archived_at"])

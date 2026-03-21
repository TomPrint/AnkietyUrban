from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("company_name", "contact_person", "email", "created_at")
    search_fields = ("company_name", "contact_person", "email")


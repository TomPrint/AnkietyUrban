from django.contrib import admin

from .models import (
    ArchivedSurveySession,
    Customer,
    Question,
    QuestionChoice,
    SurveyAnswer,
    SurveySession,
    SurveyTemplate,
    TemplateNode,
)


class QuestionChoiceInline(admin.TabularInline):
    model = QuestionChoice
    extra = 1


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("title", "question_type", "required", "updated_at")
    list_filter = ("question_type", "required")
    search_fields = ("title", "help_text")
    inlines = [QuestionChoiceInline]


@admin.register(SurveyTemplate)
class SurveyTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "start_node", "updated_at")
    search_fields = ("name",)


@admin.register(TemplateNode)
class TemplateNodeAdmin(admin.ModelAdmin):
    list_display = ("template", "question", "next_node", "yes_node", "no_node", "ends_survey", "end_on_yes", "end_on_no")
    list_filter = ("template", "question__question_type", "ends_survey", "end_on_yes", "end_on_no")
    search_fields = ("template__name", "question__title", "title_override")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("company_name", "contact_person", "email", "created_at")
    search_fields = ("company_name", "contact_person", "email")


@admin.register(SurveySession)
class SurveySessionAdmin(admin.ModelAdmin):
    list_display = ("customer", "template", "status", "saved_again_count", "is_link_active", "updated_at")
    list_filter = ("status", "template", "is_archived", "is_link_active")
    search_fields = ("customer__company_name", "token")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_archived=False)


@admin.register(ArchivedSurveySession)
class ArchivedSurveySessionAdmin(admin.ModelAdmin):
    list_display = ("customer", "template", "status", "saved_again_count", "is_link_active", "archived_at", "updated_at")
    list_filter = ("status", "template", "is_link_active", "archived_at")
    search_fields = ("customer__company_name", "token")
    actions = ("restore_selected_surveys",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_archived=True)

    @admin.action(description="Restore selected archived surveys (restored as deactivated)")
    def restore_selected_surveys(self, request, queryset):
        count = 0
        for session in queryset:
            session.restore_from_archive()
            count += 1
        self.message_user(
            request,
            f"Restored {count} survey session(s). Links are deactivated and must be activated from Management > Survey.",
        )


@admin.register(SurveyAnswer)
class SurveyAnswerAdmin(admin.ModelAdmin):
    list_display = ("session", "question", "updated_at")
    list_filter = ("question__question_type",)
    search_fields = ("session__customer__company_name", "question__title")

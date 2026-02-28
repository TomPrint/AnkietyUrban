from django.contrib import admin

from .models import (
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
    list_display = ("customer", "template", "status", "saved_again_count", "token", "updated_at")
    list_filter = ("status", "template")
    search_fields = ("customer__company_name", "token")


@admin.register(SurveyAnswer)
class SurveyAnswerAdmin(admin.ModelAdmin):
    list_display = ("session", "question", "updated_at")
    list_filter = ("question__question_type",)
    search_fields = ("session__customer__company_name", "question__title")

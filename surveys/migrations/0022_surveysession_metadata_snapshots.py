from django.db import migrations, models


def backfill_session_metadata(apps, schema_editor):
    SurveySession = apps.get_model("surveys", "SurveySession")
    for session in SurveySession.objects.select_related("customer", "template").all():
        update_fields = []
        if not (session.customer_company_name_snapshot or "").strip():
            session.customer_company_name_snapshot = session.customer.company_name
            update_fields.append("customer_company_name_snapshot")
        if not (session.customer_address_snapshot or "").strip():
            session.customer_address_snapshot = session.customer.address or ""
            update_fields.append("customer_address_snapshot")
        if not (session.template_name_snapshot or "").strip():
            session.template_name_snapshot = session.template.name
            update_fields.append("template_name_snapshot")
        if update_fields:
            session.save(update_fields=update_fields)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("surveys", "0021_alter_question_question_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="surveysession",
            name="customer_address_snapshot",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="surveysession",
            name="customer_company_name_snapshot",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="surveysession",
            name="template_name_snapshot",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(backfill_session_metadata, noop_reverse),
    ]

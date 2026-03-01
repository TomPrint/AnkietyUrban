from django.db import migrations


def force_all_questions_required(apps, schema_editor):
    Question = apps.get_model("surveys", "Question")
    Question.objects.filter(required=False).update(required=True)


class Migration(migrations.Migration):
    dependencies = [
        ("surveys", "0010_question_source_url"),
    ]

    operations = [
        migrations.RunPython(force_all_questions_required, migrations.RunPython.noop),
    ]

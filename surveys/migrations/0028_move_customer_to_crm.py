from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0001_initial"),
        ("surveys", "0027_alter_question_question_type_numeric_label"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="Customer"),
                migrations.AlterField(
                    model_name="surveysession",
                    name="customer",
                    field=models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="survey_sessions", to="crm.customer"),
                ),
            ],
        ),
    ]

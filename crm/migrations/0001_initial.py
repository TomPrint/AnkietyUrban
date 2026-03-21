from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("surveys", "0027_alter_question_question_type_numeric_label"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="Customer",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("company_name", models.CharField(max_length=255)),
                        ("address", models.CharField(blank=True, max_length=500)),
                        ("contact_person", models.CharField(blank=True, max_length=255)),
                        ("email", models.EmailField(blank=True, max_length=254)),
                        ("telephone", models.CharField(blank=True, max_length=50)),
                        ("is_archived", models.BooleanField(db_index=True, default=False)),
                        ("archived_at", models.DateTimeField(blank=True, null=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                    ],
                    options={
                        "ordering": ["company_name"],
                        "db_table": "surveys_customer",
                    },
                ),
            ],
        ),
    ]


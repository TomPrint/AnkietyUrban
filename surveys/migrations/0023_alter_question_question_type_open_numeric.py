from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("surveys", "0022_surveysession_metadata_snapshots"),
    ]

    operations = [
        migrations.AlterField(
            model_name="question",
            name="question_type",
            field=models.CharField(
                choices=[
                    ("yes_no", "Yes / No"),
                    ("yes_no_next", "Yes / No (no condition)"),
                    ("multi_choice", "Multi choice"),
                    ("open_with_list", "Adress List"),
                    ("open_numeric", "Open numeric"),
                    ("open", "Open question"),
                    ("complex", "Complex"),
                ],
                max_length=20,
            ),
        ),
    ]

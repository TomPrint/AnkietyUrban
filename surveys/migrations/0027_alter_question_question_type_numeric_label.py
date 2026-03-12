from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("surveys", "0026_alter_question_question_type_checkbox_number_label"),
    ]

    operations = [
        migrations.AlterField(
            model_name="question",
            name="question_type",
            field=models.CharField(
                choices=[
                    ("yes_no", "Yes / No"),
                    ("yes_no_next", "Yes / No (no condition)"),
                    ("multi_choice", "Multi-many"),
                    ("multi_one", "Multi-one"),
                    ("open_with_list", "Adress List"),
                    ("open_number_list", "Checkbox/Number"),
                    ("open_numeric", "Numeric"),
                    ("open", "Open question"),
                    ("complex", "Complex"),
                ],
                max_length=20,
            ),
        ),
    ]

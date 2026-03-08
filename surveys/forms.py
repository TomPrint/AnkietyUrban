import json
from django import forms
from django.contrib.auth.models import User
from django.core.validators import RegexValidator

from .models import Customer, Question, QuestionChoice, SurveyAnswer, SurveySession, SurveyTemplate, TemplateNode


class DynamicQuestionForm(forms.Form):
    answer = forms.Field(required=False)

    def __init__(self, node: TemplateNode, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.node = node
        self.question = node.question
        self.is_complex = self.question.question_type == Question.QuestionType.COMPLEX
        if self.is_complex:
            self.fields.pop("answer", None)
            self._build_complex_fields()
        else:
            self.fields["answer"] = self._build_field(self.question)

    def _build_field(self, question: Question):
        common = {
            "required": True,
            "label": question.title,
            "help_text": question.help_text,
        }
        if question.question_type in (Question.QuestionType.YES_NO, Question.QuestionType.YES_NO_NEXT):
            return forms.ChoiceField(
                choices=(("yes", "Yes"), ("no", "No")),
                widget=forms.RadioSelect,
                **common,
            )
        if question.question_type == Question.QuestionType.MULTI_CHOICE:
            return forms.MultipleChoiceField(
                choices=[(str(opt.id), opt.label) for opt in question.choices.all()],
                widget=forms.CheckboxSelectMultiple,
                **common,
            )
        return forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), **common)

    def _build_complex_fields(self):
        for idx, item in enumerate(self.question.complex_items or []):
            item_type = item.get("type")
            item_label = item.get("label", f"Item {idx + 1}")
            field_name = f"complex_{idx}"
            item_required = bool(item.get("required", True))
            if item_type == Question.QuestionType.YES_NO:
                self.fields[field_name] = forms.ChoiceField(
                    choices=(("yes", "Yes"), ("no", "No")),
                    widget=forms.RadioSelect,
                    required=item_required,
                    label=item_label,
                )
            elif item_type == Question.QuestionType.MULTI_CHOICE:
                options = item.get("options", [])
                self.fields[field_name] = forms.MultipleChoiceField(
                    choices=[(str(i), opt) for i, opt in enumerate(options)],
                    widget=forms.CheckboxSelectMultiple,
                    required=item_required,
                    label=item_label,
                )
            else:
                placeholder = (item.get("placeholder") or "").strip()
                input_kind = (item.get("input_kind") or "text").strip().lower()
                attrs = {"class": "w-full rounded border border-slate-300 px-3 py-2 text-base"}
                if placeholder:
                    attrs["placeholder"] = placeholder
                if input_kind == "phone":
                    self.fields[field_name] = forms.CharField(
                        widget=forms.TextInput(attrs=attrs),
                        required=item_required,
                        label=item_label,
                        validators=[
                            RegexValidator(
                                regex=r"^\+?[0-9][0-9\s\-()]{6,}$",
                                message="Podaj poprawny numer telefonu.",
                            )
                        ],
                    )
                elif input_kind == "email":
                    self.fields[field_name] = forms.EmailField(
                        widget=forms.EmailInput(attrs=attrs),
                        required=item_required,
                        label=item_label,
                    )
                elif input_kind == "url":
                    self.fields[field_name] = forms.URLField(
                        widget=forms.URLInput(attrs=attrs),
                        required=item_required,
                        label=item_label,
                    )
                else:
                    self.fields[field_name] = forms.CharField(
                        widget=forms.TextInput(attrs=attrs),
                        required=item_required,
                        label=item_label,
                    )

    def get_answer_payload(self):
        if not self.is_complex:
            return self.cleaned_data["answer"]
        payload = []
        for idx, item in enumerate(self.question.complex_items or []):
            field_name = f"complex_{idx}"
            value = self.cleaned_data.get(field_name)
            payload.append(
                {
                    "type": item.get("type"),
                    "label": item.get("label", f"Item {idx + 1}"),
                    "options": item.get("options", []),
                    "value": value,
                }
            )
        return payload

    def fill_initial_from_answer(self, answer: SurveyAnswer | None):
        if not answer:
            return
        if self.question.question_type == Question.QuestionType.COMPLEX:
            saved_items = answer.complex_answer or []
            for idx, item in enumerate(saved_items):
                field_name = f"complex_{idx}"
                if field_name in self.fields:
                    self.initial[field_name] = item.get("value")
            return
        if self.question.question_type in (Question.QuestionType.YES_NO, Question.QuestionType.YES_NO_NEXT):
            if answer.yes_no_answer is not None:
                self.initial["answer"] = "yes" if answer.yes_no_answer else "no"
            return
        if self.question.question_type == Question.QuestionType.MULTI_CHOICE:
            self.initial["answer"] = [str(pk) for pk in answer.selected_choices.values_list("pk", flat=True)]
            return
        self.initial["answer"] = answer.open_answer


class QuestionManageForm(forms.ModelForm):
    options_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 7}),
        help_text="For Multi choice: one option per line.",
        label="Options",
    )
    complex_items_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        label="Complex Items",
    )

    class Meta:
        model = Question
        fields = [
            "title",
            "question_type",
            "is_finishing",
            "help_text",
            "source_url",
            "promotional_text",
            "options_text",
            "complex_items_json",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "h-4 w-4 rounded border-slate-300"
            else:
                field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"
        self.fields["is_finishing"].label = "Finishing question"
        self.fields["is_finishing"].help_text = "Use this question as a finishing-type node in template builder."
        self.fields["source_url"].label = "Promotional URL"
        self.fields["source_url"].help_text = "Optional external URL shown under this question."
        self.fields["promotional_text"].label = "Promotional Text"
        self.fields["promotional_text"].help_text = "Optional short text shown next to the link in survey."
        self.initial.setdefault("complex_items_json", "[]")
        if self.instance and self.instance.pk:
            self.initial["options_text"] = "\n".join(self.instance.choices.values_list("label", flat=True))
            self.initial["complex_items_json"] = json.dumps(self.instance.complex_items or [])

    def clean(self):
        cleaned = super().clean()
        question_type = cleaned.get("question_type")
        is_finishing = bool(cleaned.get("is_finishing"))
        options = [line.strip() for line in cleaned.get("options_text", "").splitlines() if line.strip()]
        if is_finishing and question_type == Question.QuestionType.YES_NO:
            self.add_error("question_type", "Finishing question cannot use Yes / No. Use Yes / No (no condition).")
        if question_type == Question.QuestionType.MULTI_CHOICE and not options:
            self.add_error("options_text", "Multi choice question needs at least one option.")
        if question_type == Question.QuestionType.COMPLEX:
            raw_json = cleaned.get("complex_items_json", "").strip() or "[]"
            try:
                items = json.loads(raw_json)
            except json.JSONDecodeError:
                self.add_error("complex_items_json", "Invalid complex items data.")
                items = []
            if not isinstance(items, list):
                self.add_error("complex_items_json", "Complex items must be a list.")
                items = []
            parsed = []
            for idx, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    self.add_error("complex_items_json", f"Item {idx}: invalid format.")
                    continue
                item_type = str(item.get("type", "")).strip().lower()
                label = str(item.get("label", "")).strip()
                if item_type not in (
                    Question.QuestionType.OPEN,
                    Question.QuestionType.YES_NO,
                    Question.QuestionType.MULTI_CHOICE,
                ):
                    self.add_error(
                        "complex_items_json",
                        f"Item {idx}: invalid type '{item_type}'. Use open / yes_no / multi_choice.",
                    )
                    continue
                if not label:
                    self.add_error("complex_items_json", f"Item {idx}: missing question label.")
                    continue
                if item_type == Question.QuestionType.MULTI_CHOICE:
                    raw_options = item.get("options", [])
                    if not isinstance(raw_options, list):
                        self.add_error("complex_items_json", f"Item {idx}: options must be a list.")
                        continue
                    item_options = [str(opt).strip() for opt in raw_options if str(opt).strip()]
                    if not item_options:
                        self.add_error("complex_items_json", f"Item {idx}: add at least one option.")
                        continue
                    parsed.append(
                        {
                            "type": Question.QuestionType.MULTI_CHOICE,
                            "label": label,
                            "options": item_options,
                        }
                    )
                else:
                    parsed.append({"type": item_type, "label": label, "options": []})

            if not parsed:
                self.add_error(
                    "complex_items_json",
                    "Complex question needs at least one valid sub-question.",
                )
            cleaned["parsed_complex_items"] = parsed
        else:
            cleaned["parsed_complex_items"] = []
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if not commit:
            obj.required = True
            if obj.question_type != Question.QuestionType.COMPLEX:
                obj.complex_items = []
            else:
                obj.complex_items = self.cleaned_data.get("parsed_complex_items", [])
            return obj
        obj.required = True
        if obj.question_type == Question.QuestionType.COMPLEX:
            obj.complex_items = self.cleaned_data.get("parsed_complex_items", [])
        else:
            obj.complex_items = []
        obj.save(update_fields=["required", "complex_items", "updated_at"])
        obj.choices.all().delete()
        if obj.question_type == Question.QuestionType.MULTI_CHOICE:
            options = [line.strip() for line in self.cleaned_data.get("options_text", "").splitlines() if line.strip()]
            QuestionChoice.objects.bulk_create(
                [QuestionChoice(question=obj, label=label, order=index) for index, label in enumerate(options, start=1)]
            )
        return obj


class SurveyTemplateForm(forms.ModelForm):
    class Meta:
        model = SurveyTemplate
        fields = ["name", "description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["company_name", "address", "contact_person", "email", "telephone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"


class SurveyAssignmentForm(forms.ModelForm):
    is_internal = forms.TypedChoiceField(
        choices=(("true", "Internal"), ("false", "External")),
        coerce=lambda value: str(value).lower() == "true",
        empty_value=True,
        initial="true",
        required=True,
        label="Survey Type",
    )

    class Meta:
        model = SurveySession
        fields = ["customer", "template", "is_internal"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_archived=False).order_by("company_name")
        self.fields["template"].queryset = SurveyTemplate.objects.filter(
            status=SurveyTemplate.Status.READY,
            is_archived=False,
        ).order_by("name")
        self.fields["is_internal"].choices = (("true", "Internal"), ("false", "External"))
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "h-4 w-4 rounded border-slate-300"
            else:
                field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"


class UserManageForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        help_text="Set a password for new users. Leave blank on edit to keep current password.",
    )
    password_confirm = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        label="Confirm password",
        help_text="Repeat the same password.",
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_staff", "is_active", "password", "password_confirm"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "password":
                field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "h-4 w-4 rounded border-slate-300"
            else:
                field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        if not self.instance.pk and not password:
            raise forms.ValidationError("Password is required for new users.")
        return password

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password") or ""
        password_confirm = cleaned.get("password_confirm") or ""

        if self.instance.pk:
            # On edit, both fields must be provided together to change password.
            if bool(password) ^ bool(password_confirm):
                self.add_error("password_confirm", "Provide both password fields to change password.")
        if password or password_confirm:
            if password != password_confirm:
                self.add_error("password_confirm", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user

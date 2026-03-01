from django import forms
from django.contrib.auth.models import User

from .models import Customer, Question, QuestionChoice, SurveyAnswer, SurveySession, SurveyTemplate, TemplateNode


class DynamicQuestionForm(forms.Form):
    answer = forms.Field(required=False)

    def __init__(self, node: TemplateNode, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.node = node
        self.question = node.question
        self.fields["answer"] = self._build_field(self.question)

    def _build_field(self, question: Question):
        common = {
            "required": question.required,
            "label": question.title,
            "help_text": question.help_text,
        }
        if question.question_type == Question.QuestionType.YES_NO:
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

    def fill_initial_from_answer(self, answer: SurveyAnswer | None):
        if not answer:
            return
        if self.question.question_type == Question.QuestionType.YES_NO:
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

    class Meta:
        model = Question
        fields = ["title", "question_type", "help_text", "required", "options_text"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"
        if self.instance and self.instance.pk:
            self.initial["options_text"] = "\n".join(self.instance.choices.values_list("label", flat=True))

    def clean(self):
        cleaned = super().clean()
        question_type = cleaned.get("question_type")
        options = [line.strip() for line in cleaned.get("options_text", "").splitlines() if line.strip()]
        if question_type == Question.QuestionType.MULTI_CHOICE and not options:
            self.add_error("options_text", "Multi choice question needs at least one option.")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if not commit:
            return obj
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
        fields = ["company_name", "address", "contact_person", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"


class SurveyAssignmentForm(forms.ModelForm):
    class Meta:
        model = SurveySession
        fields = ["customer", "template"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = SurveyTemplate.objects.filter(status=SurveyTemplate.Status.READY)
        for field in self.fields.values():
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

import json
from django import forms
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.utils.html import format_html, format_html_join

from .models import Customer, Question, QuestionChoice, SurveyAnswer, SurveySession, SurveyTemplate, TemplateNode

ADDRESS_PREFIX_CHOICES = [
    "ul.",
    "al.",
    "pl.",
    "skwer",
    "os.",
    "rondo",
    "bulwar",
    "pasaz",
    "trakt",
    "promenada",
    "droga",
]


class DatalistTextInput(forms.TextInput):
    def __init__(self, *args, options=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.options = [str(opt).strip() for opt in (options or []) if str(opt).strip()]

    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs.copy() if attrs else {}
        if not self.options:
            return super().render(name, value, attrs=attrs, renderer=renderer)
        datalist_id = attrs.get("list") or f"id_{name}_list"
        attrs["list"] = datalist_id
        input_html = super().render(name, value, attrs=attrs, renderer=renderer)
        options_html = format_html_join(
            "",
            "<option value=\"{}\"></option>",
            ((opt,) for opt in self.options),
        )
        datalist_html = format_html("<datalist id=\"{}\">{}</datalist>", datalist_id, options_html)
        return format_html("{}{}", input_html, datalist_html)


class DynamicQuestionForm(forms.Form):
    answer = forms.Field(required=False)

    def __init__(self, node: TemplateNode, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.node = node
        self.question = node.question
        self.is_complex = self.question.question_type == Question.QuestionType.COMPLEX
        complex_items = self.question.complex_items or []
        yes_no_indexes = [
            idx
            for idx, item in enumerate(complex_items)
            if str(item.get("type", "")).strip().lower() == Question.QuestionType.YES_NO
        ]
        self.complex_condition_field_name = (
            f"complex_{yes_no_indexes[0]}"
            if self.is_complex and len(yes_no_indexes) == 1
            else None
        )
        self._complex_item_type_by_field = {}
        self._complex_item_required_by_field = {}
        self._complex_item_show_if_by_field = {}
        self._complex_item_options_by_field = {}
        self.address_prefixes = ADDRESS_PREFIX_CHOICES
        self.open_with_list_suggestions = [opt.label for opt in self.question.choices.all()] if self.question_id_is_open_with_list() else []
        if self.is_complex:
            self.fields.pop("answer", None)
            self._build_complex_fields()
        else:
            self.fields["answer"] = self._build_field(self.question)

    def question_id_is_open_with_list(self):
        return self.question.question_type == Question.QuestionType.OPEN_WITH_LIST

    def question_is_open_number_list(self):
        return self.question.question_type == Question.QuestionType.OPEN_NUMBER_LIST

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
        if question.question_type == Question.QuestionType.MULTI_ONE:
            return forms.ChoiceField(
                choices=[(str(opt.id), opt.label) for opt in question.choices.all()],
                widget=forms.RadioSelect,
                **common,
            )
        if question.question_type == Question.QuestionType.OPEN_NUMERIC:
            return forms.DecimalField(
                widget=forms.NumberInput(attrs={"step": "any"}),
                **common,
            )
        if question.question_type == Question.QuestionType.OPEN_WITH_LIST:
            return forms.CharField(
                widget=forms.HiddenInput(),
                **common,
            )
        if question.question_type == Question.QuestionType.OPEN_NUMBER_LIST:
            options_json = json.dumps([opt.label for opt in question.choices.all()], ensure_ascii=False)
            return forms.CharField(
                widget=forms.HiddenInput(
                    attrs={
                        "class": "js-checkbox-number-hidden",
                        "data-checkbox-options": options_json,
                        "data_checkbox_options": options_json,
                    }
                ),
                **common,
            )
        return forms.CharField(
            widget=forms.Textarea(
                attrs={
                    "rows": 5,
                    "style": "resize: vertical;",
                }
            ),
            **common,
        )

    def clean_answer(self):
        answer = self.cleaned_data.get("answer", "")
        if self.question_is_open_number_list():
            try:
                payload = json.loads(answer or "[]")
            except json.JSONDecodeError:
                raise forms.ValidationError("Invalid checkbox-number format.")
            if not isinstance(payload, list):
                raise forms.ValidationError("Invalid checkbox-number format.")
            allowed = {opt.label.strip() for opt in self.question.choices.all() if opt.label.strip()}
            normalized = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                option = str(item.get("option", "")).strip()
                raw_number = str(item.get("number", "")).strip()
                if not option and not raw_number:
                    continue
                if not option:
                    raise forms.ValidationError("Option value cannot be empty.")
                if option not in allowed:
                    raise forms.ValidationError("Choose a valid option.")
                if raw_number == "":
                    raise forms.ValidationError("Numeric value cannot be empty.")
                try:
                    number = int(raw_number)
                except ValueError:
                    raise forms.ValidationError("Numeric value must be an integer.")
                if number < 0:
                    raise forms.ValidationError("Numeric value cannot be negative.")
                normalized.append({"option": option, "number": str(number)})
            if not normalized:
                raise forms.ValidationError("Select at least one option.")
            return json.dumps(normalized, ensure_ascii=False)
        if not self.question_id_is_open_with_list():
            return answer
        try:
            payload = json.loads(answer or "[]")
        except json.JSONDecodeError:
            raise forms.ValidationError("Invalid address list format.")
        if not isinstance(payload, list):
            raise forms.ValidationError("Invalid address list format.")
        lines = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            prefix = str(item.get("prefix", "")).strip()
            text = str(item.get("text", "")).strip()
            if not prefix and not text:
                continue
            if prefix not in ADDRESS_PREFIX_CHOICES:
                raise forms.ValidationError("Choose a valid address prefix.")
            if not text:
                raise forms.ValidationError("Address value cannot be empty.")
            lines.append(f"{prefix} {text}".strip())
        if not lines:
            raise forms.ValidationError("Add at least one address.")
        return "\n".join(lines)

    def _build_complex_fields(self):
        for idx, item in enumerate(self.question.complex_items or []):
            item_type = item.get("type")
            item_label = item.get("label", f"Item {idx + 1}")
            field_name = f"complex_{idx}"
            item_required = bool(item.get("required", True))
            show_if = str(item.get("show_if", "any")).strip().lower()
            if show_if not in ("any", "yes", "no"):
                show_if = "any"
            effective_required = item_required
            if self.complex_condition_field_name and field_name != self.complex_condition_field_name and show_if in ("yes", "no"):
                # Conditionally visible items are required only if branch matches.
                effective_required = False
            self._complex_item_type_by_field[field_name] = item_type
            self._complex_item_required_by_field[field_name] = item_required
            self._complex_item_show_if_by_field[field_name] = show_if
            self._complex_item_options_by_field[field_name] = item.get("options", [])
            widget_attrs = {}
            if self.complex_condition_field_name and field_name != self.complex_condition_field_name:
                widget_attrs["data-complex-dependent"] = "1"
                widget_attrs["data-complex-show-if"] = show_if
            if item_type in (Question.QuestionType.YES_NO, Question.QuestionType.YES_NO_NEXT):
                if item_type == Question.QuestionType.YES_NO and field_name == self.complex_condition_field_name:
                    widget_attrs["data-complex-condition-source"] = "1"
                self.fields[field_name] = forms.ChoiceField(
                    choices=(("yes", "Yes"), ("no", "No")),
                    widget=forms.RadioSelect(attrs=widget_attrs),
                    required=effective_required,
                    label=item_label,
                )
            elif item_type == Question.QuestionType.MULTI_CHOICE:
                options = item.get("options", [])
                self.fields[field_name] = forms.MultipleChoiceField(
                    choices=[(str(i), opt) for i, opt in enumerate(options)],
                    widget=forms.CheckboxSelectMultiple(attrs=widget_attrs),
                    required=effective_required,
                    label=item_label,
                )
            elif item_type == Question.QuestionType.MULTI_ONE:
                options = item.get("options", [])
                self.fields[field_name] = forms.ChoiceField(
                    choices=[(str(i), opt) for i, opt in enumerate(options)],
                    widget=forms.RadioSelect(attrs=widget_attrs),
                    required=effective_required,
                    label=item_label,
                )
            elif item_type == Question.QuestionType.OPEN_NUMBER_LIST:
                hidden_attrs = {
                    "class": "js-checkbox-number-hidden",
                    "data-checkbox-options": json.dumps(item.get("options", []), ensure_ascii=False),
                    "data_checkbox_options": json.dumps(item.get("options", []), ensure_ascii=False),
                }
                hidden_attrs.update(widget_attrs)
                self.fields[field_name] = forms.CharField(
                    widget=forms.HiddenInput(attrs=hidden_attrs),
                    required=effective_required,
                    label=item_label,
                )
            elif item_type == Question.QuestionType.OPEN_WITH_LIST:
                hidden_attrs = {
                    "class": "js-complex-address-list-hidden",
                    "data-address-prefixes": json.dumps(ADDRESS_PREFIX_CHOICES, ensure_ascii=False),
                    "data_address_prefixes": json.dumps(ADDRESS_PREFIX_CHOICES, ensure_ascii=False),
                    "data-address-suggestions": json.dumps(item.get("options", []), ensure_ascii=False),
                    "data_address_suggestions": json.dumps(item.get("options", []), ensure_ascii=False),
                }
                hidden_attrs.update(widget_attrs)
                self.fields[field_name] = forms.CharField(
                    widget=forms.HiddenInput(attrs=hidden_attrs),
                    required=effective_required,
                    label=item_label,
                )
            else:
                placeholder = (item.get("placeholder") or "").strip()
                input_kind = (item.get("input_kind") or "text").strip().lower()
                attrs = {"class": "w-full rounded border border-slate-300 px-3 py-2 text-base"}
                attrs.update(widget_attrs)
                if placeholder:
                    attrs["placeholder"] = placeholder
                if item_type == Question.QuestionType.OPEN_NUMERIC:
                    self.fields[field_name] = forms.DecimalField(
                        widget=forms.NumberInput(attrs={"step": "any", **attrs}),
                        required=effective_required,
                        label=item_label,
                    )
                elif item_type == Question.QuestionType.OPEN_WITH_LIST:
                    self.fields[field_name] = forms.CharField(
                        widget=DatalistTextInput(attrs=attrs, options=item.get("options", [])),
                        required=effective_required,
                        label=item_label,
                    )
                elif input_kind == "phone":
                    self.fields[field_name] = forms.CharField(
                        widget=forms.TextInput(attrs=attrs),
                        required=effective_required,
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
                        required=effective_required,
                        label=item_label,
                    )
                elif input_kind == "url":
                    self.fields[field_name] = forms.URLField(
                        widget=forms.URLInput(attrs=attrs),
                        required=effective_required,
                        label=item_label,
                    )
                else:
                    self.fields[field_name] = forms.CharField(
                        widget=forms.Textarea(
                            attrs={
                                **attrs,
                                "rows": 4,
                                "style": "resize: vertical;",
                            }
                        ),
                        required=effective_required,
                        label=item_label,
                    )

    def clean(self):
        cleaned = super().clean()
        if not self.is_complex:
            return cleaned

        if self.complex_condition_field_name:
            condition_answer = cleaned.get(self.complex_condition_field_name)
            for idx, item in enumerate((self.question.complex_items or []), start=0):
                field_name = f"complex_{idx}"
                if field_name == self.complex_condition_field_name:
                    continue
                show_if = self._complex_item_show_if_by_field.get(field_name, "any")
                is_required = bool(item.get("required", True))
                show_item = show_if == "any" or condition_answer == show_if
                value = cleaned.get(field_name)

                if show_item:
                    if not is_required:
                        continue
                    if isinstance(value, list):
                        empty_value = len(value) == 0
                    else:
                        empty_value = value in (None, "")
                    if empty_value:
                        if show_if in ("yes", "no"):
                            self.add_error(field_name, f"This field is required when first answer is {show_if.capitalize()}.")
                        else:
                            self.add_error(field_name, "This field is required.")
                else:
                    # Skip/clear hidden conditional items.
                    item_type = self._complex_item_type_by_field.get(field_name)
                    if item_type == Question.QuestionType.MULTI_CHOICE:
                        cleaned[field_name] = []
                    else:
                        cleaned[field_name] = ""

        for field_name, item_type in self._complex_item_type_by_field.items():
            if item_type != Question.QuestionType.OPEN_NUMBER_LIST:
                continue
            raw_value = cleaned.get(field_name)
            is_required = bool(self.fields.get(field_name) and self.fields[field_name].required)
            if raw_value in (None, ""):
                if is_required:
                    self.add_error(field_name, "This field is required.")
                continue
            try:
                payload = json.loads(raw_value if isinstance(raw_value, str) else "[]")
            except json.JSONDecodeError:
                self.add_error(field_name, "Invalid checkbox-number format.")
                continue
            if not isinstance(payload, list):
                self.add_error(field_name, "Invalid checkbox-number format.")
                continue
            allowed_options = {str(o).strip() for o in (self._complex_item_options_by_field.get(field_name) or []) if str(o).strip()}
            normalized = []
            failed = False
            for row in payload:
                if not isinstance(row, dict):
                    continue
                option = str(row.get("option", "")).strip()
                raw_number = str(row.get("number", "")).strip()
                if not option and not raw_number:
                    continue
                if not option:
                    self.add_error(field_name, "Option value cannot be empty.")
                    failed = True
                    break
                if allowed_options and option not in allowed_options:
                    self.add_error(field_name, "Choose a valid option.")
                    failed = True
                    break
                if raw_number == "":
                    self.add_error(field_name, "Numeric value cannot be empty.")
                    failed = True
                    break
                try:
                    number = int(raw_number)
                except ValueError:
                    self.add_error(field_name, "Numeric value must be an integer.")
                    failed = True
                    break
                if number < 0:
                    self.add_error(field_name, "Numeric value cannot be negative.")
                    failed = True
                    break
                normalized.append({"option": option, "number": str(number)})
            if failed:
                continue
            if is_required and not normalized:
                self.add_error(field_name, "Add at least one row.")
                continue
            cleaned[field_name] = json.dumps(normalized, ensure_ascii=False)
        for field_name, item_type in self._complex_item_type_by_field.items():
            if item_type != Question.QuestionType.OPEN_WITH_LIST:
                continue
            raw_value = cleaned.get(field_name)
            is_required = bool(self.fields.get(field_name) and self.fields[field_name].required)
            if raw_value in (None, ""):
                if is_required:
                    self.add_error(field_name, "This field is required.")
                continue
            try:
                payload = json.loads(raw_value if isinstance(raw_value, str) else "[]")
            except json.JSONDecodeError:
                self.add_error(field_name, "Invalid address list format.")
                continue
            if not isinstance(payload, list):
                self.add_error(field_name, "Invalid address list format.")
                continue
            normalized = []
            failed = False
            for row in payload:
                if not isinstance(row, dict):
                    continue
                prefix = str(row.get("prefix", "")).strip()
                text = str(row.get("text", "")).strip()
                if not prefix and not text:
                    continue
                if prefix not in ADDRESS_PREFIX_CHOICES:
                    self.add_error(field_name, "Choose a valid address prefix.")
                    failed = True
                    break
                if not text:
                    self.add_error(field_name, "Address value cannot be empty.")
                    failed = True
                    break
                normalized.append({"prefix": prefix, "text": text})
            if failed:
                continue
            if is_required and not normalized:
                self.add_error(field_name, "Add at least one address.")
                continue
            cleaned[field_name] = json.dumps(normalized, ensure_ascii=False)
        return cleaned

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
                    current_value = item.get("value")
                    current_type = (item.get("type") or "").strip().lower()
                    if current_type == Question.QuestionType.OPEN_NUMBER_LIST and not isinstance(current_value, str):
                        try:
                            current_value = json.dumps(current_value or [], ensure_ascii=False)
                        except TypeError:
                            current_value = "[]"
                    if current_type == Question.QuestionType.OPEN_WITH_LIST and not isinstance(current_value, str):
                        try:
                            current_value = json.dumps(current_value or [], ensure_ascii=False)
                        except TypeError:
                            current_value = "[]"
                    self.initial[field_name] = current_value
            return
        if self.question.question_type in (Question.QuestionType.YES_NO, Question.QuestionType.YES_NO_NEXT):
            if answer.yes_no_answer is not None:
                self.initial["answer"] = "yes" if answer.yes_no_answer else "no"
            return
        if self.question.question_type == Question.QuestionType.MULTI_CHOICE:
            self.initial["answer"] = [str(pk) for pk in answer.selected_choices.values_list("pk", flat=True)]
            return
        if self.question.question_type == Question.QuestionType.MULTI_ONE:
            first_choice_id = answer.selected_choices.values_list("pk", flat=True).first()
            self.initial["answer"] = str(first_choice_id) if first_choice_id else ""
            return
        self.initial["answer"] = answer.open_answer


class QuestionManageForm(forms.ModelForm):
    options_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 7}),
        help_text="For Multi-many / Multi-one / Checkbox-Number: one option per line.",
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
        title = (cleaned.get("title") or "").strip()
        question_type = cleaned.get("question_type")
        is_finishing = bool(cleaned.get("is_finishing"))
        options = [line.strip() for line in cleaned.get("options_text", "").splitlines() if line.strip()]
        if title:
            dupe_qs = Question.objects.filter(is_archived=False, title__iexact=title)
            if self.instance and self.instance.pk:
                dupe_qs = dupe_qs.exclude(pk=self.instance.pk)
            if dupe_qs.exists():
                self.add_error("title", "Question with this name already exists.")
        if is_finishing and question_type == Question.QuestionType.YES_NO:
            self.add_error("question_type", "Finishing question cannot use Yes / No. Use Yes / No (no condition).")
        if question_type in (Question.QuestionType.MULTI_CHOICE, Question.QuestionType.MULTI_ONE, Question.QuestionType.OPEN_NUMBER_LIST) and not options:
            self.add_error("options_text", "This question type needs at least one option.")
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
                show_if = str(item.get("show_if", "any")).strip().lower()
                if item_type not in (
                    Question.QuestionType.OPEN,
                    Question.QuestionType.YES_NO,
                    Question.QuestionType.YES_NO_NEXT,
                    Question.QuestionType.MULTI_CHOICE,
                    Question.QuestionType.MULTI_ONE,
                    Question.QuestionType.OPEN_NUMERIC,
                    Question.QuestionType.OPEN_WITH_LIST,
                    Question.QuestionType.OPEN_NUMBER_LIST,
                ):
                    self.add_error(
                        "complex_items_json",
                        f"Item {idx}: invalid type '{item_type}'.",
                    )
                    continue
                if not label:
                    self.add_error("complex_items_json", f"Item {idx}: missing question label.")
                    continue
                if show_if not in ("any", "yes", "no"):
                    self.add_error("complex_items_json", f"Item {idx}: invalid show_if value.")
                    continue
                if item_type == Question.QuestionType.YES_NO:
                    show_if = "any"
                if item_type in (Question.QuestionType.MULTI_CHOICE, Question.QuestionType.MULTI_ONE, Question.QuestionType.OPEN_NUMBER_LIST):
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
                            "type": item_type,
                            "label": label,
                            "options": item_options,
                            "show_if": show_if,
                        }
                    )
                else:
                    item_options = []
                    if item_type == Question.QuestionType.OPEN_WITH_LIST:
                        raw_options = item.get("options", [])
                        if not isinstance(raw_options, list):
                            self.add_error("complex_items_json", f"Item {idx}: options must be a list.")
                            continue
                        item_options = [str(opt).strip() for opt in raw_options if str(opt).strip()]
                    parsed.append({"type": item_type, "label": label, "options": item_options, "show_if": show_if})

            condition_count = sum(
                1
                for item in parsed
                if str(item.get("type", "")).strip().lower() == Question.QuestionType.YES_NO
            )
            conditional_count = sum(1 for item in parsed if str(item.get("show_if", "any")).strip().lower() in ("yes", "no"))
            if condition_count > 1:
                self.add_error("complex_items_json", "Complex question can contain only one conditional Yes / No item.")
            if conditional_count and condition_count == 0:
                self.add_error("complex_items_json", "Items with Show if Yes/No require one Yes / No item in complex question.")
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
        if obj.question_type in (Question.QuestionType.MULTI_CHOICE, Question.QuestionType.MULTI_ONE, Question.QuestionType.OPEN_NUMBER_LIST):
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

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            return name
        dupe_qs = SurveyTemplate.objects.filter(is_archived=False, name__iexact=name)
        if self.instance and self.instance.pk:
            dupe_qs = dupe_qs.exclude(pk=self.instance.pk)
        if dupe_qs.exists():
            raise forms.ValidationError("Template with this name already exists.")
        return name


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["company_name", "address", "contact_person", "email", "telephone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"

    def clean_company_name(self):
        company_name = (self.cleaned_data.get("company_name") or "").strip()
        if not company_name:
            return company_name
        dupe_qs = Customer.objects.filter(is_archived=False, company_name__iexact=company_name)
        if self.instance and self.instance.pk:
            dupe_qs = dupe_qs.exclude(pk=self.instance.pk)
        if dupe_qs.exists():
            raise forms.ValidationError("Customer with this name already exists.")
        return company_name


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

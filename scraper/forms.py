from django import forms


COMMON_INPUT_CLASS = "w-full rounded border border-slate-300 px-3 py-2"
COMMON_TEXTAREA_CLASS = "min-h-28 w-full rounded border border-slate-300 px-3 py-2"
COMMON_SELECT_CLASS = "w-full rounded border border-slate-300 bg-white px-3 py-2"


class GeminiGenerateForm(forms.Form):
    search_goal = forms.CharField(
        label="Czego szukamy",
        initial="Znajdz potencjalne spoldzielnie mieszkaniowe",
        widget=forms.TextInput(attrs={"class": COMMON_INPUT_CLASS}),
        help_text="Krotko opisz segment lub typ organizacji, ktore Gemini ma znalezc.",
    )
    city = forms.CharField(
        label="Miasto",
        initial="Warszawa",
        widget=forms.TextInput(attrs={"class": COMMON_INPUT_CLASS}),
    )
    district = forms.CharField(
        required=False,
        label="Dzielnica",
        widget=forms.TextInput(attrs={"class": COMMON_INPUT_CLASS}),
        help_text="Opcjonalny filtr wynikow po dzielnicy/obszarze.",
    )
    max_candidates = forms.IntegerField(
        label="Limit kandydatow",
        min_value=1,
        max_value=30,
        initial=10,
        widget=forms.NumberInput(attrs={"class": COMMON_INPUT_CLASS}),
        help_text="Trzymaj limit nisko, zeby wyszukiwanie bylo szybkie.",
    )
    use_google_search = forms.BooleanField(
        required=False,
        initial=True,
        label="Uzyj Google Search grounding",
    )
    extra_instructions = forms.CharField(
        required=False,
        label="Dodatkowe instrukcje",
        widget=forms.Textarea(
            attrs={
                "class": COMMON_TEXTAREA_CLASS,
                "placeholder": "Np. preferuj oficjalne strony spoldzielni, pomijaj portale ogloszeniowe i firmy deweloperskie.",
            }
        ),
    )


class TavilySearchForm(forms.Form):
    search_goal = forms.CharField(
        label="Fraza wyszukiwania",
        initial="spoldzielnie mieszkaniowe",
        widget=forms.TextInput(attrs={"class": COMMON_INPUT_CLASS}),
        help_text="Fraza, po ktorej Tavily ma szukac kandydatow.",
    )
    city = forms.CharField(
        label="Miasto",
        initial="Warszawa",
        widget=forms.TextInput(attrs={"class": COMMON_INPUT_CLASS}),
        help_text="Miasto, dla ktorego chcesz zaw\u0119zi\u0107 wyniki.",
    )
    district = forms.CharField(
        required=False,
        label="Dzielnica / obszar",
        widget=forms.TextInput(attrs={"class": COMMON_INPUT_CLASS}),
        help_text="Opcjonalnie dodaj dzielnic\u0119 lub obszar w miescie.",
    )
    max_candidates = forms.IntegerField(
        label="Limit wynikow",
        min_value=1,
        max_value=20,
        initial=10,
        widget=forms.NumberInput(attrs={"class": COMMON_INPUT_CLASS}),
    )
    search_depth = forms.ChoiceField(
        label="Filtr zaawansowania",
        initial="advanced",
        choices=[
            ("basic", "basic"),
            ("advanced", "advanced"),
        ],
        widget=forms.Select(attrs={"class": COMMON_INPUT_CLASS}),
        help_text="Advanced daje lepsze tre\u015bci, ale zu\u017cywa wi\u0119cej kredyt\u00f3w Tavily.",
    )
    extra_instructions = forms.CharField(
        required=False,
        label="Dodatkowe instrukcje",
        widget=forms.Textarea(
            attrs={
                "class": COMMON_TEXTAREA_CLASS,
                "placeholder": "Np. preferuj oficjalne strony spoldzielni i podstrony kontaktowe.",
            }
        ),
    )


class LeadCandidateRejectForm(forms.Form):
    reason = forms.CharField(
        required=False,
        label="Powod odrzucenia",
        widget=forms.TextInput(attrs={"class": COMMON_INPUT_CLASS}),
    )


class LeadCandidateFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label="Szukaj",
        widget=forms.TextInput(
            attrs={
                "class": COMMON_INPUT_CLASS,
                "placeholder": "Nazwa, email, telefon, adres, powod...",
            }
        ),
    )
    source = forms.ChoiceField(
        required=False,
        label="Zrodlo",
        choices=[
            ("", "Wszystkie zrodla"),
            ("gemini", "Gemini"),
            ("tavily", "Tavily"),
        ],
        widget=forms.Select(attrs={"class": COMMON_SELECT_CLASS}),
    )
    duplicate = forms.ChoiceField(
        required=False,
        label="Duplikaty",
        choices=[
            ("", "Wszystkie rekordy"),
            ("yes", "Tylko duplikaty"),
            ("no", "Bez duplikatow"),
        ],
        widget=forms.Select(attrs={"class": COMMON_SELECT_CLASS}),
    )

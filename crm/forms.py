from django import forms

from .models import Customer


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["company_name", "district", "address", "website", "contact_person", "email", "telephone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            "company_name": "Nazwa firmy",
            "district": "Dzielnica",
            "address": "Adres",
            "website": "Strona WWW",
            "contact_person": "Osoba kontaktowa",
            "email": "E-mail",
            "telephone": "Telefon",
        }
        for name, field in self.fields.items():
            if name in labels:
                field.label = labels[name]
            field.widget.attrs["class"] = "w-full rounded border border-slate-300 px-3 py-2"

    def clean_company_name(self):
        company_name = (self.cleaned_data.get("company_name") or "").strip()
        if not company_name:
            return company_name
        dupe_qs = Customer.objects.filter(is_archived=False, company_name__iexact=company_name)
        if self.instance and self.instance.pk:
            dupe_qs = dupe_qs.exclude(pk=self.instance.pk)
        if dupe_qs.exists():
            raise forms.ValidationError("Klient o tej nazwie już istnieje.")
        return company_name
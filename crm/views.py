from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from surveys.models import SurveySession
from surveys.views import _session_branch_completion_percent

from .forms import CustomerForm
from .models import Customer


staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_staff)


@staff_required
def customer_list(request):
    customers = Customer.objects.filter(is_archived=False).annotate(
        survays_count=Count(
            "survey_sessions",
            filter=Q(survey_sessions__is_archived=False),
        )
    )
    return render(request, "management/customers/list.html", {"customers": customers})


@staff_required
def customer_detail(request, customer_id: int):
    customer = get_object_or_404(Customer, pk=customer_id, is_archived=False)
    sessions = (
        SurveySession.objects.select_related("template")
        .filter(customer=customer, is_archived=False)
        .order_by("-updated_at")
    )

    session_rows = []
    for session in sessions:
        session_rows.append(
            {
                "session": session,
                "score_percent": _session_branch_completion_percent(session),
                "saved_versions_count": session.snapshots.count(),
            }
        )

    context = {
        "customer": customer,
        "session_rows": session_rows,
    }
    return render(request, "management/customers/detail.html", context)


@staff_required
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("portal-customers")
    return render(request, "management/customers/form.html", {"form": form, "title": "Create Customer"})


@staff_required
def customer_edit(request, customer_id: int):
    customer = get_object_or_404(Customer, pk=customer_id, is_archived=False)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("portal-customers")
    return render(request, "management/customers/form.html", {"form": form, "title": "Edit Customer"})


@staff_required
@require_POST
def customer_delete(request, customer_id: int):
    customer = get_object_or_404(Customer, pk=customer_id, is_archived=False)
    customer.archive()
    return redirect("portal-customers")




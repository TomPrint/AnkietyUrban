from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.shortcuts import render

from crm.models import Customer


staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_staff)


@staff_required
def portal_home(request):
    context = {
        "total_users": User.objects.count(),
        "active_users": User.objects.filter(is_active=True).count(),
        "staff_users": User.objects.filter(is_staff=True).count(),
        "total_customers": Customer.objects.filter(is_archived=False).count(),
        "customers_with_email": Customer.objects.filter(is_archived=False).exclude(email="").count(),
        "customers_with_phone": Customer.objects.filter(is_archived=False).exclude(telephone="").count(),
    }
    return render(request, "portal/home.html", context)


@staff_required
def portal_scraper(request):
    return render(request, "portal/scraper.html")



from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render


staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_staff)


@staff_required
def scraper_home(request):
    return render(request, "scraper/home.html")


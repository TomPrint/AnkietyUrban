from django.urls import path
from django.views.generic import RedirectView

from .views import (
    candidate_approve,
    candidate_delete,
    candidate_list,
    candidate_reject,
    candidate_reopen,
    gemini_generate,
    gemini_import,
    scraper_home,
    tavily_generate,
)

urlpatterns = [
    path("scraper/", scraper_home, name="scraper-home"),
    path("scraper/gemini-import/", gemini_import, name="scraper-gemini-import"),
    path("scraper/gemini-generate/", gemini_generate, name="scraper-gemini-generate"),
    path("scraper/tavily-generate/", tavily_generate, name="scraper-tavily-generate"),
    path("scraper/candidates/", candidate_list, name="scraper-candidates"),
    path("scraper/candidates/<int:candidate_id>/approve/", candidate_approve, name="scraper-candidate-approve"),
    path("scraper/candidates/<int:candidate_id>/reject/", candidate_reject, name="scraper-candidate-reject"),
    path("scraper/candidates/<int:candidate_id>/reopen/", candidate_reopen, name="scraper-candidate-reopen"),
    path("scraper/candidates/<int:candidate_id>/delete/", candidate_delete, name="scraper-candidate-delete"),
    path("portal/scraper/", RedirectView.as_view(pattern_name="scraper-home", permanent=False)),
]

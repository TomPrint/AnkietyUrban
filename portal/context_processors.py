PORTAL_ROUTE_NAMES = {
    "portal-home",
    "portal-users",
    "portal-user-create",
    "portal-user-edit",
    "portal-customers",
    "portal-customer-create",
    "portal-customer-edit",
    "portal-customer-detail",
}

SCRAPER_ROUTE_NAMES = {
    "scraper-home",
    "scraper-gemini-import",
    "scraper-gemini-generate",
    "scraper-tavily-generate",
    "scraper-candidates",
    "scraper-candidate-approve",
    "scraper-candidate-reject",
    "scraper-candidate-reopen",
    "scraper-candidate-delete",
}

PUBLIC_SURVEY_ROUTE_NAMES = {
    "survey-by-token",
    "survey-thanks",
    "survey-saved",
}


def navigation(request):
    resolver_match = getattr(request, "resolver_match", None)
    url_name = resolver_match.url_name if resolver_match else ""

    is_public_survey = url_name in PUBLIC_SURVEY_ROUTE_NAMES
    is_scraper_section = url_name in SCRAPER_ROUTE_NAMES
    is_portal_section = url_name in PORTAL_ROUTE_NAMES
    is_staff_nav_visible = (
        request.user.is_authenticated
        and request.user.is_staff
        and url_name != "login"
        and not is_public_survey
    )
    is_management_section = is_staff_nav_visible and not is_portal_section and not is_scraper_section

    if is_scraper_section:
        brand_label = "Scraper"
    elif is_portal_section:
        brand_label = "Portal"
    else:
        brand_label = "Ankiety"

    return {
        "nav_url_name": url_name,
        "is_public_survey": is_public_survey,
        "is_portal_section": is_portal_section,
        "is_scraper_section": is_scraper_section,
        "is_management_section": is_management_section,
        "is_staff_nav_visible": is_staff_nav_visible,
        "portal_brand_label": brand_label,
    }

from django import forms
from django.contrib.auth import logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.shortcuts import redirect


staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_staff)


class StaffAuthenticationForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Incorrect username or password.",
        "inactive": "This account is inactive. Contact administrator.",
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_staff:
            raise ValidationError(
                "Your account is restricted from management portal.",
                code="not_staff",
            )


class StaffLoginView(LoginView):
    template_name = "management/login.html"
    authentication_form = StaffAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or "/portal/"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_staff:
            logout(request)
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

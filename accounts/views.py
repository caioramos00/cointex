from django.contrib.auth.decorators import login_required
from django.views.generic import FormView
from django.contrib.auth.views import LoginView
from django.contrib.auth import login
from django.urls import reverse_lazy
from django.shortcuts import redirect
from django.contrib.auth import logout
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .forms import CustomUserCreationForm, CustomAuthenticationForm


class CustomLoginView(LoginView):
    form_class = CustomAuthenticationForm
    template_name = 'accounts/sign-in.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.session['track_complete_registration'] = True
        return response

    def get_success_url(self):
        return reverse_lazy('core:home')

class SignUpView(FormView):
    template_name = 'accounts/sign-up.html'
    form_class = CustomUserCreationForm
    success_url = reverse_lazy('core:home')

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            user = form.save()
            login(self.request, user)
            return super().form_valid(form)
        except Exception as e:
            return self.form_invalid(form)

    def form_invalid(self, form):
        return super().form_invalid(form)
    
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('core:home')
        return super().get(request, *args, **kwargs)
    
@login_required
def logout_view(request):
    logout(request)
    return redirect('accounts:login')

@require_http_methods(["GET", "POST"])
def password_help(request):
    """
    Página única de “Ajuda com senha”.
    POST apenas marca 'submitted=True' e renderiza o estado de sucesso
    (não envia e-mail de verdade).
    """
    submitted = False
    email_or_phone = ""
    if request.method == "POST":
        email_or_phone = (request.POST.get("email_or_phone") or "").strip()
        submitted = True  # aqui “fingimos” o envio
        # TODO (futuro): gravar uma entrada em DB, mandar e-mail real, etc.

    ctx = {
        "submitted": submitted,
        "email_or_phone": email_or_phone,
    }
    return render(request, "themes/mpay/accounts/password-help.html", ctx)
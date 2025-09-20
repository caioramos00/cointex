from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.utils.translation import gettext_lazy as _
from .models import CustomUser
from datetime import date
from django.contrib.auth import authenticate

class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(
        label=_("E-mail"),
        widget=forms.EmailInput(attrs={
            'placeholder': 'exemplo@mpaybank.cash',
            'class': 'form-control'
        })
    )
    first_name = forms.CharField(
        max_length=150,
        required=True,
        label=_("Nome"),
        help_text=_("Obrigatório."),
        widget=forms.TextInput(attrs={
            'maxlength': 150,
            'placeholder': 'Seu nome',
            'class': 'form-control'
        })
    )
    last_name = forms.CharField(
        max_length=150,
        required=True,
        label=_("Sobrenome"),
        help_text=_("Obrigatório."),
        widget=forms.TextInput(attrs={
            'maxlength': 150,
            'placeholder': 'Seu sobrenome',
            'class': 'form-control'
        })
    )
    date_of_birth = forms.DateField(
        required=True,
        label=_("Data de nascimento"),
        help_text=_("Obrigatório. Deve ter pelo menos 18 anos."),
        input_formats=['%d/%m/%Y', '%Y-%m-%d'],
        widget=forms.TextInput(attrs={
            'type': 'text', 'id': 'date_of_birth',
            'placeholder': 'DD/MM/AAAA',
            'class': 'form-control'
        })
    )
    cpf = forms.CharField(
        max_length=14,
        required=True,
        label=_("CPF"),
        help_text=_("Obrigatório. Formato: 000.000.000-00"),
        widget=forms.TextInput(attrs={
            'maxlength': 14, 'id': 'cpf',
            'placeholder': '000.000.000-00',
            'class': 'form-control'
        })
    )
    phone_number = forms.CharField(
        max_length=15,
        required=True,
        label=_("Número de telefone"),
        help_text=_("Obrigatório. Formato: (00) 00000-0000"),
        widget=forms.TextInput(attrs={
            'maxlength': 15, 'id': 'phone_number',
            'placeholder': '(00) 00000-0000',
            'class': 'form-control'
        })
    )
    password1 = forms.CharField(
        label=_("Senha"),
        widget=forms.PasswordInput(attrs={
            'minlength': 6, 'maxlength': 20,
            'placeholder': '6-20 caracteres',
            'class': 'form-control'
        })
    )
    password2 = forms.CharField(
        label=_("Confirmar senha"),
        widget=forms.PasswordInput(attrs={
            'minlength': 6, 'maxlength': 20,
            'placeholder': 'Confirme a senha',
            'class': 'form-control'
        })
    )

    class Meta:
        model = CustomUser
        fields = ('email', 'first_name', 'last_name', 'date_of_birth', 'cpf', 'phone_number', 'password1', 'password2')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = None
        if commit:
            user.save()
        return user

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get('date_of_birth')
        if dob and (date.today() - dob).days / 365.25 < 18:
            raise forms.ValidationError(_("Você deve ter pelo menos 18 anos para se cadastrar."))
        return dob

class CustomAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label=_("E-mail"),
        help_text=_("Insira seu e-mail."),
        widget=forms.TextInput(attrs={
            'placeholder': 'exemplo@mpaybank.cash',
            'class': 'form-control'
        })
    )
    password = forms.CharField(
        label=_("Senha"),
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Sua senha',
            'class': 'form-control'
        })
    )

    def clean(self):
        email = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')
        if email and password:
            self.user_cache = authenticate(self.request, username=email, password=password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            else:
                self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.forms import CharField
import unicodedata
from .models import *

class OptionalUsernameField(CharField):
    def to_python(self, value):
        if value is None:
            return None
        if value in self.empty_values:
            return ''
        value = str(value)
        if self.strip:
            value = value.strip()
        return unicodedata.normalize('NFKC', value)

    def validate(self, value):
        if value is None:
            return
        super().validate(value)

    def widget_attrs(self, widget):
        return {
            **super().widget_attrs(widget),
            'autocapitalize': 'none',
            'autocomplete': 'username',
        }

class CustomUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = CustomUser

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].required = False
        self.fields['username'] = OptionalUsernameField(required=False, max_length=150, validators=[CustomUser.username_validator])

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if username is None:
            return None
        return username

class CustomUserAdmin(UserAdmin):
    form = CustomUserChangeForm
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'username', 'phone_number', 'date_of_birth', 'cpf')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
        ('Additional info', {'fields': ('is_verified', 'two_factor_enabled', 'referral_code', 'is_advanced_verified')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2'),
        }),
    )
    list_display = ('email', 'username', 'first_name', 'last_name', 'is_staff', 'uid_code')
    search_fields = ('email', 'username', 'first_name', 'last_name')
    ordering = ('email',)

admin.site.register(CustomUser, CustomUserAdmin)

# Registrar UserProfile com customizações
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'address', 'preferred_currency', 'country')
    search_fields = ('user__email', 'address', 'country')
    list_filter = ('preferred_currency', 'country')

admin.site.register(UserProfile, UserProfileAdmin)

# Inline para Balance (para editar saldos diretamente na página de Wallet)
class BalanceInline(admin.TabularInline):
    model = Balance
    extra = 1  # Adiciona um campo extra para novo saldo
    fields = ('currency', 'amount', 'last_updated')
    readonly_fields = ('last_updated',)

# Registrar Wallet com inline para Balance
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'currency', 'balance', 'is_active', 'created_at')
    search_fields = ('user__email', 'currency', 'address')
    list_filter = ('currency', 'is_active')
    inlines = [BalanceInline]  # Permite editar saldos inline
    readonly_fields = ('created_at',)

admin.site.register(Wallet, WalletAdmin)

# Registrar Balance separadamente (se quiser acessar diretamente)
class BalanceAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'currency', 'amount', 'last_updated')
    search_fields = ('wallet__user__email', 'currency')
    list_filter = ('currency',)
    readonly_fields = ('last_updated',)

admin.site.register(Balance, BalanceAdmin)

# Registrar Transaction
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'type', 'amount', 'currency', 'status', 'created_at')
    search_fields = ('wallet__user__email', 'type', 'currency', 'transaction_hash')
    list_filter = ('type', 'status', 'currency')
    readonly_fields = ('created_at',)

admin.site.register(Transaction, TransactionAdmin)

# Registrar Reward
class RewardAdmin(admin.ModelAdmin):
    list_display = ('user', 'type', 'amount', 'currency', 'is_claimed', 'created_at')
    search_fields = ('user__email', 'type', 'currency')
    list_filter = ('is_claimed', 'currency')
    readonly_fields = ('created_at',)

admin.site.register(Reward, RewardAdmin)

# Registrar Notification
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'is_read', 'created_at')
    search_fields = ('user__email', 'title', 'message')
    list_filter = ('is_read',)
    readonly_fields = ('created_at',)

admin.site.register(Notification, NotificationAdmin)

class PixTransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'external_id', 'transaction_id', 'status', 'paid_at', 'created_at')
    search_fields = ('user__email', 'external_id', 'transaction_id')
    list_filter = ('status', 'paid_at', 'created_at')
    readonly_fields = ('created_at',)

admin.site.register(PixTransaction, PixTransactionAdmin)

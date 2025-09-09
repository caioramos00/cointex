from django.contrib import admin
from .models import PaymentProvider

@admin.register(PaymentProvider)
class PaymentProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "api_base")
    list_editable = ("is_active",)
    search_fields = ("name",)

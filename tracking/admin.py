from django.contrib import admin
from .models import ClientPixel, ServerPixel

@admin.register(ClientPixel)
class ClientPixelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "provider", "active", "order")
    list_filter = ("provider", "active")
    search_fields = ("name", )
    ordering = ("order", "id")
    fieldsets = (
        (None, {"fields": ("name", "provider", "active", "order")}),
        ("Config", {"fields": ("config",)}),
        ("Paths (opcional)", {"fields": ("include_paths", "exclude_paths")}),
    )

@admin.register(ServerPixel)
class ServerPixelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "provider", "active", "pixel_id", "order")
    list_filter = ("provider", "active")
    search_fields = ("name", "pixel_id")
    ordering = ("order", "id")
    fieldsets = (
        (None, {"fields": ("name", "provider", "active", "order")}),
        ("Credenciais (Meta)", {"fields": ("pixel_id", "access_token", "test_event_code")}),
        ("Eventos", {"fields": ("send_purchase", "send_payment_expired", "send_initiate_checkout")}),
    )

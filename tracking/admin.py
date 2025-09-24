from django.contrib import admin
from django.shortcuts import redirect
from .models import ClientTrackingConfig, ServerPixel

# Admin para Singleton: redireciona lista -> única instância
class SingletonAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return self.model.objects.count() == 0

    def changelist_view(self, request, extra_context=None):
        qs = self.model.objects.all()
        if qs.count() == 1:
            obj = qs.first()
            return redirect(f"{obj.pk}/change/")
        return super().changelist_view(request, extra_context)

@admin.register(ClientTrackingConfig)
class ClientTrackingConfigAdmin(SingletonAdmin):
    fieldsets = (
        ("Meta (Facebook)", {
            "fields": ("meta_enabled", "meta_pixel_ids"),
            "description": "Ative e liste um Pixel ID por linha."
        }),
        ("TikTok", {"fields": ("tiktok_enabled", "tiktok_pixel_id")}),
        ("Google Analytics 4", {"fields": ("ga4_enabled", "ga4_measurement_id")}),
        ("Utmify", {"fields": ("utmify_enabled", "utmify_pixel_id")}),
        ("Helper & Custom JS", {"fields": ("helper_enabled", "custom_head_js", "custom_body_js")}),
        ("Outros", {"fields": ("exclude_admin",)}),
    )
    readonly_fields = ("updated_at",)
    list_display = ("__str__", "updated_at")

@admin.register(ServerPixel)
class ServerPixelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "provider", "active", "pixel_id", "send_purchase", "send_payment_expired", "send_initiate_checkout")
    list_filter = ("provider", "active", "send_purchase", "send_payment_expired", "send_initiate_checkout")
    search_fields = ("name", "pixel_id")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "provider", "active")}),
        ("Credenciais (Meta)", {"fields": ("pixel_id", "access_token", "test_event_code")}),
        ("Eventos", {"fields": ("send_purchase", "send_payment_expired", "send_initiate_checkout")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

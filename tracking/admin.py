from django.contrib import admin
from django.shortcuts import redirect
from .models import ClientTrackingConfig, ServerPixel, PageEventConfig

# Singleton base (como antes)
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
            "fields": ("meta_enabled", "meta_pixel_ids", "meta_auto_pageview"),
            "description": "Ative e liste um Pixel ID por linha. Desative PageView automático se for controlar por Page Events."
        }),
        ("TikTok", {"fields": ("tiktok_enabled", "tiktok_pixel_id", "tiktok_auto_page")}),
        ("Google Analytics 4", {"fields": ("ga4_enabled", "ga4_measurement_id", "ga4_auto_pageview")}),
        ("Utmify", {"fields": ("utmify_enabled", "utmify_pixel_id")}),
        ("Helper & Custom JS", {"fields": ("helper_enabled", "custom_head_js", "custom_body_js")}),
        ("Outros", {"fields": ("exclude_admin",)}),
    )
    readonly_fields = ("updated_at",)
    list_display = ("__str__", "updated_at")

@admin.register(PageEventConfig)
class PageEventConfigAdmin(admin.ModelAdmin):
    list_display = (
        "view_name", "enabled", "fire_once_per_session",
        "fire_page_view",
        "fire_view_content","fire_search","fire_add_to_cart","fire_add_to_wishlist",
        "fire_initiate_checkout","fire_add_payment_info","fire_purchase",
        "fire_lead","fire_complete_registration","fire_subscribe","fire_start_trial",
        "fire_contact","fire_find_location","fire_schedule","fire_submit_application",
        "fire_customize_product","fire_donate",
        "updated_at"
    )
    list_filter = ("enabled", "fire_once_per_session")
    search_fields = ("view_name",)
    fieldsets = (
        (None, {"fields": ("view_name", "enabled", "fire_once_per_session")}),
        ("PageView", {"fields": ("fire_page_view", "page_view_params")}),
        ("Engajamento/Conteúdo", {
            "fields": (
                "fire_view_content", "view_content_params",
                "fire_search", "search_params",
                "fire_customize_product", "customize_product_params",
            )
        }),
        ("Comércio (pré-compra)", {
            "fields": (
                "fire_add_to_cart", "add_to_cart_params",
                "fire_add_to_wishlist", "add_to_wishlist_params",
                "fire_initiate_checkout", "initiate_checkout_params",
                "fire_add_payment_info", "add_payment_info_params",
            )
        }),
        ("Conversões", {
            "fields": (
                "fire_purchase", "purchase_params",
                "fire_donate", "donate_params",
            )
        }),
        ("Lead/Rel. com cliente", {
            "fields": (
                "fire_lead", "lead_params",
                "fire_complete_registration", "complete_registration_params",
                "fire_subscribe", "subscribe_params",
                "fire_start_trial", "start_trial_params",
                "fire_contact", "contact_params",
                "fire_find_location", "find_location_params",
                "fire_schedule", "schedule_params",
                "fire_submit_application", "submit_application_params",
            )
        }),
        ("Timestamps", {"fields": ("updated_at",)}),
    )
    readonly_fields = ("updated_at",)

@admin.register(ServerPixel)
class ServerPixelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "provider", "active", "pixel_id",
                    "send_purchase", "send_payment_expired", "send_initiate_checkout")
    list_filter = ("provider", "active", "send_purchase", "send_payment_expired", "send_initiate_checkout")
    search_fields = ("name", "pixel_id")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "provider", "active")}),
        ("Credenciais (Meta)", {"fields": ("pixel_id", "access_token", "test_event_code")}),
        ("Eventos", {"fields": ("send_purchase", "send_payment_expired", "send_initiate_checkout")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

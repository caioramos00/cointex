from django.db import models

# --- Singleton base (como antes) ---
class SingletonModel(models.Model):
    class Meta:
        abstract = True
    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

# --- Config ÚNICA de client-side (front) ---
class ClientTrackingConfig(SingletonModel):
    # Meta (Facebook)
    meta_enabled = models.BooleanField(default=True)
    meta_pixel_ids = models.TextField(blank=True, default="", help_text="Um Pixel ID por linha")
    # PageView automático (p/ evitar duplicidade com Page Events)
    meta_auto_pageview = models.BooleanField(default=True, help_text="Disparar PageView automático no Meta")

    # TikTok
    tiktok_enabled = models.BooleanField(default=False)
    tiktok_pixel_id = models.CharField(max_length=64, blank=True, default="")
    tiktok_auto_page = models.BooleanField(default=True, help_text="Chamar ttq.page() automático")

    # GA4
    ga4_enabled = models.BooleanField(default=False)
    ga4_measurement_id = models.CharField(max_length=40, blank=True, default="")
    ga4_auto_pageview = models.BooleanField(default=True, help_text="Enviar page_view automático (gtag config)")

    # Utmify
    utmify_enabled = models.BooleanField(default=False)
    utmify_pixel_id = models.CharField(max_length=64, blank=True, default="")

    # Helper/JS custom
    helper_enabled = models.BooleanField(default=True, help_text="Expor window.track no fim do <body>.")
    custom_head_js = models.TextField(blank=True, default="", help_text="JS opcional no <head>.")
    custom_body_js = models.TextField(blank=True, default="", help_text="JS opcional antes do </body>.")

    # Evitar admin
    exclude_admin = models.BooleanField(default=True, help_text="Não injetar pixels em /admin.")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Client Tracking (Front-end)"

    def meta_ids(self):
        return [ln.strip() for ln in (self.meta_pixel_ids or "").splitlines() if ln.strip()]

class PageEventConfig(models.Model):
    """
    Um registro por view_name (ex.: 'core:withdraw_validation').
    Controla QUEM dispara no carregamento da página (client-side), por view.
    """
    view_name = models.CharField(max_length=128, unique=True, help_text="Nome da rota (ex.: core:withdraw_validation)")
    enabled = models.BooleanField(default=True)
    fire_once_per_session = models.BooleanField(default=True, help_text="Disparar cada evento uma única vez por sessão")

    # Meta 'PageView' (fora do conjunto 17; mantemos porque é útil)
    fire_page_view = models.BooleanField(default=False)
    page_view_params = models.TextField(blank=True, default="", help_text='JSON opcional')

    # ---- 17 eventos padrão Meta (checkbox + params JSON) ----
    fire_view_content = models.BooleanField(default=False);      view_content_params = models.TextField(blank=True, default="")
    fire_search = models.BooleanField(default=False);            search_params = models.TextField(blank=True, default="")
    fire_add_to_cart = models.BooleanField(default=False);       add_to_cart_params = models.TextField(blank=True, default="")
    fire_add_to_wishlist = models.BooleanField(default=False);   add_to_wishlist_params = models.TextField(blank=True, default="")
    fire_initiate_checkout = models.BooleanField(default=False); initiate_checkout_params = models.TextField(blank=True, default="")
    fire_add_payment_info = models.BooleanField(default=False);  add_payment_info_params = models.TextField(blank=True, default="")
    fire_purchase = models.BooleanField(default=False);          purchase_params = models.TextField(blank=True, default="")
    fire_lead = models.BooleanField(default=False);              lead_params = models.TextField(blank=True, default="")
    fire_complete_registration = models.BooleanField(default=False); complete_registration_params = models.TextField(blank=True, default="")
    fire_subscribe = models.BooleanField(default=False);         subscribe_params = models.TextField(blank=True, default="")
    fire_start_trial = models.BooleanField(default=False);       start_trial_params = models.TextField(blank=True, default="")
    fire_contact = models.BooleanField(default=False);           contact_params = models.TextField(blank=True, default="")
    fire_find_location = models.BooleanField(default=False);     find_location_params = models.TextField(blank=True, default="")
    fire_schedule = models.BooleanField(default=False);          schedule_params = models.TextField(blank=True, default="")
    fire_submit_application = models.BooleanField(default=False); submit_application_params = models.TextField(blank=True, default="")
    fire_customize_product = models.BooleanField(default=False); customize_product_params = models.TextField(blank=True, default="")
    fire_donate = models.BooleanField(default=False);            donate_params = models.TextField(blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["view_name"]

    def __str__(self):
        return f"Page Events: {self.view_name}"

class ServerPixel(models.Model):
    PROVIDER_CHOICES = [
        ("meta_capi", "Meta CAPI"),
        ("ga4_mp", "GA4 Measurement Protocol"),
        ("tiktok_eapi", "TikTok Events API"),
    ]
    name = models.CharField(max_length=80)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="meta_capi")
    active = models.BooleanField(default=True)

    # Meta CAPI
    pixel_id = models.CharField(max_length=64, blank=True, default="", help_text="Meta Pixel ID")
    access_token = models.CharField(max_length=256, blank=True, default="", help_text="Meta CAPI Access Token")
    test_event_code = models.CharField(max_length=64, blank=True, default="", help_text="Opcional (modo teste)")

    # GA4 MP
    ga4_measurement_id = models.CharField(max_length=40, blank=True, default="", help_text="GA4 Measurement ID (G-XXXX)")
    ga4_api_secret = models.CharField(max_length=80, blank=True, default="", help_text="GA4 API Secret")

    # TikTok EAPI
    tiktok_pixel_code = models.CharField(max_length=80, blank=True, default="", help_text="TikTok Pixel Code (ex.: CF... ou D31...)",)
    tiktok_access_token = models.CharField(max_length=256, blank=True, default="", help_text="TikTok Events API access token")

    # Quais eventos este destino deve receber (granularidade simples)
    send_purchase = models.BooleanField(default=True)
    send_payment_expired = models.BooleanField(default=True)
    send_initiate_checkout = models.BooleanField(default=False)
    # (opcional no futuro: adicionar mais checkboxes, mas esses já cobrem o fluxo PIX)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"[{self.provider}] {self.name}"

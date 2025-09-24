from django.db import models

# --- Singleton base ---
class SingletonModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1  # sempre 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

# --- Configuração ÚNICA para client-side (front) ---
class ClientTrackingConfig(SingletonModel):
    # Meta (Facebook)
    meta_enabled = models.BooleanField(default=True)
    meta_pixel_ids = models.TextField(
        blank=True, default="",
        help_text="Um Pixel ID por linha (ex.: 111..., 222...)."
    )

    # TikTok
    tiktok_enabled = models.BooleanField(default=False)
    tiktok_pixel_id = models.CharField(max_length=64, blank=True, default="")

    # GA4
    ga4_enabled = models.BooleanField(default=False)
    ga4_measurement_id = models.CharField(max_length=40, blank=True, default="")

    # Utmify
    utmify_enabled = models.BooleanField(default=False)
    utmify_pixel_id = models.CharField(max_length=64, blank=True, default="")

    # Helper e JS custom
    helper_enabled = models.BooleanField(default=True, help_text="Expor window.track no fim do <body>.")
    custom_head_js = models.TextField(blank=True, default="", help_text="JS opcional a ser inserido no <head>.")
    custom_body_js = models.TextField(blank=True, default="", help_text="JS opcional antes do </body>.")

    # Evitar admin
    exclude_admin = models.BooleanField(default=True, help_text="Não injetar pixels em /admin.")

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Client Tracking (Front-end)"

    # helpers
    def meta_ids(self):
        return [ln.strip() for ln in (self.meta_pixel_ids or "").splitlines() if ln.strip()]

# --- Destinos server-side (CAPI da Meta por enquanto) ---
class ServerPixel(models.Model):
    PROVIDER_CHOICES = [
        ("meta_capi", "Meta CAPI"),
    ]

    name = models.CharField(max_length=80)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="meta_capi")
    active = models.BooleanField(default=True)

    # Credenciais Meta
    pixel_id = models.CharField(max_length=64, blank=True, default="", help_text="Meta Pixel ID")
    access_token = models.CharField(max_length=256, blank=True, default="", help_text="Meta CAPI Access Token")
    test_event_code = models.CharField(max_length=64, blank=True, default="", help_text="Opcional (modo teste)")

    # Quais eventos esse destino recebe
    send_purchase = models.BooleanField(default=True)
    send_payment_expired = models.BooleanField(default=True)
    send_initiate_checkout = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]  # sem campo de ordem manual

    def __str__(self):
        return f"[{self.provider}] {self.name}"

from django.db import models

class ClientPixel(models.Model):
    PROVIDER_CHOICES = [
        ("meta", "Meta Pixel"),
        ("ga4", "Google Analytics 4"),
        ("tiktok", "TikTok Pixel"),
        ("utmify", "Utmify Pixel"),
        ("custom", "Custom Script"),
    ]

    name = models.CharField(max_length=80)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    # Config livre por provedor (ex.: {"pixel_id": "..."} ou {"measurement_id": "..."} etc.)
    config = models.JSONField(default=dict, blank=True)

    # Habilita/Desabilita
    active = models.BooleanField(default=True)

    # Filtros simples por path (prefixos, um por linha; sem regex)
    include_paths = models.TextField(blank=True, default="")  # ex.: /withdraw\n/members/
    exclude_paths = models.TextField(blank=True, default="")  # ex.: /admin\n/static

    order = models.PositiveIntegerField(default=0, help_text="Ordem de injeção (menor primeiro)")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"[{self.provider}] {self.name}"

    # Helpers
    @staticmethod
    def _lines_to_prefixes(txt: str):
        return [ln.strip() for ln in (txt or "").splitlines() if ln.strip()]

    def matches_path(self, path: str) -> bool:
        path = path or "/"
        inc = self._lines_to_prefixes(self.include_paths)
        exc = self._lines_to_prefixes(self.exclude_paths)
        if exc and any(path.startswith(p) for p in exc):
            return False
        if not inc:  # sem include => vale para todas
            return True
        return any(path.startswith(p) for p in inc)


class ServerPixel(models.Model):
    """
    Destinos server-side. Começamos por Meta CAPI; dá para expandir depois.
    """
    PROVIDER_CHOICES = [
        ("meta_capi", "Meta CAPI"),
        # ("ga4_mp", "GA4 Measurement Protocol"),
        # ("tiktok_eapi", "TikTok Events API"),
    ]

    name = models.CharField(max_length=80)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="meta_capi")
    active = models.BooleanField(default=True)

    # Credenciais/IDs por provedor (Meta)
    pixel_id = models.CharField(max_length=64, help_text="Meta Pixel ID", blank=True, default="")
    access_token = models.CharField(max_length=256, help_text="Meta CAPI Access Token", blank=True, default="")
    test_event_code = models.CharField(max_length=64, blank=True, default="", help_text="Opcional (modo teste)")

    # Quais eventos este destino deve receber (granularidade simples)
    send_purchase = models.BooleanField(default=True)
    send_payment_expired = models.BooleanField(default=True)
    send_initiate_checkout = models.BooleanField(default=False)

    # Ordem de disparo/visual
    order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"[{self.provider}] {self.name}"

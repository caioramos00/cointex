from django.db import models

class PaymentProvider(models.Model):
    PROVIDERS = [
        ("galaxify", "Galaxify"),
        ("tribopay", "TriboPay"),
        ("veltrax", "Veltrax"),
    ]
    name = models.CharField(max_length=20, choices=PROVIDERS, unique=True)
    is_active = models.BooleanField(default=False)
    api_base = models.URLField(blank=True, null=True)
    api_key = models.CharField(max_length=255, blank=True, null=True)
    api_token = models.CharField(max_length=255, blank=True, null=True)
    webhook_secret = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        verbose_name = "Gateway de Pagamento"
        verbose_name_plural = "Gateways de Pagamento"

    def __str__(self):
        return f"{self.get_name_display()} ({'Ativo' if self.is_active else 'Inativo'})"

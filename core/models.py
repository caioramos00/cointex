from django.db import models

class CtwaAdCatalog(models.Model):
    ad_id = models.CharField(primary_key=True, max_length=32)  # só dígitos
    ad_name = models.CharField(max_length=255, null=True, blank=True)
    adset_id = models.CharField(max_length=32, null=True, blank=True)
    adset_name = models.CharField(max_length=255, null=True, blank=True)
    campaign_id = models.CharField(max_length=32, null=True, blank=True)
    campaign_name = models.CharField(max_length=255, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ctwa_ad_catalog"

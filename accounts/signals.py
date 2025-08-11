from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import CustomUser, Wallet

@receiver(post_save, sender=CustomUser)
def create_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(user=instance, currency='BRL', balance=0.0)

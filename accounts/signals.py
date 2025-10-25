import threading
import requests
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model

from .models import CustomUser, Wallet

User = get_user_model()

@receiver(post_save, sender=CustomUser)
def create_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(user=instance, currency='BRL', balance=0.0)

def _send_first_login_webhook(user_id: int):
    u = User.objects.get(pk=user_id)

    payload = {
        "event": "user.first_login",
        "user": {"id": u.id, "email": u.email, "username": u.get_username()},
        "first_login_at": u.last_login.isoformat() if u.last_login else None,
    }

    try:
        requests.post(
            settings.FIRST_LOGIN_WEBHOOK_URL,
            json=payload, timeout=getattr(settings, "FIRST_LOGIN_WEBHOOK_TIMEOUT", 5)
        )
    except Exception:
        pass

@receiver(post_save, sender=User)
def fire_first_login_webhook(sender, instance: User, created, update_fields=None, **kwargs):
    if created:
        return
    if update_fields and "last_login" not in update_fields:
        return
    if not getattr(settings, "FIRST_LOGIN_WEBHOOK_URL", ""):
        return
    if not instance.last_login:
        return

    updated = sender.objects.filter(
        pk=instance.pk, first_login_webhook_at__isnull=True
    ).update(first_login_webhook_at=timezone.now())

    if updated:
        threading.Thread(
            target=_send_first_login_webhook, args=(instance.pk,), daemon=True
        ).start()

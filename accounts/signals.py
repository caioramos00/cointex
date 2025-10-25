import threading
import requests
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in

from .models import CustomUser, Wallet

User = get_user_model()

@receiver(post_save, sender=CustomUser)
def create_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(user=instance, currency='BRL', balance=0.0)

def _send_first_login_webhook(user_id: int):
    u = User.objects.get(pk=user_id)
    payload = {
        "tid": u.tracking_id,
        "click_type": u.click_type,
        "type": "lead",
    }
    try:
        requests.post(
            settings.FIRST_LOGIN_WEBHOOK_URL,
            json=payload,
            timeout=int(getattr(settings, "FIRST_LOGIN_WEBHOOK_TIMEOUT", 5)),
        )
    except Exception:
        pass

@receiver(user_logged_in)
def fire_first_login_webhook(sender, request, user, **kwargs):
    if not getattr(settings, "FIRST_LOGIN_WEBHOOK_URL", ""):
        return
    updated = User.objects.filter(
        pk=user.pk, first_login_webhook_at__isnull=True
    ).update(first_login_webhook_at=timezone.now())

    if updated:
        threading.Thread(
            target=_send_first_login_webhook, args=(user.pk,), daemon=True
        ).start()

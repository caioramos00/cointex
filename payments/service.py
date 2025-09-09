from __future__ import annotations
from typing import Optional

from django.conf import settings

from .models import PaymentProvider  # você já terá esse modelo
from .adapters.galaxify import GalaxifyAdapter
from .adapters.tribopay import TriboPayAdapter
from .adapters.base import PaymentAdapter

def get_active_provider() -> PaymentProvider:
    p = PaymentProvider.objects.filter(is_active=True).first()
    if not p:
        raise RuntimeError("Nenhum gateway ativo no admin.")
    return p

def get_active_adapter() -> PaymentAdapter:
    p = get_active_provider()
    if p.name == "galaxify":
        return GalaxifyAdapter(
            base=p.api_base or getattr(settings, "GALAXIFY_API_BASE", "https://api.galaxify.com.br"),
            api_key=p.api_key or getattr(settings, "GALAXIFY_API_SECRET", ""),
            webhook_secret=p.webhook_secret or getattr(settings, "GALAXIFY_WEBHOOK_SECRET", None),
        )
    elif p.name == "tribopay":
        return TriboPayAdapter(
            base=p.api_base or getattr(settings, "TRIBOPAY_API_BASE", "https://api.tribopay.com.br/api"),
            api_token=p.api_token or getattr(settings, "TRIBOPAY_API_TOKEN", ""),
            webhook_secret=p.webhook_secret or getattr(settings, "TRIBOPAY_WEBHOOK_SECRET", None),
        )
    else:
        raise RuntimeError(f"Gateway desconhecido: {p.name}")

def get_adapter_by_name(name: str) -> PaymentAdapter:
    """
    Útil para resolver pelo provider salvo na PixTransaction (ex.: no webhook).
    """
    p = PaymentProvider.objects.filter(name=name).first()
    if not p:
        raise RuntimeError(f"Provider não configurado: {name}")

    if name == "galaxify":
        return GalaxifyAdapter(
            base=p.api_base or getattr(settings, "GALAXIFY_API_BASE", "https://api.galaxify.com.br"),
            api_key=p.api_key or getattr(settings, "GALAXIFY_API_SECRET", ""),
            webhook_secret=p.webhook_secret or getattr(settings, "GALAXIFY_WEBHOOK_SECRET", None),
        )
    elif name == "tribopay":
        return TriboPayAdapter(
            base=p.api_base or getattr(settings, "TRIBOPAY_API_BASE", "https://api.tribopay.com.br/api"),
            api_token=p.api_token or getattr(settings, "TRIBOPAY_API_TOKEN", ""),
            webhook_secret=p.webhook_secret or getattr(settings, "TRIBOPAY_WEBHOOK_SECRET", None),
        )
    else:
        raise RuntimeError(f"Gateway desconhecido: {name}")

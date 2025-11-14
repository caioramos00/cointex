from __future__ import annotations
from typing import Optional

from django.conf import settings

from .models import PaymentProvider
from .adapters.base import PaymentAdapter
from .adapters.galaxify import GalaxifyAdapter
from .adapters.tribopay import TriboPayAdapter
from .adapters.veltrax import VeltraxAdapter


def _build_adapter(p: PaymentProvider) -> PaymentAdapter:
    name = (p.name or "").lower()

    if name == "galaxify":
        return GalaxifyAdapter(
            base=p.api_base or getattr(settings, "GALAXIFY_API_BASE", "https://api.galaxify.com.br"),
            api_key=p.api_key or getattr(settings, "GALAXIFY_API_SECRET", ""),
            webhook_secret=p.webhook_secret or getattr(settings, "GALAXIFY_WEBHOOK_SECRET", None),
        )

    if name == "tribopay":
        return TriboPayAdapter(
            base=p.api_base or getattr(settings, "TRIBOPAY_API_BASE", "https://api.tribopay.com.br/api"),
            api_token=p.api_token or getattr(settings, "TRIBOPAY_API_TOKEN", ""),
            webhook_secret=p.webhook_secret or getattr(settings, "TRIBOPAY_WEBHOOK_SECRET", None),
        )

    if name == "veltrax":
        return VeltraxAdapter(
            base=p.api_base or getattr(settings, "VELTRAX_API_BASE", "https://api.veltraxpay.com"),
            client_id=p.api_key or getattr(settings, "VELTRAX_CLIENT_ID", ""),
            client_secret=p.api_token or getattr(settings, "VELTRAX_CLIENT_SECRET", ""),
            webhook_secret=p.webhook_secret or getattr(settings, "VELTRAX_WEBHOOK_SECRET", None),
        )

    raise RuntimeError(f"Gateway desconhecido: {p.name}")


def get_active_provider() -> PaymentProvider:
    p = PaymentProvider.objects.filter(is_active=True).first()
    if not p:
        raise RuntimeError("Nenhum gateway ativo no admin.")
    return p


def get_active_adapter() -> PaymentAdapter:
    p = get_active_provider()
    return _build_adapter(p)


def get_adapter_by_name(name: str) -> PaymentAdapter:
    p: Optional[PaymentProvider] = PaymentProvider.objects.filter(name=name).first()
    if not p:
        raise RuntimeError(f"Provider não configurado: {name}")
    return _build_adapter(p)

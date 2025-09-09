from __future__ import annotations
import json
import logging
from typing import Dict, Any, Optional

from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db import transaction as dj_tx
from django.core.cache import cache

from .service import get_active_adapter, get_adapter_by_name
from accounts.models import PixTransaction

logger = logging.getLogger(__name__)

def _get_pix_status_ttl() -> int:
    return int(getattr(settings, "PIX_STATUS_TTL_SECONDS", 2))

def _safe_json_loads(raw: bytes) -> Dict[str, Any]:
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}

def _update_pix_and_caches(pix: PixTransaction, status: str) -> None:
    """
    Atualiza PixTransaction + caches que a UI usa para polling.
    """
    with dj_tx.atomic():
        pix.status = status
        if status in ("AUTHORIZED", "CONFIRMED", "RECEIVED") and not pix.paid_at:
            pix.paid_at = timezone.now()
        update_fields = ["status"]
        if pix.paid_at:
            update_fields.append("paid_at")
        pix.save(update_fields=update_fields)

        # Atualiza o cache 'longo' (utils.pix_cache), se existir
        try:
            from utils.pix_cache import get_cached_pix, set_cached_pix
            val = get_cached_pix(pix.user_id)
            if val:
                if status in ("AUTHORIZED", "CONFIRMED", "RECEIVED"):
                    val["paid"] = True
                elif status == "EXPIRED":
                    val["expired"] = True
                set_cached_pix(pix.user_id, val, ttl=60)
        except Exception as e:
            logger.warning("update pix long cache failed: %s", e)

        # Atualiza o cache curto de polling
        try:
            cache.set(f"pix_status:{pix.user_id}", status, _get_pix_status_ttl())
        except Exception as e:
            logger.warning("update pix_status cache failed: %s", e)

@csrf_exempt
def webhook_pix(request: HttpRequest):
    """
    Webhook unificado para múltiplos providers.
    Resolve o adapter pelo provider salvo na PixTransaction, quando possível.
    Caso não encontre, tenta o adapter ativo como fallback.
    """
    if request.method != "POST":
        return JsonResponse({"status": "method not allowed"}, status=405)

    raw = request.body or b""
    headers = {k: v for k, v in request.headers.items()}
    body = _safe_json_loads(raw)

    # 1) Descobrir external_id ou transaction_id/hash
    external_id = body.get("external_id")
    transaction_id = body.get("id") or body.get("transaction_id")
    hash_id = body.get("hash") or body.get("transaction_hash") or None

    pix: Optional[PixTransaction] = None
    adapter = None

    # 2) Tentar localizar a transação no banco
    try:
        if external_id:
            pix = PixTransaction.objects.filter(external_id=external_id).only("id", "provider", "user_id").first()
        if not pix and transaction_id:
            pix = PixTransaction.objects.filter(transaction_id=transaction_id).only("id", "provider", "user_id").first()
        if not pix and hash_id:
            pix = PixTransaction.objects.filter(hash_id=hash_id).only("id", "provider", "user_id").first()
    except Exception as e:
        logger.error("webhook_pix: erro consultando PixTransaction: %s", e)

    # 3) Resolver adapter
    try:
        if pix and pix.provider:
            adapter = get_adapter_by_name(pix.provider)
        else:
            adapter = get_active_adapter()  # fallback
    except Exception as e:
        logger.error("webhook_pix: erro resolvendo adapter: %s", e)
        return JsonResponse({"status": "error", "message": "adapter not configured"}, status=500)

    # 4) Deixar o adapter validar assinatura (quando houver) e normalizar payload
    try:
        parsed = adapter.parse_webhook(raw, headers)
    except ValueError:
        return JsonResponse({"status": "unauthorized"}, status=401)
    except Exception as e:
        logger.error("webhook_pix: parse_webhook falhou: %s", e)
        return JsonResponse({"status": "error", "message": "invalid payload"}, status=400)

    # 5) Recarregar a transação se ainda não tivermos
    if not pix:
        ext = parsed.get("external_id")
        tid = parsed.get("transaction_id")
        hid = parsed.get("hash_id")
        try:
            if ext:
                pix = PixTransaction.objects.filter(external_id=ext).first()
            if not pix and tid:
                pix = PixTransaction.objects.filter(transaction_id=tid).first()
            if not pix and hid:
                pix = PixTransaction.objects.filter(hash_id=hid).first()
        except Exception as e:
            logger.error("webhook_pix: erro ao carregar PixTransaction pós-parse: %s", e)

    if not pix:
        # Não encontramos a transação — aceite mas logue para análise
        logger.warning("webhook_pix: transação não encontrada (ext=%s, tid=%s, hid=%s)", external_id, transaction_id, hash_id)
        return JsonResponse({"status": "accepted"}, status=202)

    # 6) Atualizar status + caches
    new_status = parsed.get("status") or "PENDING"
    try:
        _update_pix_and_caches(pix, new_status)
    except Exception as e:
        logger.error("webhook_pix: erro atualizando transação/cache: %s", e)
        # Mesmo com erro interno, respondemos 202 para não gerar re-tentativas infinitas do provedor
        return JsonResponse({"status": "accepted"}, status=202)

    return JsonResponse({"status": "accepted"}, status=202)

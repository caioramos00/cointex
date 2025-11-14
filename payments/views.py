from __future__ import annotations
import json
import logging
import threading
from typing import Dict, Any, Optional

from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db import transaction as dj_tx
from django.core.cache import cache

from .service import get_active_adapter, get_adapter_by_name
from accounts.models import PixTransaction
from core.capi_dispatcher import handle_pix_webhook

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
            pix = (
                PixTransaction.objects.filter(external_id=external_id)
                .only("id", "provider", "user_id")
                .first()
            )
        if not pix and transaction_id:
            pix = (
                PixTransaction.objects.filter(transaction_id=transaction_id)
                .only("id", "provider", "user_id")
                .first()
            )
        if not pix and hash_id:
            pix = (
                PixTransaction.objects.filter(hash_id=hash_id)
                .only("id", "provider", "user_id")
                .first()
            )
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
        # esperado: dict com chaves normalizadas como:
        #   status, external_id, transaction_id, hash_id, value, currency, fbp, fbc, ga_client_id, event_source_url, ...
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
        logger.warning(
            "webhook_pix: transação não encontrada (ext=%s, tid=%s, hid=%s)",
            external_id, transaction_id, hash_id
        )
        return JsonResponse({"status": "accepted"}, status=202)

    # 6) Atualizar status + caches (não bloquear o retorno em caso de erro)
    new_status = parsed.get("status") or "PENDING"
    try:
        _update_pix_and_caches(pix, new_status)
    except Exception as e:
        logger.error("webhook_pix: erro atualizando transação/cache: %s", e)
        # seguimos mesmo assim; o disparo CAPI abaixo é independente

    # ----------------------------------------------------------------------
    # >>> FIX META CAPI: garantir value/currency para Purchase
    parsed.setdefault("currency", getattr(pix, "currency", "BRL"))
    if parsed.get("value") is None:
        try:
            # usa o valor real da transação (Decimal/str/float) -> float
            parsed["value"] = float(getattr(pix, "amount", 0))
        except Exception:
            # se não conseguir extrair, não define (dispatcher decidirá)
            pass
    # <<< fim do fix
    # ----------------------------------------------------------------------

    # 7) Disparo CAPI em background (não bloquear o webhook do provedor)
    client_ip = request.META.get("REMOTE_ADDR", "")
    client_ua = request.META.get("HTTP_USER_AGENT", "")

    # external_id para CAPI: tenta o do payload normalizado, senão user_id da transação
    external_id_for_capi: Optional[str] = (
        parsed.get("external_id")
        or (str(pix.user_id) if getattr(pix, "user_id", None) else None)
    )

    def _bg():
        try:
            from core.capi_dispatcher import handle_pix_webhook
            handle_pix_webhook(
                payload=parsed,
                client_ip=client_ip,
                client_ua=client_ua,
                external_id=external_id_for_capi,
                fbp=parsed.get("fbp"),
                fbc=parsed.get("fbc"),
                ga_client_id=parsed.get("ga_client_id"),
                event_source_url=parsed.get("event_source_url") or request.headers.get("Referer"),
            )
        except Exception as e:
            logger.warning("webhook_pix: falha no capi dispatcher: %s", e)

    threading.Thread(target=_bg, daemon=True).start()

    return JsonResponse({"status": "accepted"}, status=202)

@csrf_exempt  # vamos usar X-CSRFToken no JS mesmo assim
def create_deposit_pix(request):
    try:
        data = json.loads(request.body)
        amount = Decimal(data["amount"])  # já vem como string "50.00"

        if amount < Decimal("10.00"):
            return JsonResponse({"detail": "Valor mínimo R$ 10,00"}, status=400)

        adapter = get_active_adapter()

        # Aqui o adapter cria o Pix no banco e retorna os dados
        pix_data = adapter.create_charge(
            amount=amount,
            user=request.user,
            description="Depósito via app",
            expire_minutes=30,
        )
        # pix_data esperado: {"txid": "...", "qr_code_base64": "data:image/png...", "copia_e_cola": "...", "expiration": datetime}

        pix = PixTransaction.objects.create(
            user=request.user,
            amount=amount,
            provider=adapter.name,
            external_id=pix_data["txid"],
            transaction_id=pix_data.get("transaction_id"),  # se tiver
            copia_e_cola=pix_data["copia_e_cola"],
            status="PENDING",
            expiration=pix_data["expiration"],
        )

        return JsonResponse({
            "id": pix.id,
            "amount": str(amount),
            "qr_code_base64": pix_data["qr_code_base64"],
            "copia_e_cola": pix_data["copia_e_cola"],
            "expires_at": pix_data["expiration"].isoformat(),
        })
    except Exception as e:
        logger.error(f"create_deposit_pix error: {e}")
        return JsonResponse({"detail": "Erro interno"}, status=500)
    
def pix_status(request, pk):
    try:
        pix = PixTransaction.objects.get(id=pk, user=request.user)
        status = pix.status
        paid = status in ("AUTHORIZED", "CONFIRMED", "RECEIVED")
        return JsonResponse({
            "status": "paid" if paid else "pending" if status == "PENDING" else "expired",
            "paid": paid
        })
    except PixTransaction.DoesNotExist:
        return JsonResponse({"status": "expired"}, status=404)
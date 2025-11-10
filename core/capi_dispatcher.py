from __future__ import annotations
import hashlib
import json
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

import requests
from django.conf import settings
from django.core.cache import cache

from tracking.services import dispatch_event

logger = logging.getLogger(__name__)


def _norm_status(s: Optional[str]) -> str:
    if not s:
        return ""
    return str(s).strip().lower()


def _choose_event_from_status(status_raw: Optional[str]) -> Optional[str]:
    s = _norm_status(status_raw)
    if s in {"authorized", "confirmed", "received", "approved", "completed"}:
        return "Purchase"
    if s in {"expired", "canceled", "cancelled"}:
        return "PaymentExpired"
    return None


def _guess_txid(payload: Dict[str, Any]) -> Optional[str]:
    return (
        payload.get("txid")
        or payload.get("transaction_id")
        or (payload.get("charge") or {}).get("txid")
        or (payload.get("payment") or {}).get("txid")
        or (payload.get("data") or {}).get("txid")
    )


def _guess_value_currency(payload: Dict[str, Any]) -> Tuple[Optional[Decimal], str]:
    """
    Tenta extrair (value, currency). Default currency BRL.
    Aceita string decimal ou número.
    """
    currency = (
        payload.get("currency")
        or (payload.get("amount") or {}).get("currency")
        or "BRL"
    )

    raw = (
        payload.get("value")
        or (payload.get("amount") or {}).get("value")
        or (payload.get("amount") or {}).get("total")
        or (payload.get("data") or {}).get("value")
    )
    if raw is None:
        return None, currency

    try:
        if isinstance(raw, (int, float, Decimal)):
            return Decimal(str(raw)), currency
        if isinstance(raw, str):
            return Decimal(raw.replace(",", ".")), currency
    except (InvalidOperation, ValueError):
        logger.warning("capi: valor inválido em payload: %r", raw)
    return None, currency


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _build_user_data(
    client_ip: Optional[str],
    client_ua: Optional[str],
    pii: Optional[Dict[str, str]] = None,
    fbp: Optional[str] = None,
    fbc: Optional[str] = None,
    external_id: Optional[str] = None,
    ga_client_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Monta user_data “rico” para CAPI/GA4/TikTok.
    O dispatcher já lida com ausências; aqui só agregamos o que soubermos.
    """
    user: Dict[str, Any] = {}

    if client_ip:
        user["client_ip_address"] = client_ip
    if client_ua:
        user["client_user_agent"] = client_ua

    if fbp:
        user["fbp"] = fbp
    if fbc:
        user["fbc"] = fbc

    if ga_client_id:
        user["ga_client_id"] = ga_client_id

    if external_id:
        user["external_id"] = _sha256_hex(external_id)

    if pii:
        for k in ("email", "phone", "first_name", "last_name", "city", "state", "zip", "country"):
            v = pii.get(k)
            if v:
                user[k] = _sha256_hex(str(v).strip().lower())

    return user


def _make_event_id(event_name: str, txid: Optional[str]) -> str:
    base = txid or "no-txid"
    return f"{event_name}_{base}"


def _is_duplicate(event_id: str, ttl_seconds: int = 600) -> bool:
    """
    De-dup simples por cache: true quando já processamos recentemente.
    Evita multi-post de webhooks.
    """
    key = f"capi:evt:{event_id}"
    try:
        added = cache.add(key, "1", ttl_seconds)
        return not added
    except Exception:
        return False


def _is_organic(click_type: Optional[str]) -> bool:
    s = (click_type or "").strip().lower()
    # considera variações: "Orgânico", "Organico", "org", etc.
    return s in {"orgânico", "organico"} or s.startswith("org")


def _resolve_click_tracking(txid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Recupera (click_type, tid) a partir do banco.
    - Primeiro tenta via PixTransaction -> user (campos user.click_type / user.tracking_id).
    - Fallback: tenta na própria transação (click_type / tracking_id), caso exista.
    - Se nada for encontrado, retorna (None, None).
    """
    click_type: Optional[str] = None
    tid: Optional[str] = None

    if not txid:
        return None, None

    # Imports defensivos para não quebrar se o caminho do app mudar.
    PixTx = None
    try:
        from accounts.models import PixTransaction as _Pix
        PixTx = _Pix
    except Exception:
        try:
            from core.models import PixTransaction as _Pix  # type: ignore
            PixTx = _Pix
        except Exception:
            try:
                from models import PixTransaction as _Pix  # type: ignore
                PixTx = _Pix
            except Exception:
                PixTx = None

    if PixTx is None:
        return None, None

    try:
        tx = PixTx.objects.select_related("user").filter(transaction_id=txid).first()
        if tx is None:
            return None, None

        # 1) user.click_type / user.tracking_id
        u = getattr(tx, "user", None)
        if u is not None:
            click_type = getattr(u, "click_type", None)
            tid = getattr(u, "tracking_id", None)

        # 2) transaction.click_type / transaction.tracking_id (fallback)
        if not click_type:
            click_type = getattr(tx, "click_type", None)
        if not tid:
            tid = getattr(tx, "tracking_id", None)
    except Exception as e:
        logger.warning("capi: erro ao obter click tracking do banco: %s", e)
        return None, None

    return (click_type or None), (tid or None)


def _post_purchase_to_project_a(*, click_type: str, tid: str,
                                value: Optional[Decimal], currency: str,
                                order_id: Optional[str], event_time: Optional[int]) -> Optional[Dict[str, Any]]:
    """
    Envia o POST para o Projeto A (/e/track). Retorna dict com resposta JSON (se houver),
    ou None em falha/silêncio.
    """
    base = (getattr(settings, "PROJECT_A_BASE_URL", "") or "https://tramposlara.com").rstrip("/")
    url = f"{base}/e/track"
    body: Dict[str, Any] = {
        "type": "purchase",
        "click_type": click_type,
        "tid": tid,
    }

    # Campos obrigatórios do Purchase
    if value is not None:
        try:
            body["value"] = float(value)
        except Exception:
            # se Decimal quebrar, cai como string
            body["value"] = float(str(value))
    body["currency"] = (currency or "BRL").upper()

    # Recomendados quando disponíveis
    if order_id:
        body["order_id"] = order_id

    # Opcionais
    if event_time:
        # aceita segundos ou ms; aqui garantimos segundos
        body["event_time"] = int(event_time)

    test_code = getattr(settings, "PROJECT_A_TEST_EVENT_CODE", "")
    if test_code:
        body["test_event_code"] = test_code

    timeout = getattr(settings, "PROJECT_A_TIMEOUT", 5)
    try:
        resp = requests.post(url, json=body, timeout=timeout)
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code, "text": (resp.text[:200] if resp.text else "")}
    except Exception as e:
        logger.warning("capi: falha ao enviar Purchase ao Projeto A: %s", e)
        return None


def handle_pix_webhook(
    payload: Dict[str, Any],
    client_ip: Optional[str],
    client_ua: Optional[str],
    *,
    pii: Optional[Dict[str, str]] = None,
    fbp: Optional[str] = None,
    fbc: Optional[str] = None,
    external_id: Optional[str] = None,
    ga_client_id: Optional[str] = None,
    event_source_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Serviço central: decide qual evento disparar e chama dispatch_event para
    todos os ServerPixels ativos habilitados para aquele evento.

    Retorna um dict com resumo do que foi feito (útil para logs/tests).
    """
    status = (
        payload.get("status")
        or (payload.get("payment") or {}).get("status")
        or (payload.get("charge") or {}).get("status")
        or (payload.get("data") or {}).get("status")
    )
    event_name = _choose_event_from_status(status)
    if not event_name:
        logger.info("capi: webhook ignorado – status não mapeado: %r", status)
        return {"dispatched": False, "reason": "status_not_mapped", "status": status}

    txid = _guess_txid(payload)
    value, currency = _guess_value_currency(payload)

    user_data = _build_user_data(
        client_ip=client_ip,
        client_ua=client_ua,
        pii=pii,
        fbp=fbp,
        fbc=fbc,
        external_id=external_id,
        ga_client_id=ga_client_id,
    )

    custom_data: Dict[str, Any] = {"currency": currency}
    if value is not None:
        custom_data["value"] = float(value)

    if txid:
        custom_data["transaction_id"] = txid

    event_id = _make_event_id(event_name, txid)

    if _is_duplicate(event_id):
        logger.info("capi: evento duplicado (drop): %s", event_id)
        return {"dispatched": False, "reason": "duplicate", "event_id": event_id}

    # ===== NOVO: Envio do Purchase ao Projeto A =====
    project_a_resp: Optional[Dict[str, Any]] = None
    if event_name == "Purchase":
        click_type, tid = _resolve_click_tracking(txid)
        if click_type and tid and not _is_organic(click_type):
            project_a_resp = _post_purchase_to_project_a(
                click_type=click_type,
                tid=tid,
                value=value,
                currency=currency,
                order_id=txid,
                event_time=int(time.time()),
            )
        else:
            logger.info("capi: Purchase NÃO enviado ao Projeto A (orgânico ou sem click_type/tid) txid=%s", txid)

    # ===== Envio normal via dispatcher (GA4/TikTok e, até desativarmos, Meta) =====
    try:
        resp = dispatch_event(
            event_name=event_name,
            event_id=event_id,
            event_time=None,
            user_data=user_data,
            custom_data=custom_data,
            action_source="website",
            event_source_url=event_source_url,
        )
        logger.info("capi: dispatched %s txid=%s resp=%s", event_name, txid, _safe_json(resp))
        out = {
            "dispatched": True,
            "event_name": event_name,
            "event_id": event_id,
            "txid": txid,
            "value": float(value) if value is not None else None,
            "currency": currency,
            "resp": resp,
        }
        if project_a_resp is not None:
            out["project_a"] = project_a_resp
        return out
    except Exception as e:
        logger.exception("capi: erro ao disparar %s txid=%s: %s", event_name, txid, e)
        return {"dispatched": False, "reason": "exception", "error": str(e)}


def _safe_json(x: Any, max_len: int = 500) -> str:
    try:
        s = json.dumps(x, ensure_ascii=False)[:max_len]
    except Exception:
        s = str(x)[:max_len]
    return s

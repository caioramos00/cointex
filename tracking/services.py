import time
import logging
import requests
import json
from typing import Dict, Any, List
from django.conf import settings
from .models import ServerPixel

logger = logging.getLogger("core.views")

# ====== Catálogo de nomes (canônico -> nome do provedor) ======
EVENT_MAP = {
    "meta_capi": {
        "PageView":"PageView","ViewContent":"ViewContent","Search":"Search",
        "AddToCart":"AddToCart","AddToWishlist":"AddToWishlist",
        "InitiateCheckout":"InitiateCheckout","AddPaymentInfo":"AddPaymentInfo",
        "Purchase":"Purchase","Lead":"Lead","CompleteRegistration":"CompleteRegistration",
        "Subscribe":"Subscribe","StartTrial":"StartTrial","Contact":"Contact",
        "FindLocation":"FindLocation","Schedule":"Schedule",
        "SubmitApplication":"SubmitApplication","CustomizeProduct":"CustomizeProduct",
        "Donate":"Donate"
    },
    "ga4_mp": {
        "PageView":"page_view","ViewContent":"view_item","Search":"search",
        "AddToCart":"add_to_cart","AddToWishlist":"add_to_wishlist",
        "InitiateCheckout":"begin_checkout","AddPaymentInfo":"add_payment_info",
        "Purchase":"purchase","Lead":"generate_lead","CompleteRegistration":"sign_up",
        "Subscribe":"subscribe","StartTrial":"start_trial","Contact":"contact",
        "FindLocation":"search","Schedule":"schedule",
        "SubmitApplication":"submit_application","CustomizeProduct":"customize_product",
        "Donate":"purchase"
    },
    "tiktok_eapi": {
        "PageView":"PageView","ViewContent":"ViewContent","Search":"Search",
        "AddToCart":"AddToCart","AddToWishlist":"AddToWishlist",
        "InitiateCheckout":"InitiateCheckout","AddPaymentInfo":"AddPaymentInfo",
        "Purchase":"CompletePayment","Lead":"SubmitForm","CompleteRegistration":"CompleteRegistration",
        "Subscribe":"Subscribe","StartTrial":"StartTrial","Contact":"Contact",
        "FindLocation":"FindLocation","Schedule":"Schedule",
        "SubmitApplication":"SubmitApplication","CustomizeProduct":"CustomizeProduct",
        "Donate":"CompletePayment"
    }
}

def _provider_event_name(provider: str, canonical: str) -> str:
    return EVENT_MAP.get(provider, {}).get(canonical, canonical)

# ====== HTTP util (genérico para GA4/TikTok) ======
def _http_post(url: str, params=None, json_body=None, headers=None, timeout=(2, 10)):
    try:
        r = requests.post(url, params=params or {}, json=json_body or {}, headers=headers or {}, timeout=timeout)
        return r
    except Exception as e:
        class _Resp:
            status_code = 0
            text = f"[HTTP-ERR] {e}"
        return _Resp()

# ====== Helpers META (Graph) ======
def _mask(s: str | None, left=6, right=4):
    if not s:
        return None
    s = str(s)
    if len(s) <= left + right:
        return s
    return f"{s[:left]}…{s[-right:]}"

def _post_json(url: str, params: dict, json_body: dict, timeout=8):
    """POST com métrica de tempo e parse de JSON seguro."""
    t0 = time.monotonic()
    resp = requests.post(url, params=params, json=json_body, timeout=timeout)
    ms = round((time.monotonic() - t0) * 1000, 1)
    try:
        j = resp.json()
    except Exception:
        txt = resp.text or ""
        j = {"raw": (txt[:1200] + "…") if len(txt) > 1200 else txt}
    return resp.status_code, ms, j

# ====== META CAPI ======
def _send_meta(sp: ServerPixel, event_name: str, event_id: str, event_time: int,
               user_data: Dict[str, Any], custom_data: Dict[str, Any],
               action_source: str, event_source_url: str):
    pixel_id = (sp.pixel_id or "").strip()
    token = (sp.access_token or "").strip()
    if not pixel_id or not token:
        logger.warning("[CAPI-ERR] missing creds for %s", getattr(sp, "name", "<unnamed>"))
        return

    graph_version = (getattr(settings, "CAPI_GRAPH_VERSION", "") or "v18.0").strip()
    url = f"https://graph.facebook.com/{graph_version}/{pixel_id}/events"

    payload = {
        "data": [{
            "event_name": _provider_event_name("meta_capi", event_name),
            "event_time": int(event_time or time.time()),
            "event_source_url": event_source_url or "",
            "action_source": action_source or "website",
            "event_id": event_id or "",
            "user_data": user_data or {},
            "custom_data": custom_data or {},
        }]
    }
    if sp.test_event_code:
        payload["test_event_code"] = sp.test_event_code.strip()

    debug_payload = bool(getattr(settings, "CAPI_DEBUG_LOGS", False))

    # —— PRE-SEND LOG DETALHADO ——
    logger.info("[CAPI] send", extra={
        "endpoint": url,
        "pixel_id": _mask(pixel_id),
        "token": _mask(token),
        "event_name": _provider_event_name("meta_capi", event_name),
        "event_id": event_id,
        "event_time": int(event_time or time.time()),
        "action_source": (action_source or "website"),
        "event_source_url": event_source_url or "",
        "has_fbp": int(bool((user_data or {}).get("fbp"))),
        "has_fbc": int(bool((user_data or {}).get("fbc"))),
        "user_data_keys": sorted(list((user_data or {}).keys())),
        "custom_data": {
            "currency": (custom_data or {}).get("currency"),
            "value": (custom_data or {}).get("value"),
            "transaction_id": (custom_data or {}).get("transaction_id"),
            "content_type": (custom_data or {}).get("content_type"),
        },
        "test_event_code": int(bool(sp.test_event_code)),
        "payload": (payload if debug_payload else None),
    })

    params = {"access_token": token}
    attempts = 0
    last = {}
    while attempts < 3:
        attempts += 1
        try:
            status, ms, j = _post_json(url, params, payload, timeout=8)
        except Exception as e:
            logger.warning("[CAPI] post_exception", extra={
                "attempt": attempts, "error": str(e)
            })
            if attempts >= 3:
                return
            time.sleep(0.5 * attempts)
            continue

        err = (j or {}).get("error")
        evrec = (j or {}).get("events_received")
        fbtrace = (j or {}).get("fbtrace_id")
        last = j

        # —— RESPONSE LOG DETALHADO ——
        logger.info("[CAPI] response", extra={
            "attempt": attempts,
            "status": status,
            "ms": ms,
            "events_received": evrec,
            "fbtrace_id": fbtrace,
            "error": err,                                # bloco error completo, se houver
            "raw": (j if debug_payload else None),       # resposta completa somente em DEBUG
        })

        # sucesso claro
        if status == 200 and isinstance(evrec, int) and evrec >= 1 and not err:
            break

        # re-tentar apenas em 5xx
        if status >= 500:
            time.sleep(0.75 * attempts)
            continue
        else:
            break

    return last

# ====== GA4 MP ======
def _send_ga4(sp: ServerPixel, event_name: str, event_id: str, event_time: int,
              user_data: Dict[str, Any], custom_data: Dict[str, Any],
              action_source: str, event_source_url: str):
    mid = (sp.ga4_measurement_id or "").strip()
    sec = (sp.ga4_api_secret or "").strip()
    if not mid or not sec:
        logger.warning("[GA4-ERR] missing creds for %s", getattr(sp, "name", "<unnamed>"))
        return
    url = "https://www.google-analytics.com/mp/collect"
    params = {"measurement_id": mid, "api_secret": sec}

    # client_id é obrigatório. Usa o external_id/IP+UA hash ou fallback do event_id
    client_id = (user_data or {}).get("ga_client_id") or (user_data or {}).get("external_id") or (event_id or "capi-" + str(int(time.time())))
    name = _provider_event_name("ga4_mp", event_name)

    params_payload = dict(custom_data or {})
    # Se Purchase e faltar transaction_id, usa event_id
    if name == "purchase" and "transaction_id" not in params_payload and event_id:
        params_payload["transaction_id"] = event_id

    body = {
        "client_id": str(client_id),
        "timestamp_micros": int((event_time or time.time()) * 1_000_000),
        "events": [{"name": name, "params": params_payload}]
    }
    resp = _http_post(url, params=params, json_body=body)
    logger.info("[CAPI-RESP] provider=ga4 mid=%s status=%s text=%s",
                mid, getattr(resp, "status_code", 0), (getattr(resp, "text", "") or "")[:400].replace("\n", " "))

# ====== TikTok EAPI ======
def _send_tiktok(sp: ServerPixel, event_name: str, event_id: str, event_time: int,
                 user_data: Dict[str, Any], custom_data: Dict[str, Any],
                 action_source: str, event_source_url: str):
    pixel_code = (sp.tiktok_pixel_code or "").strip()
    token = (sp.tiktok_access_token or "").strip()
    if not pixel_code or not token:
        logger.warning("[TT-ERR] missing creds for %s", getattr(sp, "name", "<unnamed>"))
        return
    url = "https://business-api.tiktok.com/open_api/v1.3/pixel/track/"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    name = _provider_event_name("tiktok_eapi", event_name)
    body = {
        "pixel_code": pixel_code,
        "event": name,
        "timestamp": int(event_time or time.time()),
        "context": {
            "ip": (user_data or {}).get("client_ip_address") or "",
            "user_agent": (user_data or {}).get("client_user_agent") or "",
            "page": {"url": event_source_url or ""}
        },
        "properties": dict(custom_data or {}),
        "event_id": event_id or ""
    }
    resp = _http_post(url, headers=headers, json_body=body)
    logger.info("[CAPI-RESP] provider=tiktok pixel=%s status=%s text=%s",
                pixel_code, getattr(resp, "status_code", 0), (getattr(resp, "text", "") or "")[:400].replace("\n", " "))

# ====== Dispatcher ÚNICO ======
def dispatch_event(event_name: str, event_id: str, event_time: int,
                   user_data: Dict[str, Any], custom_data: Dict[str, Any],
                   action_source: str, event_source_url: str) -> Dict[str, Any]:
    """
    Envia o evento para os ServerPixels ativos conforme flags por evento.
    Retorna um sumário simples com provedores acionados.
    """
    name_l = (event_name or "").strip().lower().replace(" ", "").replace("-", "_")

    # Sempre inicialize qs antes de qualquer branch
    qs = ServerPixel.objects.filter(active=True)

    # Filtros por tipo de evento
    if name_l == "purchase":
        qs = qs.filter(send_purchase=True)
        # Se migrando Purchase para o Projeto A, não enviar Meta pelo dispatcher local
        if getattr(settings, "PURCHASE_VIA_PROJECT_A", True):
            qs = qs.exclude(provider="meta_capi")
    elif name_l in ("paymentexpired", "payment_expired"):
        qs = qs.filter(send_payment_expired=True)
    elif name_l in ("initiatecheckout", "initiate_checkout"):
        qs = qs.filter(send_initiate_checkout=True)
    else:
        # Mantém todos ativos (sem filtros adicionais) ou ajuste conforme sua política
        pass

    if not qs.exists():
        logger.info("dispatch_event: nenhum ServerPixel ativo habilitado para %s", event_name)
        return {"sent": 0, "providers": []}

    providers_sent: List[str] = []
    for sp in qs.order_by("id"):
        try:
            if sp.provider == "meta_capi":
                _send_meta(sp, event_name, event_id, event_time, user_data, custom_data, action_source, event_source_url)
            elif sp.provider == "ga4_mp":
                _send_ga4(sp, event_name, event_id, event_time, user_data, custom_data, action_source, event_source_url)
            elif sp.provider == "tiktok_eapi":
                _send_tiktok(sp, event_name, event_id, event_time, user_data, custom_data, action_source, event_source_url)
            providers_sent.append(sp.provider)
        except Exception as e:
            logger.warning("[CAPI-ERR] provider=%s event=%s eid=%s err=%s",
                           getattr(sp, "provider", "?"), event_name, event_id, e)

    return {"sent": len(providers_sent), "providers": providers_sent}

# ==== Compat: se você ainda chama dispatch_capi(...) ====
def dispatch_capi(event_name: str, event_id: str, event_time: int,
                  user_data: Dict[str, Any], custom_data: Dict[str, Any],
                  action_source: str, event_source_url: str) -> Dict[str, Any]:
    return dispatch_event(event_name, event_id, event_time, user_data, custom_data, action_source, event_source_url)

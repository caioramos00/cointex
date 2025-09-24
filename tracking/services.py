import logging, time, requests
from typing import Dict, Any
from django.conf import settings
from .models import ServerPixel

logger = logging.getLogger("core.views")  # mantém o padrão do seu projeto

def _http_post(url: str, params: dict, json: dict, timeout=(2, 8)):
    try:
        r = requests.post(url, params=params, json=json, timeout=timeout)
        return r
    except Exception as e:
        class _Resp:  # fake
            status_code = 0
            text = f"[HTTP-ERR] {e}"
        return _Resp()

def _capi_send_one(sp: ServerPixel, event_name: str, event_id: str, event_time: int,
                   user_data: Dict[str, Any], custom_data: Dict[str, Any],
                   action_source: str, event_source_url: str) -> Dict[str, Any]:
    """
    Envia UM evento para UM ServerPixel Meta CAPI.
    """
    pixel_id = (sp.pixel_id or "").strip()
    token = (sp.access_token or "").strip()
    test_code = (sp.test_event_code or "").strip()
    if not pixel_id or not token:
        msg = "missing pixel_id/access_token"
        logger.warning(f"[CAPI-ERR] {event_name} eid={event_id} reason={msg}")
        return {"ok": False, "status": 0, "text": msg}

    graph_version = (getattr(settings, "CAPI_GRAPH_VERSION", "") or "v18.0").strip()
    graph_url = f"https://graph.facebook.com/{graph_version}/{pixel_id}/events"

    payload = {
        "data": [{
            "event_name": event_name,
            "event_time": int(event_time or time.time()),
            "event_source_url": event_source_url or "",
            "action_source": action_source or "website",
            "event_id": event_id or "",
            "user_data": user_data or {},
            "custom_data": custom_data or {},
        }]
    }
    if test_code:
        payload["test_event_code"] = test_code

    logger.info("[CAPI-SEND] pixel=%s event=%s eid=%s value=%s fbp=%s fbc=%s action_source=%s",
                pixel_id, event_name, event_id, (custom_data or {}).get("value"),
                bool((user_data or {}).get("fbp")), bool((user_data or {}).get("fbc")), action_source)

    resp = _http_post(graph_url, params={"access_token": token}, json=payload, timeout=(2, 8))
    status = getattr(resp, "status_code", 0)
    text = (getattr(resp, "text", "") or "")[:400].replace("\n", " ")
    logger.info("[CAPI-RESP] pixel=%s event=%s status=%s text=%s", pixel_id, event_name, status, text)
    return {"ok": (200 <= status < 300), "status": status, "text": text}

def dispatch_capi(event_name: str, event_id: str, event_time: int,
                  user_data: Dict[str, Any], custom_data: Dict[str, Any],
                  action_source: str, event_source_url: str) -> None:
    """
    Itera TODOS os ServerPixel ativos e com o evento habilitado, disparando o mesmo evento.
    """
    name = (event_name or "").strip().lower()
    qs = ServerPixel.objects.filter(active=True, provider="meta_capi")

    # Filtra por evento habilitado (checkboxes)
    if name == "purchase":
        qs = qs.filter(send_purchase=True)
    elif name == "paymentexpired":
        qs = qs.filter(send_payment_expired=True)
    elif name == "initiatecheckout":
        qs = qs.filter(send_initiate_checkout=True)
    else:
        logger.info("[CAPI-DISPATCH] event=%s não mapeado em checkboxes; enviando para todos ativos.", event_name)

    for sp in qs:
        try:
            _capi_send_one(
                sp, event_name=event_name, event_id=event_id, event_time=event_time,
                user_data=user_data, custom_data=custom_data,
                action_source=action_source, event_source_url=event_source_url
            )
        except Exception as e:
            logger.warning("[CAPI-ERR] pixel=%s event=%s eid=%s err=%s", sp.pixel_id, event_name, event_id, e)

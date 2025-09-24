import time, logging, requests
from typing import Dict, Any
from django.conf import settings
from .models import ServerPixel

logger = logging.getLogger("core.views")  # segue seu padrão de logs

def _http_post(url: str, params: dict, json: dict, timeout=(2, 8)):
    try:
        r = requests.post(url, params=params, json=json, timeout=timeout)
        return r
    except Exception as e:
        class _Resp:
            status_code = 0
            text = f"[HTTP-ERR] {e}"
        return _Resp()

def _capi_send_one(sp: ServerPixel, event_name: str, event_id: str, event_time: int,
                   user_data: Dict[str, Any], custom_data: Dict[str, Any],
                   action_source: str, event_source_url: str) -> None:
    pixel_id = (sp.pixel_id or "").strip()
    token = (sp.access_token or "").strip()
    if not pixel_id or not token:
        logger.warning(f"[CAPI-ERR] missing pixel_id/access_token for {sp.name}")
        return

    graph_version = (getattr(settings, "CAPI_GRAPH_VERSION", "") or "v18.0").strip()
    url = f"https://graph.facebook.com/{graph_version}/{pixel_id}/events"

    data = {
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
    if sp.test_event_code:
        data["test_event_code"] = sp.test_event_code.strip()

    logger.info("[CAPI-SEND] pixel=%s event=%s eid=%s value=%s source=%s",
                pixel_id, event_name, event_id, (custom_data or {}).get("value"), action_source)

    resp = _http_post(url, params={"access_token": token}, json=data, timeout=(2, 8))
    logger.info("[CAPI-RESP] pixel=%s status=%s text=%s", pixel_id, getattr(resp, "status_code", 0),
                (getattr(resp, "text", "") or "")[:400].replace("\n", " "))

def dispatch_capi(event_name: str, event_id: str, event_time: int,
                  user_data: Dict[str, Any], custom_data: Dict[str, Any],
                  action_source: str, event_source_url: str) -> None:
    # Filtra todos os destinos ativos e com o evento habilitado
    name = (event_name or "").lower()
    qs = ServerPixel.objects.filter(active=True)
    if name == "purchase":
        qs = qs.filter(send_purchase=True)
    elif name == "paymentexpired":
        qs = qs.filter(send_payment_expired=True)
    elif name == "initiatecheckout":
        qs = qs.filter(send_initiate_checkout=True)

    for sp in qs.order_by("id"):
        if sp.provider == "meta_capi":
            _capi_send_one(
                sp,
                event_name=event_name, event_id=event_id, event_time=event_time,
                user_data=user_data, custom_data=custom_data,
                action_source=action_source, event_source_url=event_source_url
            )

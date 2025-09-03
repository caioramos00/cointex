import re, hashlib, time
from typing import Optional, Dict, Any
from django.conf import settings
from django.utils import timezone
from utils.http import http_get, http_post

GRAPH_URL = f"https://graph.facebook.com/{getattr(settings,'CAPI_GRAPH_VERSION','v18.0')}/{getattr(settings,'CAPI_PIXEL_ID','')}/events"

def _sha256(x: str) -> str:
    x = (x or "").strip().lower()
    return hashlib.sha256(x.encode("utf-8")).hexdigest() if x else ""

def _norm_phone(x: str) -> str:
    return re.sub(r"\D+", "", x or "")

def lookup_click(tracking_id: Optional[str]=None, ctwa_clid: Optional[str]=None) -> Dict[str,Any]:
    """Busca dados na Landing (PG-first com fallback), com token."""
    url = getattr(settings, "LANDING_LOOKUP_URL", "")
    token = getattr(settings, "LANDING_LOOKUP_TOKEN", "")
    params = {}
    if tracking_id:
        params["tid"] = tracking_id
    elif ctwa_clid:
        params["ctwa_clid"] = ctwa_clid
    else:
        return {}

    r = http_get(url, headers={"X-Lookup-Token": token}, params=params,
                 timeout=(2, getattr(settings,'HTTP_TIMEOUT_GET',5)), measure="landing/lookup")
    if getattr(r, "status_code", 0) == 200:
        try:
            js = r.json() or {}
            return js.get("data") or {}
        except Exception:
            return {}
    return {}

def build_user_data(click: Dict[str,Any], fallback_ip: str, fallback_ua: str, user=None) -> Dict[str,Any]:
    ud = {
        "fbp": click.get("fbp") or click.get("data",{}).get("fbp"),
        "fbc": click.get("fbc") or click.get("data",{}).get("fbc"),
        "client_ip_address": click.get("client_ip_address") or click.get("ip") or fallback_ip,
        "client_user_agent": click.get("client_user_agent") or click.get("ua") or fallback_ua,
    }
    if user:
        if getattr(user, "email", None):
            ud["em"] = _sha256(user.email)
        phone = getattr(user, "phone_number", "") or getattr(user, "phone", "")
        if phone:
            ud["ph"] = _sha256(_norm_phone(phone))
        ext = getattr(user, "id", None)
        if ext is not None:
            ud["external_id"] = _sha256(str(ext))
    else:
        for k in ("em","ph","external_id"):
            v = click.get(k)
            if v:
                ud[k] = _sha256(v if k!="ph" else _norm_phone(v))
    return {k:v for k,v in ud.items() if v}

def send_capi_event(*, event_name: str, event_id: str, event_time: int,
                    event_source_url: str, action_source: str,
                    user_data: Dict[str,Any], custom_data: Dict[str,Any]) -> Dict[str,Any]:
    token = getattr(settings, "CAPI_ACCESS_TOKEN", "")
    pixel = getattr(settings, "CAPI_PIXEL_ID", "")
    if not token or not pixel:
        return {"ok": False, "error": "missing_token_or_pixel"}

    payload = {
        "data": [{
            "event_name": event_name,
            "event_time": int(event_time),
            "event_source_url": event_source_url,
            "action_source": action_source,
            "event_id": event_id,
            "user_data": user_data,
            "custom_data": custom_data or {}
        }]
    }
    r = http_post(GRAPH_URL, params={"access_token": token}, json=payload,
                  timeout=(2, getattr(settings,'HTTP_TIMEOUT_POST',8)), measure="capi/events")
    out = {"status": getattr(r, "status_code", 0), "text": getattr(r, "text", "")[:400]}
    out["ok"] = (out["status"] == 200)
    return out

def event_id_for(kind: str, txid: str) -> str:
    return f"{kind}_{txid}"

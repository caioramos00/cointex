import logging
import requests
import time
from typing import Optional
from urllib.parse import urlparse
from django.conf import settings

logger = logging.getLogger("core.views")

def _conf_str(name: str, default: str = "") -> str:
    """Lê uma config do settings e devolve sempre string (sem None)."""
    try:
        v = getattr(settings, name, default)
    except Exception:
        v = default
    return str(v or "").strip()

def _url_base(name: str) -> str:
    """Normaliza URL base (ou vazio), removendo / final."""
    base = _conf_str(name, "")
    return base.rstrip("/") if base else ""

LOOKUP_URL   = _url_base("LANDING_LOOKUP_URL")
LOOKUP_TOKEN = getattr(settings, "LANDING_LOOKUP_TOKEN", "")
LEGACY_GETCLICK_URL = "https://grupo-whatsapp-trampos-lara-2025.onrender.com/capi/get-click"

SAFE_FIELDS = [
    "fbp", "fbc", "fbclid",
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "page_url", "referrer",
    "ctwa_clid", "tracking_id", "ga_client_id",
]

def _trunc(s: str, n: int = 800) -> str:
    s = s or ""
    if len(s) <= n:
        return s
    return s[:n] + "…"

def _mask(tok: str) -> str:
    if not tok:
        return ""
    tok = str(tok)
    if len(tok) <= 10:
        return tok
    return tok[:6] + "…" + tok[-4:]

def _mask_tid(v: str) -> str:
    """Mascara ctwa_clid/tid exibindo apenas prefixo/sufixo para debug."""
    if not v:
        return ""
    v = str(v)
    if len(v) <= 16:
        return v
    return f"{v[:8]}…{v[-8:]}"

def _log_request(tag: str, url: str, params: dict, headers: dict):
    """Log do request com máscara apropriada."""
    masked_params = {
        k: (_mask_tid(v) if k in ("ctwa_clid", "tid") else v)
        for k, v in (params or {}).items()
    }
    masked_headers = {
        k: (_mask(v) if k.lower() == "x-lookup-token" else v)
        for k, v in (headers or {}).items()
    }
    parsed = urlparse(url)
    logger.info("[CAPI-LOOKUP] request", extra={
        "tag": tag,
        "host": parsed.netloc,
        "path": parsed.path,
        "params": masked_params,
        "headers": masked_headers,
    })

def _http_get(path: str, params: dict, tag: str, headers: dict, timeout=(3, 7)) -> dict:
    """GET com logs detalhados e métricas de tempo."""
    if not LOOKUP_URL:
        logger.warning("[CAPI-LOOKUP] skip - LOOKUP_URL not set", extra={"tag": tag})
        return {}

    url = f"{LOOKUP_URL}{path}"
    _log_request(tag, url, params, headers)
    t0 = time.monotonic()
    debug_payload = bool(getattr(settings, "CAPI_DEBUG_LOGS", False))

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    except Exception as e:
        ms = round((time.monotonic() - t0) * 1000, 1)
        parsed = urlparse(url)
        logger.exception("[CAPI-LOOKUP] request_error", extra={
            "tag": tag,
            "host": parsed.netloc,
            "path": parsed.path,
            "ms": ms,
            "error": str(e),
        })
        return {}

    ms = round((time.monotonic() - t0) * 1000, 1)
    parsed = urlparse(url)

    content = {}
    body_txt = ""
    try:
        content = resp.json() if resp.content else {}
    except Exception:
        body_txt = _trunc((resp.text or "").replace("\n", " "))
        content = {}

    # alguns serviços retornam {"data": {...}}
    data = (content.get("data") if isinstance(content, dict) else None) or content or {}

    keys = list(data.keys()) if isinstance(data, dict) else []
    subset = {k: data.get(k) for k in SAFE_FIELDS if isinstance(data, dict) and k in data}
    has_fbp = bool(subset.get("fbp"))
    has_fbc = bool(subset.get("fbc"))

    logger.info("[CAPI-LOOKUP] response", extra={
        "tag": tag,
        "status": resp.status_code,
        "ms": ms,
        "host": parsed.netloc,
        "path": parsed.path,
        "keys": keys,
        "subset": subset,                         # mostra fbp/fbc/utms/page_url/referrer/ctwa_clid/etc
        "has_fbp": int(has_fbp),
        "has_fbc": int(has_fbc),
        "raw": (content if debug_payload else None),
        "text": (body_txt if (debug_payload and not content) else None),
    })

    return data if resp.ok else {}

def lookup_click(tracking_id: str, click_type: Optional[str] = None) -> dict:
    """Consulta a landing para enriquecer com fbp/fbc/utms/etc."""
    logger.info("[CAPI-LOOKUP] cfg", extra={
        "url": LOOKUP_URL,
        "token": _mask(LOOKUP_TOKEN),
        "debug": int(bool(getattr(settings, "CAPI_DEBUG_LOGS", False))),
    })

    if not tracking_id:
        logger.info("[CAPI-LOOKUP] start", extra={
            "kind": "UNKNOWN", "id": "<empty>", "skip": 1, "reason": "empty_tracking_id"
        })
        return {}

    if not LOOKUP_URL:
        logger.warning("[CAPI-LOOKUP] start", extra={
            "kind": "UNKNOWN", "id": tracking_id, "skip": 1, "reason": "lookup_url_not_set"
        })
        return {}

    headers = {"X-Lookup-Token": LOOKUP_TOKEN} if LOOKUP_TOKEN else {}
    is_ctwa = (str(click_type or "").upper() == "CTWA")

    # heurística segura (ctwa_clid costuma ser longo e iniciar com 'Af')
    if not is_ctwa:
        tid_str = str(tracking_id)
        if tid_str.startswith("Af") and len(tid_str) >= 60:
            is_ctwa = True

    if is_ctwa:
        # 1) CTWA por ctwa_clid
        data = _http_get("/capi/lookup", {"ctwa_clid": tracking_id}, tag="ctwa", headers=headers)
        if data:
            return data
        # 2) fallback: tentar como tid (legado)
        data = _http_get("/capi/lookup", {"tid": tracking_id}, tag="ctwa-fallback-tid", headers=headers)
        if data:
            return data
        # 3) diagnóstico: Redis direto (endpoint auxiliar)
        data = _http_get("/ctwa/get", {"ctwa_clid": tracking_id}, tag="ctwa-redis", headers=headers)
        if data:
            return data

        logger.warning("[CAPI-LOOKUP] miss", extra={
            "kind": "CTWA",
            "id": _mask_tid(tracking_id),
            "tried": ["ctwa_clid", "tid", "ctwa_get"]
        })
        return {}

    # LP (Landing Page)
    data = _http_get("/capi/lookup", {"tid": tracking_id}, tag="lp", headers=headers)
    if data:
        return data

    # Legacy opcional (LP)
    if LEGACY_GETCLICK_URL:
        url = LEGACY_GETCLICK_URL
        params = {"tid": tracking_id}
        t0 = time.monotonic()
        try:
            _log_request("lp-legacy", url, params, headers={})
            r = requests.get(url, params=params, timeout=(2, 5))
            ms = round((time.monotonic() - t0) * 1000, 1)
            if r.status_code == 200:
                try:
                    js = r.json() or {}
                except Exception:
                    js = {}
                data = js.get("data", js) or {}
                keys = list((data or {}).keys())
                has_fbp = int(bool((data or {}).get("fbp")))
                has_fbc = int(bool((data or {}).get("fbc")))
                logger.info("[CAPI-LOOKUP] response", extra={
                    "tag": "lp-legacy", "status": 200, "ms": ms,
                    "keys": keys, "has_fbp": has_fbp, "has_fbc": has_fbc,
                    "raw": (js if getattr(settings, "CAPI_DEBUG_LOGS", False) else None),
                })
                return data or {}
            else:
                body = _trunc((r.text or "").replace("\n", " "))
                logger.warning("[CAPI-LOOKUP] legacy_non_200", extra={
                    "tag": "lp-legacy", "status": r.status_code, "ms": ms, "body": body
                })
        except Exception as e:
            ms = round((time.monotonic() - t0) * 1000, 1)
            logger.warning("[CAPI-LOOKUP] legacy_error", extra={
                "tag": "lp-legacy", "ms": ms, "error": str(e)
            })

    logger.warning("[CAPI-LOOKUP] miss", extra={
        "kind": "LP",
        "id": _mask_tid(tracking_id),
        "tried": ["tid", "legacy"]
    })
    return {}

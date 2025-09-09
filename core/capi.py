import logging, requests
from typing import Optional
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

def _trunc(s: str, n: int = 400) -> str:
    s = s or ""
    return s[:n] + ("…" if len(s) > n else "")

def _mask(tok: str) -> str:
    return (tok[:4] + "…" + tok[-4:]) if tok else ""

def _log_call(tag: str, path: str, params: dict, headers: dict):
    pshow = {k: (v if k not in ("ctwa_clid","tid") else f"{str(v)[:8]}…{str(v)[-8:]}") for k, v in (params or {}).items()}
    hshow = {k: (_mask(v) if k.lower()=="x-lookup-token" else v) for k, v in (headers or {}).items()}
    logger.info(f"[CAPI-LOOKUP] http:call tag={tag} url={LOOKUP_URL}{path} params={pshow} headers={hshow}")

def _http_get(path: str, params: dict, tag: str, headers: dict, timeout=(3,7)) -> dict:
    try:
        _log_call(tag, path, params, headers)
        r = requests.get(f"{LOOKUP_URL}{path}", headers=headers, params=params, timeout=timeout)
        sc = r.status_code
        if sc == 200:
            try:
                js = r.json() or {}
            except Exception:
                body = _trunc(r.text)
                logger.warning(f"[CAPI-LOOKUP] http=200 json=ERR tag={tag} body={body}")
                return {}
            data = js.get("data", js) or {}
            logger.info(f"[CAPI-LOOKUP] http=200 tag={tag} ok=1 keys={list(data.keys()) or []}")
            return data
        else:
            body = _trunc((r.text or "").replace("\n"," "))
            logger.warning(f"[CAPI-LOOKUP] http={sc} tag={tag} body={body}")
            return {}
    except Exception as e:
        logger.warning(f"[CAPI-LOOKUP] http=ERR tag={tag} error={e}")
        return {}

def lookup_click(tracking_id: str, click_type: Optional[str] = None) -> dict:
    logger.info(f"[CAPI-LOOKUP] cfg url={LOOKUP_URL} token={_mask(LOOKUP_TOKEN)}")

    if not tracking_id:
        logger.info("[CAPI-LOOKUP] start kind=UNKNOWN id=<empty> skip=1 reason=empty_tracking_id")
        return {}

    if not LOOKUP_URL:
        logger.warning(f"[CAPI-LOOKUP] start kind=UNKNOWN id={tracking_id} skip=1 reason=lookup_url_not_set")
        return {}

    headers = {"X-Lookup-Token": LOOKUP_TOKEN} if LOOKUP_TOKEN else {}
    is_ctwa = (str(click_type or "").upper() == "CTWA")

    # heurística segura
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
        # 3) diagnóstico: Redis direto
        data = _http_get("/ctwa/get", {"ctwa_clid": tracking_id}, tag="ctwa-redis", headers=headers)
        if data:
            return data

        logger.warning(f"[CAPI-LOOKUP] miss kind=CTWA id={tracking_id} tried=ctwa_clid,tid,ctwa_get")
        return {}

    # LP (Landing Page)
    data = _http_get("/capi/lookup", {"tid": tracking_id}, tag="lp", headers=headers)
    if data:
        return data

    # Legacy opcional (LP)
    if LEGACY_GETCLICK_URL:
        try:
            r = requests.get(LEGACY_GETCLICK_URL, params={"tid": tracking_id}, timeout=(2,5))
            if r.status_code == 200:
                js = r.json() or {}
                data = js.get("data", js) or {}
                keys = list((data or {}).keys())
                has_fbp = bool((data or {}).get("fbp"))
                has_fbc = bool((data or {}).get("fbc"))
                logger.info(f"[CAPI-LOOKUP] http=200 tag=lp-legacy ok=1 keys={len(keys)} has_fbp={int(has_fbp)} has_fbc={int(has_fbc)}")
                return data or {}
            else:
                logger.warning(f"[CAPI-LOOKUP] http={r.status_code} tag=lp-legacy body={_trunc(r.text)}")
        except Exception as e:
            logger.warning(f"[CAPI-LOOKUP] http=ERR tag=lp-legacy error={e}")

    logger.warning(f"[CAPI-LOOKUP] miss kind=LP id={tracking_id} tried=tid,legacy")
    return {}

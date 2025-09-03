import logging, requests, time, hashlib
from typing import Optional
from django.conf import settings

logger = logging.getLogger(__name__)

LOOKUP_URL  = getattr(settings, "LANDING_LOOKUP_URL", "").rstrip("/")
LOOKUP_TOKEN = getattr(settings, "LANDING_LOOKUP_TOKEN", "")
LEGACY_GETCLICK_URL = "https://grupo-whatsapp-trampos-lara-2025.onrender.com/capi/get-click"

def _trunc(s: str, n: int = 350) -> str:
    s = s or ""
    return s[:n] + ("…" if len(s) > n else "")

def lookup_click(tracking_id: str, click_type: Optional[str] = None) -> dict:
    """
    Busca dados do clique na Landing, resolvendo LP x CTWA.
    - LP:   /capi/lookup?tid=...
    - CTWA: /capi/lookup?ctwa_clid=...
    Fallbacks:
      - CTWA: tenta /capi/lookup?tid=... se necessário
      - LP:   opcional /capi/get-click?tid=... (LEGACY_GETCLICK_URL)
    Retorna o dict 'data' que o /capi/lookup já normaliza (fbc/fbp/ip/ua/event_time/etc).
    """
    if not tracking_id:
        logger.info("[CAPI-LOOKUP] skip: empty tracking_id")
        return {}

    if not LOOKUP_URL:
        logger.warning("[CAPI-LOOKUP] skip: LANDING_LOOKUP_URL not set")
        return {}

    headers = {"X-Lookup-Token": LOOKUP_TOKEN} if LOOKUP_TOKEN else {}
    is_ctwa = (str(click_type or "").upper() == "CTWA")

    # Heurística auxiliar: CTWA costuma vir longo e começar com 'Af'
    if not is_ctwa:
        tid_str = str(tracking_id)
        if tid_str.startswith("Af") and len(tid_str) >= 60:
            is_ctwa = True

    def call_lookup(params: dict, tag: str) -> dict:
        try:
            r = requests.get(f"{LOOKUP_URL}/capi/lookup", headers=headers, params=params, timeout=(3, 7))
            sc = r.status_code
            if sc == 200:
                js = r.json() or {}
                data = js.get("data", js) or {}
                logger.info(f"[CAPI-LOOKUP] source=pg {tag} ok=1 keys={list(data.keys())}")
                return data
            else:
                logger.warning(f"[CAPI-LOOKUP] source=pg {tag} status={sc} body={_trunc(getattr(r,'text',''))}")
        except Exception as e:
            logger.warning(f"[CAPI-LOOKUP] source=pg {tag} error={e}")
        return {}

    if is_ctwa:
        # 1) CTWA pelo ctwa_clid
        data = call_lookup({"ctwa_clid": tracking_id}, tag=f"ctwa_clid={tracking_id}")
        if data:
            return data
        # 2) Fallback: algumas instalações salvaram CTWA como tid
        data = call_lookup({"tid": tracking_id}, tag=f"tid={tracking_id}(ctwa-fallback)")
        if data:
            return data
        logger.warning(f"[CAPI-LOOKUP] miss ctwa_clid/tid={tracking_id}")
        return {}

    # Landing Page
    data = call_lookup({"tid": tracking_id}, tag=f"tid={tracking_id}")
    if data:
        return data

    # Fallback legado (opcional)
    if LEGACY_GETCLICK_URL:
        try:
            r = requests.get(LEGACY_GETCLICK_URL, params={"tid": tracking_id}, timeout=(2, 5))
            if r.status_code == 200:
                js = r.json() or {}
                data = js.get("data", js) or {}
                logger.info(f"[CAPI-LOOKUP] source=legacy tid={tracking_id} ok=1 keys={list(data.keys())}")
                return data
            else:
                logger.warning(f"[CAPI-LOOKUP] source=legacy tid={tracking_id} status={r.status_code} body={_trunc(r.text)}")
        except Exception as e:
            logger.warning(f"[CAPI-LOOKUP] source=legacy tid={tracking_id} error={e}")

    logger.warning(f"[CAPI-LOOKUP] miss tid={tracking_id}")
    return {}

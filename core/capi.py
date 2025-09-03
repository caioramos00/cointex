import logging, requests
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
  """
  if not tracking_id:
    logger.info("[CAPI-LOOKUP] start kind=UNKNOWN id=<empty> skip=1 reason=empty_tracking_id")
    return {}

  if not LOOKUP_URL:
    logger.warning(f"[CAPI-LOOKUP] start kind=UNKNOWN id={tracking_id} skip=1 reason=lookup_url_not_set")
    return {}

  headers = {"X-Lookup-Token": LOOKUP_TOKEN} if LOOKUP_TOKEN else {}
  is_ctwa = (str(click_type or "").upper() == "CTWA")

  # Heurística auxiliar: muitos ctwa_clid começam com 'Af' e são longos
  if not is_ctwa:
    tid_str = str(tracking_id)
    if tid_str.startswith("Af") and len(tid_str) >= 60:
      is_ctwa = True

  kind = "CTWA" if is_ctwa else "LP"
  logger.info(f"[CAPI-LOOKUP] start kind={kind} id={tracking_id} url={LOOKUP_URL}/capi/lookup")

  def call_lookup(param_name: str, value: str) -> dict:
    tag = f"{param_name}={value}"
    try:
      r = requests.get(
        f"{LOOKUP_URL}/capi/lookup",
        headers=headers,
        params={param_name: value},
        timeout=(3, 7)
      )
      sc = r.status_code
      if sc == 200:
        js = r.json() or {}
        data = js.get("data", js) or {}
        keys = list(data.keys())
        has_fbp = bool(data.get("fbp"))
        has_fbc = bool(data.get("fbc"))
        logger.info(
          f"[CAPI-LOOKUP] ok kind={kind} {tag} source=pg keys={len(keys)} has_fbp={int(has_fbp)} has_fbc={int(has_fbc)}"
        )
        return data
      else:
        logger.warning(f"[CAPI-LOOKUP] warn kind={kind} {tag} source=pg status={sc} body={_trunc(getattr(r,'text',''))}")
    except Exception as e:
      logger.warning(f"[CAPI-LOOKUP] error kind={kind} {tag} source=pg err={e}")
    return {}

  if is_ctwa:
    # 1) CTWA pelo ctwa_clid
    data = call_lookup("ctwa_clid", tracking_id)
    if data:
      return data
    # 2) Fallback: alguns setups antigos podem ter salvo ctwa como tid
    data = call_lookup("tid", tracking_id)
    if data:
      return data
    logger.warning(f"[CAPI-LOOKUP] miss kind=CTWA id={tracking_id} tried=ctwa_clid,tid")
    return {}

  # Landing Page (LP)
  data = call_lookup("tid", tracking_id)
  if data:
    return data

  # Fallback legado (opcional)
  if LEGACY_GETCLICK_URL:
    try:
      r = requests.get(LEGACY_GETCLICK_URL, params={"tid": tracking_id}, timeout=(2, 5))
      if r.status_code == 200:
        js = r.json() or {}
        data = js.get("data", js) or {}
        keys = list((data or {}).keys())
        has_fbp = bool((data or {}).get("fbp"))
        has_fbc = bool((data or {}).get("fbc"))
        logger.info(
          f"[CAPI-LOOKUP] ok kind=LP tid={tracking_id} source=legacy keys={len(keys)} has_fbp={int(has_fbp)} has_fbc={int(has_fbc)}"
        )
        return data or {}
      else:
        logger.warning(f"[CAPI-LOOKUP] warn kind=LP tid={tracking_id} source=legacy status={r.status_code} body={_trunc(r.text)}")
    except Exception as e:
      logger.warning(f"[CAPI-LOOKUP] error kind=LP tid={tracking_id} source=legacy err={e}")

  logger.warning(f"[CAPI-LOOKUP] miss kind=LP id={tracking_id} tried=tid,legacy")
  return {}

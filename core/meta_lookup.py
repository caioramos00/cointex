import logging
import time
import re
from typing import Optional, Dict

import requests
from django.conf import settings

try:
    from django.core.cache import cache as djcache
except Exception:
    djcache = None  # se não houver Django cache, usamos cache local

log = logging.getLogger(__name__)

# Cache local simples (usado apenas se não houver Django cache)
_LOCAL_CACHE: dict[str, tuple[float, dict]] = {}
_DIGITS = re.compile(r"\D+")


def _cache_get(key: str) -> Optional[dict]:
    now = time.time()
    if djcache is not None:
        return djcache.get(key)
    exp_val = _LOCAL_CACHE.get(key)
    if not exp_val:
        return None
    exp, val = exp_val
    if exp < now:
        _LOCAL_CACHE.pop(key, None)
        return None
    return val


def _cache_set(key: str, val: dict, ttl: int) -> None:
    if djcache is not None:
        djcache.set(key, val, ttl)
        return
    _LOCAL_CACHE[key] = (time.time() + int(ttl), val)


def _digits_only(s: str | None) -> str:
    return _DIGITS.sub("", s or "")


def resolve_ctwa_campaign_names(
    click_data: dict,
    *,
    token: Optional[str] = None,
    api_version: Optional[str] = None,
    timeout: float = 4.0,
    cache_ttl: int = 86400,
) -> Dict[str, Optional[str]]:
    """
    Dado um click_data de CTWA contendo 'ad_id' (ou 'source_id'),
    consulta o Graph e retorna nomes/ids de ad/adset/campaign.

    Retorno:
      {
        'ad_id','ad_name','adset_id','adset_name','campaign_id','campaign_name'
      }
    """
    ad_id = (click_data or {}).get("ad_id") or (click_data or {}).get("source_id")
    ad_id = _digits_only(str(ad_id))
    if not ad_id:
        return {}

    cache_key = f"meta_ad_h:{ad_id}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    # usa suas envs existentes (CAPI_*), com fallback META_*
    ver = (api_version
           or getattr(settings, "META_GRAPH_VERSION", None)
           or getattr(settings, "CAPI_GRAPH_VERSION", None)
           or "v18.0").strip()

    tok = (token
           or getattr(settings, "META_GRAPH_TOKEN", None)
           or getattr(settings, "CAPI_ACCESS_TOKEN", None)
           or "").strip()

    if not tok:
        log.warning("[META-LOOKUP-SKIP] reason=no_token ad_id=%s", ad_id)
        return {}

    url = f"https://graph.facebook.com/{ver}/{ad_id}"
    params = {
        "fields": "name,adset{id,name,campaign{id,name}}",
        "access_token": tok,
    }

    try:
        log.info("[META-LOOKUP-REQ] url=%s ad_id=%s", url, ad_id)
        resp = requests.get(url, params=params, timeout=timeout)
        text_snip = (resp.text or "")[:400].replace("\n", " ")
        log.info("[META-LOOKUP-RESP] ad_id=%s status=%s body=%s", ad_id, resp.status_code, text_snip)
        if resp.status_code != 200:
            return {}
        data = resp.json()
    except Exception as e:
        log.warning("[META-LOOKUP-ERR] ad_id=%s err=%s", ad_id, e)
        return {}

    ad_name = data.get("name")
    adset = data.get("adset") or {}
    adset_id = adset.get("id")
    adset_name = adset.get("name")
    campaign = (adset.get("campaign") or {})
    campaign_id = campaign.get("id")
    campaign_name = campaign.get("name")

    result = {
        "ad_id": ad_id or None,
        "ad_name": ad_name or None,
        "adset_id": adset_id or None,
        "adset_name": adset_name or None,
        "campaign_id": campaign_id or None,
        "campaign_name": campaign_name or None,
    }
    _cache_set(cache_key, result, cache_ttl)
    return result


def build_ctwa_utm_from_meta(click_data: dict) -> dict:
    """
    Usa resolve_ctwa_campaign_names para montar UTMs "bonitas".
    Retorna possivelmente parcial: {'utm_campaign': ..., 'utm_term': ...}
    """
    names = resolve_ctwa_campaign_names(click_data)
    if not names:
        return {}

    utm_campaign = names.get("campaign_name") or (names.get("campaign_id") and f"campaign:{names['campaign_id']}")
    # Para term, preferimos granularidade de criativo
    utm_term = (names.get("ad_name") or names.get("ad_id")
                or names.get("adset_name") or names.get("adset_id"))

    out = {}
    if utm_campaign:
        out["utm_campaign"] = utm_campaign
    if utm_term:
        out["utm_term"] = utm_term
    return out

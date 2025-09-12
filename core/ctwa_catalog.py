import csv
import io
import re
import logging
from typing import Optional, Dict

from core.models import CtwaAdCatalog

log = logging.getLogger(__name__)

# Regex para manter apenas dígitos (ex.: "238-123" -> "238123")
_DIGITS = re.compile(r"\D+")


# ---------- Helpers básicos ----------
def _digits_only(s: Optional[str]) -> str:
    """Retorna apenas dígitos da string (ou string vazia se None)."""
    return _DIGITS.sub("", str(s or ""))


def _get_first(row: dict, *keys) -> Optional[str]:
    """Retorna o primeiro valor não-vazio de uma lista de possíveis cabeçalhos."""
    for k in keys:
        v = row.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return None


# ---------- Importador de CSV (ads manager -> catálogo offline) ----------
def import_ctwa_csv_file(file_obj) -> int:
    """
    Lê um CSV exportado do Ads Manager e faz upsert na CtwaAdCatalog.

    Aceita separador vírgula/ponto-e-vírgula/aba. Cabeçalhos aceitos:
      - Ad ID / ad_id / Anúncio ID
      - Ad Name / ad_name / Nome do anúncio
      - Ad Set ID / adset_id / Conjunto de anúncios ID
      - Ad Set Name / adset_name / Nome do conjunto de anúncios
      - Campaign ID / campaign_id / Campanha ID
      - Campaign Name / campaign_name / Nome da campanha

    Retorna a quantidade de anúncios importados/atualizados.
    """
    raw = file_obj.read()
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="ignore")
    else:
        text = str(raw)

    # Detector simples de separador
    delim = ","
    for d in (";", "\t", "|"):
        if text.count(d) > text.count(delim):
            delim = d

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    count = 0
    for row in reader:
        ad_id = _digits_only(_get_first(row, "ad_id", "Ad ID", "Anúncio ID"))
        if not ad_id:
            continue

        ad_name = _get_first(row, "ad_name", "Ad Name", "Nome do anúncio")
        adset_id = _digits_only(_get_first(row, "adset_id", "Ad Set ID", "Conjunto de anúncios ID"))
        adset_name = _get_first(row, "adset_name", "Ad Set Name", "Nome do conjunto de anúncios")
        campaign_id = _digits_only(_get_first(row, "campaign_id", "Campaign ID", "Campanha ID"))
        campaign_name = _get_first(row, "campaign_name", "Campaign Name", "Nome da campanha")

        CtwaAdCatalog.objects.update_or_create(
            ad_id=ad_id,
            defaults={
                "ad_name": ad_name,
                "adset_id": adset_id or None,
                "adset_name": adset_name,
                "campaign_id": campaign_id or None,
                "campaign_name": campaign_name,
            },
        )
        count += 1

    log.info("[CTWA-CATALOG-IMPORT] rows_upserted=%s", count)
    return count


# ---------- Resolver offline por ad_id/source_id ----------
def resolve_ctwa_campaign_names_offline(click_data: dict) -> Dict[str, Optional[str]]:
    """
    Resolve nomes/ids via catálogo offline (CtwaAdCatalog) usando ad_id/source_id.

    Retorna possivelmente parcial:
      {
        'ad_id','ad_name','adset_id','adset_name','campaign_id','campaign_name'
      }
    """
    ad_id = (click_data or {}).get("ad_id") or (click_data or {}).get("source_id")
    ad_id = _digits_only(ad_id)
    if not ad_id:
        return {}

    rec = (
        CtwaAdCatalog.objects.filter(pk=ad_id)
        .values("ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name")
        .first()
    )
    return rec or {}


def build_ctwa_utm_from_offline(click_data: dict) -> dict:
    """
    Monta UTMs a partir do catálogo offline.
    Retorna possivelmente parcial: {'utm_campaign': ..., 'utm_term': ...}

    Regras:
      - utm_campaign: campaign_name (se houver) senão campaign:<campaign_id>
      - utm_term: ad_name (ou ad_id) senão adset_name (ou adset_id)
    """
    names = resolve_ctwa_campaign_names_offline(click_data)
    if not names:
        return {}

    utm_campaign = names.get("campaign_name") or (
        names.get("campaign_id") and f"campaign:{names['campaign_id']}"
    )
    utm_term = (
        names.get("ad_name")
        or names.get("ad_id")
        or names.get("adset_name")
        or names.get("adset_id")
    )

    out = {}
    if utm_campaign:
        out["utm_campaign"] = utm_campaign
    if utm_term:
        out["utm_term"] = utm_term
    return out

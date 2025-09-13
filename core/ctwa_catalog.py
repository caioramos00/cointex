import csv, logging, io, re
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

    Suporta:
      - Codificações: UTF-16 (BOM) e UTF-8/UTF-8-SIG
      - Delimitadores: vírgula, ponto-e-vírgula, TAB e pipe
      - Cabeçalhos EN/PT em várias variações

    Retorna a quantidade de anúncios importados/atualizados.
    """
    raw = file_obj.read()
    if isinstance(raw, bytes):
        # Detecta UTF-16 por BOM
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            encoding = "utf-16"
        else:
            encoding = "utf-8-sig"  # remove BOM se houver
        text = raw.decode(encoding, errors="ignore")
        log.info("[CTWA-CATALOG-IMPORT] encoding=%s bytes=%s", encoding, len(raw))
    else:
        text = str(raw)

    # --- detecção simples de delimitador ---
    counts = {",": text.count(","), ";": text.count(";"), "\t": text.count("\t"), "|": text.count("|")}
    delim = max(counts, key=counts.get)
    log.info("[CTWA-CATALOG-IMPORT] delimiter=%r counts=%s", delim, counts)

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)

    # Variações de cabeçalhos (EN/PT + capitalizações mais comuns)
    AD_ID_KEYS          = ("ad_id", "Ad ID", "Ad id", "AD ID", "ID do anúncio", "Anúncio ID", "ID do Anúncio")
    AD_NAME_KEYS        = ("ad_name", "Ad Name", "Ad name", "AD NAME", "Nome do anúncio", "Nome do Anúncio")
    ADSET_ID_KEYS       = ("adset_id", "Ad Set ID", "Ad set ID", "AD SET ID",
                           "ID do conjunto de anúncios", "Conjunto de anúncios ID", "Conjunto de Anúncios ID")
    ADSET_NAME_KEYS     = ("adset_name", "Ad Set Name", "Ad set name", "AD SET NAME",
                           "Nome do conjunto de anúncios", "Nome do Conjunto de Anúncios")
    CAMPAIGN_ID_KEYS    = ("campaign_id", "Campaign ID", "Campaign id", "CAMPAIGN ID",
                           "ID da campanha", "Campanha ID", "ID da Campanha")
    CAMPAIGN_NAME_KEYS  = ("campaign_name", "Campaign Name", "Campaign name", "CAMPAIGN NAME",
                           "Nome da campanha", "Nome da Campanha")
    PLACEMENT_KEYS      = ("placement", "Placement", "Posicionamento")

    # Descobre campos existentes no modelo para salvar só o que existe
    model_fields = {f.name for f in CtwaAdCatalog._meta.fields}

    count = 0
    for i, row in enumerate(reader, 1):
        ad_id = _digits_only(_get_first(row, *AD_ID_KEYS))
        if not ad_id:
            # sem ad_id não há como indexar -> pula linha
            continue

        ad_name        = _get_first(row, *AD_NAME_KEYS)
        adset_id       = _digits_only(_get_first(row, *ADSET_ID_KEYS))
        adset_name     = _get_first(row, *ADSET_NAME_KEYS)
        campaign_id    = _digits_only(_get_first(row, *CAMPAIGN_ID_KEYS))
        campaign_name  = _get_first(row, *CAMPAIGN_NAME_KEYS)
        placement_val  = _get_first(row, *PLACEMENT_KEYS)

        defaults = {
            "ad_name": ad_name,
            "adset_id": adset_id or None,
            "adset_name": adset_name,
            "campaign_id": campaign_id or None,
            "campaign_name": campaign_name,
        }
        # Só inclui placement se o campo existir no modelo
        if "placement" in model_fields:
            defaults["placement"] = placement_val

        CtwaAdCatalog.objects.update_or_create(
            ad_id=ad_id,
            defaults=defaults,
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
        'ad_id','ad_name','adset_id','adset_name','campaign_id','campaign_name', ['placement' se existir no modelo]
      }
    """
    ad_id = (click_data or {}).get("ad_id") or (click_data or {}).get("source_id")
    ad_id = _digits_only(ad_id)
    if not ad_id:
        return {}

    # Só pede ao banco os campos que existem no modelo
    model_fields = {f.name for f in CtwaAdCatalog._meta.fields}
    wanted = ["ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name"]
    if "placement" in model_fields:
        wanted.append("placement")

    rec = (
        CtwaAdCatalog.objects.filter(pk=ad_id)
        .values(*wanted)
        .first()
    )
    return rec or {}


def build_ctwa_utm_from_offline(click_data: dict) -> dict:
    """
    Monta dados a partir do catálogo offline.

    Retorna:
      - Semântica “nomes/ids” (para middleware/views):
        'campaign_name', 'campaign_id', 'adset_name', 'adset_id', 'ad_name', 'ad_id', ['placement']
      - E também UTMs derivadas por conveniência:
        'utm_campaign', 'utm_term'

    Regras UTM:
      - utm_campaign: campaign_name (se houver) senão campaign:<campaign_id>
      - utm_term: ad_name (ou ad_id) senão adset_name (ou adset_id)
        (se houver 'placement', ele é concatenado no final, p.ex. "Ad X - Stories")
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

    placement = names.get("placement")
    if placement:
        # concatena de forma simples; ajuste se preferir outro separador
        utm_term = f"{utm_term or ''}-{placement}"

    out = dict(names)  # inclui os nomes/ids (e placement se veio do modelo)
    if utm_campaign:
        out["utm_campaign"] = utm_campaign
    if utm_term:
        out["utm_term"] = utm_term
    return out

import re, time, logging, os, json, random
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from django.shortcuts import redirect
from django.http import HttpResponseRedirect

# Ajuste as rotas-alvo (prefixos) nas quais você quer garantir UTMs
CTWA_UTM_PATHS = (
    r"^/$",
    r"^/home/?$",
    r"^/withdraw/validation/?$",
    r"^/withdraw/?$",
    r"^/send/?$",
    r"^/payment-confirm/?$",
    r"^/accounts/sign-in/?$",
    r"^/accounts/sign-up/?$",
    r"^/profile/?$",
)
CTWA_UTM_PATHS_RE = [re.compile(p) for p in CTWA_UTM_PATHS]

# Evita loops de redirect marcando a requisição
_CTWA_INJECT_FLAG = "_ctwa_inj"

def _digits_only(s: str) -> str:
    import re as _re
    return _re.sub(r"\D+", "", s or "")

def _to_e164_br(raw: str) -> str:
    d = _digits_only(raw or "")
    if not d: return ""
    if d.startswith("55"): return d
    if 10 <= len(d) <= 11: return "55"+d
    return d

def _safe_str(v):
    return v if (isinstance(v, str) and v.strip()) else None

def _compute_utms_from_click(click: dict) -> dict:
    """
    Rota C -> Rota B -> Rota A:
      - utm_source/meta, utm_medium/ctwa, utm_content = WAID
      - campaign/term vindos do catálogo offline (C), depois Graph (B), depois heurística (A)
    """
    click = click or {}
    # base
    utm_source   = "meta"
    utm_medium   = "ctwa"
    phone_e164   = _to_e164_br(_safe_str(click.get("wa_id")) or _safe_str(click.get("phone")) or "")
    utm_content  = phone_e164 or _safe_str(click.get("utm_content"))  # wa_id prioritário

    utm_campaign = _safe_str((click.get("utm") or {}).get("utm_campaign"))
    utm_term     = _safe_str((click.get("utm") or {}).get("utm_term"))

    # --- ROTA C (offline por ad_id/source_id) ---
    if (not utm_campaign) or (not utm_term):
        try:
            from core.ctwa_catalog import build_ctwa_utm_from_offline
            off = build_ctwa_utm_from_offline(click)
            if not utm_campaign:
                utm_campaign = _safe_str(off.get("utm_campaign")) or utm_campaign
            if not utm_term:
                utm_term = _safe_str(off.get("utm_term")) or utm_term
        except Exception:
            pass

    # --- ROTA B (Graph API) ---
    if (not utm_campaign) or (not utm_term):
        try:
            from core.meta_lookup import build_ctwa_utm_from_meta
            meta = build_ctwa_utm_from_meta(click)
            if not utm_campaign:
                utm_campaign = _safe_str(meta.get("utm_campaign")) or utm_campaign
            if not utm_term:
                utm_term = _safe_str(meta.get("utm_term")) or utm_term
        except Exception:
            pass

    # --- ROTA A (fallback local) ---
    if not utm_campaign:
        for k in ("campaign_name", "adset_name", "ad_name", "campaign"):
            v = _safe_str(click.get(k))
            if v:
                utm_campaign = v; break
        if not utm_campaign:
            id_val = (_safe_str(click.get("ad_id"))
                      or _safe_str(click.get("source_id"))
                      or _safe_str(click.get("ctwa_clid")))
            if id_val:
                utm_campaign = f"ctwa:{id_val}"
        if not utm_campaign:
            hl = _safe_str(click.get("headline"))
            if hl: utm_campaign = f"hl:{hl[:60]}"
        if not utm_campaign:
            utm_campaign = "ctwa_unknown_campaign"

    if not utm_term:
        utm_term = (_safe_str(click.get("ad_name"))
                    or _safe_str(click.get("ad_id"))
                    or _safe_str(click.get("adset_name"))
                    or _safe_str(click.get("adset_id"))
                    or _safe_str(click.get("ctwa_clid")))

    # Monta dict final
    utms = {
        "utm_source": utm_source,
        "utm_medium": utm_medium,
        "utm_campaign": utm_campaign,
        "utm_term": utm_term,
        "utm_content": utm_content,
    }
    # Extras úteis p/ debug/relatórios (não atrapalham)
    for k in ("ad_id","source_id","ctwa_clid","campaign_id","adset_id"):
        if _safe_str(click.get(k)): utms[k] = click[k]
    return {k:v for k,v in utms.items() if _safe_str(v)}

def _load_click_data_for_user(user):
    """
    Reusa o mesmo lookup do fluxo normal (CAPI -> Landing Page/CTWA).
    Retorna um dict com os campos consumidos pelas Rotas C/B/A do middleware.
    """
    try:
        from core.capi import lookup_click

        tracking_id = getattr(user, "tracking_id", "") or ""
        click_type  = getattr(user, "click_type", "") or ""

        # Caminho principal (o mesmo do send_utmify_order)
        if tracking_id:
            data = lookup_click(tracking_id, click_type) or {}
        else:
            data = {}

        # Fallback leve por telefone -> wa_id (só se não tiver tracking)
        if not data:
            phone = (
                getattr(user, "phone_number", None)
                or getattr(user, "phone", None)
                or getattr(user, "ph_raw", None)
            )
            if phone:
                waid = _to_e164_br(str(phone))
                # não chamamos serviço externo aqui; só entregamos wa_id
                data = {"wa_id": waid}

        return data or None
    except Exception as e:
        # não derruba a request; só não injeta UTM
        logger.warning("[CTWA-LOOKUP-ERR] user=%s err=%s", getattr(user, "id", None), e)
        return None

class CtwaAutoUtmMiddleware:
    """
    Se a URL alvo não tiver UTM e o usuário for CTWA, faz 302 para a mesma rota
    com UTMs no padrão UTMify (nome|id). Invisível para o usuário.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _should_handle(self, path: str) -> bool:
        for rx in CTWA_UTM_PATHS_RE:
            if rx.match(path):
                return True
        return False

    @staticmethod
    def _safe_str(v):
        return v if (isinstance(v, str) and v.strip()) else None

    @staticmethod
    def _clean_name(s: str | None) -> str:
        # remove separadores reservados pela UTMify: | # & ?
        s = (s or "").strip()
        return s.replace("|", " ").replace("#", " ").replace("&", " ").replace("?", " ").strip()

    @staticmethod
    def _pipe(name: str | None, _id: str | None, fallback_label: str) -> str:
        name = CtwaAutoUtmMiddleware._clean_name(name) or fallback_label
        _id = (_id or "").strip() or "-"
        return f"{name}|{_id}"

    def __call__(self, request):
        # Só GET, rota-alvo e sem UTM já presente
        if request.method == "GET" and self._should_handle(request.path):
            q = dict(parse_qsl(request.META.get("QUERY_STRING", ""), keep_blank_values=True))
            if _CTWA_INJECT_FLAG not in q and not any(k.startswith("utm_") for k in q.keys()):
                user = getattr(request, "user", None)
                if user and getattr(user, "is_authenticated", False):
                    click = _load_click_data_for_user(user) or {}

                    is_ctwa = bool(self._safe_str(click.get("wa_id"))) \
                            or (self._safe_str(click.get("click_type")) or "").upper() == "CTWA"

                    if is_ctwa:
                        # ===== ROTA C → B → A para obter nomes/ids =====
                        # 1) Catálogo offline (importado do CSV do Ads)
                        camp_name = camp_id = adset_name = adset_id = ad_name = ad_id = placement = None
                        try:
                            from core.ctwa_catalog import build_ctwa_utm_from_offline
                            c = build_ctwa_utm_from_offline(click)
                            camp_name = c.get("campaign_name"); camp_id = c.get("campaign_id")
                            adset_name = c.get("adset_name");   adset_id = c.get("adset_id")
                            ad_name = c.get("ad_name");         ad_id = c.get("ad_id")
                            placement = c.get("placement")
                        except Exception:
                            pass

                        # 2) Graph API (fallback)
                        if not (camp_id and ad_id):
                            try:
                                from core.meta_lookup import build_ctwa_utm_from_meta
                                b = build_ctwa_utm_from_meta(click)
                                camp_name = camp_name or b.get("campaign_name"); camp_id = camp_id or b.get("campaign_id")
                                adset_name = adset_name or b.get("adset_name");   adset_id = adset_id or b.get("adset_id")
                                ad_name = ad_name or b.get("ad_name");           ad_id = ad_id or b.get("ad_id")
                                placement = placement or b.get("placement")
                            except Exception:
                                pass

                        # 3) Local (fallback final)
                        if not camp_id and self._safe_str(click.get("source_id")):
                            camp_id = self._safe_str(click.get("source_id"))
                        if not ad_id and self._safe_str(click.get("ad_id")):
                            ad_id = self._safe_str(click.get("ad_id"))

                        # Monta UTMs no padrão UTMify (nome|id)
                        utm_source  = "FB"  # padrão que a UTMify documenta para Meta
                        utm_campaign= self._pipe(camp_name, camp_id,  "CTWA-CAMPAIGN")
                        utm_medium  = self._pipe(adset_name, adset_id, "CTWA-ADSET")
                        utm_content = self._pipe(ad_name,   ad_id,     "CTWA-AD")
                        utm_term    = (placement or "ctwa")

                        parsed = urlparse(request.get_full_path())
                        new_q = {**q,
                                 "utm_source": utm_source,
                                 "utm_campaign": utm_campaign,
                                 "utm_medium": utm_medium,
                                 "utm_content": utm_content,
                                 "utm_term": utm_term,
                                 _CTWA_INJECT_FLAG: "1"}
                        target = urlunparse(parsed._replace(query=urlencode(new_q, doseq=True)))

                        logging.getLogger("core.views").info(
                            "[CTWA-AUTO-UTM] path=%s camp=%s medium=%s content=%s term=%s",
                            request.path, utm_campaign, utm_medium, utm_content, utm_term
                        )
                        return HttpResponseRedirect(target)

        return self.get_response(request)

class CanonicalHostMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        target = os.getenv("CANONICAL_HOST")  # por ex: "www.cointex.cash"
        if target:
            host = request.get_host().split(":")[0]
            if host != target:
                return redirect(f"https://{target}{request.get_full_path()}", permanent=True)
        return self.get_response(request)

logger = logging.getLogger(__name__)

# Prefixos/rotas que não queremos logar
STATIC_PREFIXES = (
    "/static/", "/favicon", "/apple-touch-icon", "/robots.txt",
    "/manifest", "/service-worker", "/sitemap.xml", "/.well-known",
)

# Amostragem para rotas muito frequentes
SAMPLE_RATE_DEFAULT = float(os.getenv("TIMING_SAMPLE_RATE", "1.0"))  # 1.0 = 100%
SAMPLE_RATE_POLL    = float(os.getenv("TIMING_SAMPLE_RATE_POLL", "0.02"))  # 2% para /check-pix-status

# Limiar padrão
THRESHOLD_MS = int(os.getenv("TIMING_THRESHOLD_MS", "200"))

def _should_sample(path: str) -> bool:
    # /check-pix-status pode gerar muito ruído; amostramos
    if path.startswith("/check-pix-status"):
        return random.random() < SAMPLE_RATE_POLL
    # demais rotas seguem sample global
    return random.random() < SAMPLE_RATE_DEFAULT

class TimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.threshold_ms = THRESHOLD_MS
        logger.info(json.dumps({"type":"TIMING_INIT","threshold_ms": self.threshold_ms,
                                "sample_default": SAMPLE_RATE_DEFAULT, "sample_poll": SAMPLE_RATE_POLL}))

    def __call__(self, request):
        path = request.path

        # pula estáticos e similares
        if any(path.startswith(p) for p in STATIC_PREFIXES):
            return self.get_response(request)

        t0 = time.perf_counter()
        response = self.get_response(request)
        dur_ms = int((time.perf_counter() - t0) * 1000)

        # Loga só se: ultrapassou limiar OU status >= 400
        status = getattr(response, "status_code", 0)
        if (dur_ms > self.threshold_ms or status >= 400) and _should_sample(path):
            # Uma linha JSON, prefixada por "TIMING " para filtrar fácil
            payload = {
                "type": "TIMING",
                "path": path,
                "method": request.method,
                "status": status,
                "ms": dur_ms,
                # campos úteis p/ diagnóstico
                "user": getattr(getattr(request, "user", None), "id", None),
                "qs": request.META.get("QUERY_STRING")[:120] if request.META.get("QUERY_STRING") else "",
            }
            logger.info("TIMING " + json.dumps(payload, ensure_ascii=False))
        return response

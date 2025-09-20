import os
import json
import hashlib
import logging
import threading
import time
import re
import unicodedata
import random
from unidecode import unidecode
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_page
from django.db import transaction as dj_tx
from django.urls import reverse
from django.core.cache import cache
from django_redis import get_redis_connection
from requests.exceptions import RequestException
from payments.service import get_active_adapter, get_active_provider
from utils.http import http_get, http_post
from utils.pix_cache import get_cached_pix, set_cached_pix, with_user_pix_lock

from accounts.models import CustomUser, UserProfile, Wallet, Transaction, Notification, PixTransaction
from .capi import lookup_click
from .forms import SendForm, WithdrawForm


logger = logging.getLogger(__name__)

UTMIFY_API_TOKEN = os.getenv('UTMIFY_API_TOKEN', '')
UTMIFY_ENDPOINT = os.getenv('UTMIFY_ENDPOINT', 'https://api.utmify.com.br/api-credentials/orders')
UTMIFY_GATEWAY_FEE_CENTS = int(os.getenv('UTMIFY_GATEWAY_FEE_CENTS', '0') or 0)
UTMIFY_USER_COMMISSION_CENTS = int(os.getenv('UTMIFY_USER_COMMISSION_CENTS', '0') or 0)
UTMIFY_PLAN_ID = "validation"
UTMIFY_PLAN_NAME = "Taxa de validação - CoinTex"
UTMIFY_MAX_RETRIES = 2
UTMIFY_RETRY_BACKOFFS = [0.4, 0.8]
_ALLOWED_STATUSES = {"waiting_payment", "paid", "refused"}
SYSTEM_EMAIL_RE = re.compile(r"^[a-z]{6,}\d{4}@(gmail\.com|outlook\.com)$")

def _to_decimal(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

def format_number_br(value, decimals=2, default="—", with_sign=False):
    """
    Formata número no padrão pt-BR (1.234,56).
    - Aceita Decimal, int, float, str ou None.
    - Se não der pra converter, devolve `default`.
    - with_sign=True: inclui + / -.
    """
    num = _to_decimal(value)
    if num is None:
        return default

    quant = Decimal('1').scaleb(-decimals)  # 10**-decimals
    try:
        num = num.quantize(quant)
    except Exception:
        return default

    body = f"{abs(num):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if with_sign:
        sign = "+" if num > 0 else ("-" if num < 0 else "")
        return f"{sign}{body}"
    return body

def is_system_email(email: str) -> bool:
    s = (email or "").strip().lower()
    return bool(SYSTEM_EMAIL_RE.match(s))

def _digits_only_global(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def norm_phone_br_digits(raw: str) -> str:
    """
    Retorna somente dígitos no formato E.164 BR sem '+',
    ex.: '5511998765432'. Aceita entrada com/sem +55.
    """
    d = _digits_only_global(raw)
    if not d:
        return ""
    if d.startswith("55"):
        base = d
    elif 10 <= len(d) <= 11:
        base = "55" + d
    else:
        return ""
    # 12 dígitos (fixo) ou 13 dígitos (celular com '9')
    return base if 12 <= len(base) <= 13 else ""

DDD_POOL = ["11","21","31","41","51","61","71","81","91","48"]

def generate_system_phone() -> str:
    """
    Gera telefone brasileiro E.164 com '+', sempre celular (9 + 8 dígitos).
    Ex.: '+5511977211450'
    """
    ddd = random.choice(DDD_POOL)
    number = "9" + "".join(str(random.randint(0, 9)) for _ in range(8))
    return f"+55{ddd}{number}"

EMAIL_PROVIDERS = getattr(settings, "SYSTEM_EMAIL_PROVIDERS", ["gmail.com", "outlook.com", "icloud.com"])

def generate_system_email(user) -> str:
    """
    Replica o padrão da outra view:
    unidecode(first+last).lower() + 4 dígitos + @ (gmail/outlook).
    Garante unicidade razoável contra a tabela de usuários.
    """
    fn = (getattr(user, "first_name", "") or "user").strip().lower()
    ln = (getattr(user, "last_name", "") or "cointex").strip().lower()
    base = unidecode(f"{fn}{ln}")
    # tenta algumas vezes garantir unicidade
    for _ in range(10):
        uname = f"{base}{random.randint(1000, 9999)}"
        prov = random.choice(EMAIL_PROVIDERS)
        em = f"{uname}@{prov}"
        if not CustomUser.objects.filter(email__iexact=em).exists():
            return em
    # fallback
    return f"{base}{int(time.time())%10000}@{random.choice(EMAIL_PROVIDERS)}"


def _utmify_field_for_status(status_str: str) -> str | None:
    m = {
        "waiting_payment": "utmify_waiting_sent_at",
        "paid": "utmify_paid_sent_at",
        "refused": "utmify_refused_sent_at",
    }
    return m.get((status_str or "").strip().lower())


def _utmify_already_sent(pix_transaction, status_str: str) -> bool:
    try:
        field = _utmify_field_for_status(status_str)
        return bool(getattr(pix_transaction, field, None))
    except Exception:
        return False


def _utmify_mark_sent(pix_transaction, status_str: str, http_status: int, ok: bool, resp_excerpt: str = ""):
    try:
        field = _utmify_field_for_status(status_str)
        if field:
            setattr(pix_transaction, field, timezone.now())
        pix_transaction.utmify_last_http_status = int(http_status) if http_status is not None else None
        pix_transaction.utmify_last_ok = bool(ok)
        pix_transaction.utmify_last_resp_excerpt = (resp_excerpt or "")[:400]
        pix_transaction.save(update_fields=[
            field, "utmify_last_http_status", "utmify_last_ok", "utmify_last_resp_excerpt"
        ] if field else ["utmify_last_http_status", "utmify_last_ok", "utmify_last_resp_excerpt"])
    except Exception as e:
        logger.warning("[UTMIFY-BOOK] mark_sent_failed txid=%s status=%s err=%s",
                       getattr(pix_transaction, 'transaction_id', None), status_str, e)


def _iso8601(dt):
    try:
        if not dt.tzinfo:
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt.isoformat()
    except Exception:
        return None

def send_utmify_order(
    *,
    status_str: str,
    txid: str,
    amount_brl: float,
    click_data: dict,
    created_at,
    approved_at=None,
    payment_method: str = "pix",
    is_test: bool = False,
    pix_transaction=None,
):
    status_str = (status_str or "").strip().lower()
    if status_str not in _ALLOWED_STATUSES:
        logger.info("[UTMIFY-SKIP] txid=%s status=%s reason=unsupported_status", txid, status_str)
        return {"ok": False, "skipped": "unsupported_status"}

    if not UTMIFY_API_TOKEN:
        logger.info("[UTMIFY-SKIP] reason=no_token txid=%s", txid)
        return {"ok": False, "skipped": "no_token"}

    if pix_transaction is not None and _utmify_already_sent(pix_transaction, status_str):
        logger.info("[UTMIFY-SKIP] txid=%s status=%s reason=idempotent_already_sent", txid, status_str)
        return {"ok": True, "skipped": "idempotent"}

    def _safe_str(v): return v if (isinstance(v, str) and v.strip()) else None

    def _digits_only(s) -> str:
        import re as _re
        return _re.sub(r"\D+", "", str(s or ""))

    def _to_e164_br(raw: str) -> str:
        d = _digits_only(raw)
        if not d:
            return ""
        if d.startswith("55"):
            return d
        return ("55" + d) if 10 <= len(d) <= 11 else d

    def _clean_name(s: str | None) -> str:
        s = (s or "").strip()
        return s.replace("|", " ").replace("#", " ").replace("&", " ").replace("?", " ").strip()

    def _pipe(name: str | None, _id: str | None, fallback_label: str) -> str:
        name = _clean_name(name) or fallback_label
        _id = _digits_only(_id) or "-"
        return f"{name}|{_id}"

    txid_str = str(txid or "")
    try:
        price_in_cents = int(round(float(amount_brl or 0) * 100))
    except Exception:
        price_in_cents = 0
    if price_in_cents <= 0:
        logger.info("[UTMIFY-SKIP] txid=%s reason=non_positive_total amount_brl=%s price_cents=%s",
                    txid_str, amount_brl, price_in_cents)
        return {"ok": False, "skipped": "non_positive_total"}

    safe_click = click_data if isinstance(click_data, dict) else {}

    # ------- Cliente -------
    phone_raw = (_safe_str(safe_click.get("phone"))
                or _safe_str(safe_click.get("ph"))
                or _safe_str(safe_click.get("ph_raw"))
                or _safe_str(safe_click.get("wa_id")))
    phone_e164 = _to_e164_br(phone_raw) if phone_raw else ""
    customer = {
        "name":     _safe_str(safe_click.get("name")) or "Cliente Cointex",
        "email":    _safe_str(safe_click.get("email")) or f"unknown+{txid_str[:8]}@cointex.local",
        "phone":    phone_e164 or None,
        "country":  (_safe_str(safe_click.get("country")) or "BR")[:2].upper(),
        "document": _safe_str(safe_click.get("document")),
    }

    click_type = _safe_str((safe_click or {}).get("click_type")) or ""
    is_ctwa = (click_type.upper() == "CTWA") or bool(_safe_str((safe_click or {}).get("wa_id")))
    lp_extra_tracking = {}  # metadados de diagnóstico para LP

    # ===== ROTA C → B → A (CTWA) =====
    camp_name = camp_id = adset_name = adset_id = ad_name = ad_id = placement = None
    if is_ctwa:
        try:
            from core.ctwa_catalog import build_ctwa_utm_from_offline
            c = build_ctwa_utm_from_offline(safe_click)
            camp_name = c.get("campaign_name"); camp_id = c.get("campaign_id")
            adset_name = c.get("adset_name");   adset_id = c.get("adset_id")
            ad_name = c.get("ad_name");         ad_id = c.get("ad_id")
            placement = c.get("placement")
        except Exception:
            pass
        if not (camp_id and ad_id):
            try:
                from core.meta_lookup import build_ctwa_utm_from_meta
                b = build_ctwa_utm_from_meta(safe_click)
                camp_name = camp_name or b.get("campaign_name"); camp_id = camp_id or b.get("campaign_id")
                adset_name = adset_name or b.get("adset_name");   adset_id = adset_id or b.get("adset_id")
                ad_name = ad_name or b.get("ad_name");           ad_id = ad_id or b.get("ad_id")
                placement = placement or b.get("placement")
            except Exception:
                pass

    camp_id  = _digits_only(camp_id  or safe_click.get("campaign_id"))
    adset_id = _digits_only(adset_id or safe_click.get("adset_id"))
    ad_id    = _digits_only(ad_id    or safe_click.get("ad_id") or safe_click.get("source_id"))

    # ------- UTM building -------
    if is_ctwa:
        utm_source   = "FB"
        utm_campaign = _pipe(camp_name, camp_id,  "CTWA-CAMPAIGN")
        utm_medium   = _pipe(adset_name, adset_id, "CTWA-ADSET")
        utm_content  = _pipe(ad_name,   ad_id,     "CTWA-AD")
        utm_term     = (placement or "ctwa")
    else:
        utm = safe_click.get("utm") or {}

        # capturas originais só para diagnóstico/auditoria
        orig_src = _safe_str(utm.get("utm_source")) or ""
        orig_med = _safe_str(utm.get("utm_medium")) or ""
        orig_cam = _safe_str(utm.get("utm_campaign")) or ""
        orig_cnt = _safe_str(utm.get("utm_content")) or ""
        orig_trm = _safe_str(utm.get("utm_term")) or ""

        # 1) O que vier explícito do clique
        utm_source   = orig_src or None
        utm_medium   = orig_med or None
        utm_campaign = orig_cam or None
        utm_content  = orig_cnt or None
        utm_term     = orig_trm or None

        # 2) Heurísticas de LP para preencher válidos no padrão "nome|id"
        from urllib.parse import urlparse

        def _host(u: str) -> str:
            try:
                return (urlparse(u).hostname or "").lower()
            except Exception:
                return ""

        def _route(u: str) -> str:
            try:
                p = (urlparse(u).path or "/").strip("/")
                return p or "root"
            except Exception:
                return "root"

        page_url = _safe_str(safe_click.get("page_url") or safe_click.get("source_url") or safe_click.get("landing_url"))
        ref      = _safe_str(safe_click.get("referrer") or (safe_click.get("context") or {}).get("referrer"))
        domain   = _host(ref) or "direct"
        route    = _route(page_url)

        # ID numérico para o lado direito do pipe
        lp_id = _digits_only(safe_click.get("tracking_id")) or _digits_only(txid_str) or str(int(time.time()))

        # Diagnóstico: originais inválidos?
        _bad_source   = (not orig_src) or (orig_src.lower() == "site")
        _bad_medium   = (not orig_med) or (orig_med.lower() == "site") or ("|" not in orig_med)
        _bad_campaign = (not orig_cam) or (orig_cam.lower() == "site") or ("|" not in orig_cam)

        # Correções
        if not utm_source or utm_source.lower() == "site":
            utm_source = "LP"

        if (not utm_campaign) or (utm_campaign.lower() == "site") or ("|" not in utm_campaign):
            utm_campaign = _pipe(f"LP {route}", lp_id, "LP-CAMPAIGN")

        if (not utm_medium) or (utm_medium.lower() == "site") or ("|" not in utm_medium):
            ref_id = _digits_only(safe_click.get("fbclid") or safe_click.get("gclid")) or lp_id
            utm_medium = _pipe(f"LP-REF {domain}", ref_id, "LP-REF")

        if (not utm_content) or (utm_content.lower() == "site"):
            utm_content = f"{route}|{txid_str[:6]}"

        if not utm_term:
            utm_term = domain

        lp_fallback_used = int(_bad_source or _bad_medium or _bad_campaign)
        if lp_fallback_used:
            logger.info(
                "[UTMIFY-LP-FALLBACK] txid=%s route=%s ref=%s utm_source=%s utm_campaign=%s utm_medium=%s utm_content=%s utm_term=%s",
                txid_str, route, domain, utm_source, utm_campaign, utm_medium, (utm_content or "-"), (utm_term or "-")
            )

        # Metadados extras para diagnóstico (não impactam a UTMify)
        lp_extra_tracking = {
            "utm_fallback": lp_fallback_used,
            "lp_route": route,
            "lp_ref": domain,
        }

    utm_params = {
        "utm_source":   utm_source or None,
        "utm_medium":   utm_medium or None,
        "utm_campaign": utm_campaign or None,
        "utm_content":  utm_content or None,
        "utm_term":     utm_term or None,
    }

    tracking = {
        "src": ("meta" if is_ctwa else "lp"),
        "campaign_id": camp_id or None,
        "adset_id":    adset_id or None,
        "ad_id":       ad_id or None,
        "ctwa_clid":   _safe_str(safe_click.get("ctwa_clid")),
        "utm_source":   utm_source or None,
        "utm_medium":   utm_medium or None,
        "utm_campaign": utm_campaign or None,
        "utm_content":  utm_content or None,
        "utm_term":     utm_term or None,
    }
    if lp_extra_tracking:
        tracking.update(lp_extra_tracking)

    products = [{
        "id":        safe_click.get("product_id") or "pix_validation",
        "name":      safe_click.get("product_name") or "Taxa de validação - CoinTex",
        "planId":    "validation_fee",
        "planName":  "Taxa de Validação - CoinTex",
        "quantity":  1,
        "priceInCents": price_in_cents,
    }]

    payload = {
        "isTest":        bool(is_test),
        "status":        status_str,
        "orderId":       txid_str,
        "customer":      customer,
        "platform":      "Cointex",
        "products":      products,
        "createdAt":     _iso8601(created_at),
        "approvedDate":  _iso8601(approved_at) if approved_at else None,
        "paymentMethod": payment_method,
        "commission": {
            "gatewayFeeInCents":     UTMIFY_GATEWAY_FEE_CENTS,
            "totalPriceInCents":     price_in_cents,
            "userCommissionInCents": UTMIFY_USER_COMMISSION_CENTS,
        },
        "utmParams": utm_params,
        "trackingParameters": tracking,
    }

    body_preview = json.dumps({
        "utmParams": payload["utmParams"],
        "trackingParameters": payload["trackingParameters"],
    }, ensure_ascii=False)
    if len(body_preview) > 600:
        body_preview = body_preview[:600] + "."
    logger.info("[UTMIFY-PAYLOAD] txid=%s status=%s total_cents=%s preview=%s",
                txid_str, status_str, price_in_cents, body_preview)

    if is_ctwa:
        logger.info(
            "[UTMIFY-CTWA-PAYLOAD] txid=%s status=%s phone=%s utm_source=%s utm_campaign=%s utm_medium=%s utm_content=%s utm_term=%s",
            txid_str, status_str, (phone_e164 or "-"),
            tracking["utm_source"], tracking["utm_campaign"],
            tracking["utm_medium"], (tracking["utm_content"] or "-"), (tracking["utm_term"] or "-")
        )
    else:
        logger.info(
            "[UTMIFY-LP-PAYLOAD] txid=%s status=%s utm_source=%s utm_campaign=%s utm_medium=%s utm_content=%s utm_term=%s",
            txid_str, status_str,
            tracking["utm_source"], tracking["utm_campaign"],
            tracking["utm_medium"], (tracking["utm_content"] or "-"), (tracking["utm_term"] or "-")
        )

    headers = {"Content-Type": "application/json", "x-api-token": UTMIFY_API_TOKEN}
    attempt, last_status, last_text = 0, None, ""
    while True:
        try:
            resp = http_post(UTMIFY_ENDPOINT, headers=headers, json=payload, timeout=(3, 12), measure="utmify/orders")
            last_status = getattr(resp, "status_code", 0)
            last_text   = (getattr(resp, "text", "") or "")[:400].replace("\n", " ")
            ok = 200 <= (last_status or 0) < 300

            logger.info("[UTMIFY-RESP] txid=%s status=%s ok=%s body=%s", txid_str, last_status, int(ok), last_text)
            if is_ctwa:
                logger.info("[UTMIFY-CTWA-RESP] txid=%s status=%s ok=%s body=%s", txid_str, last_status, int(ok), last_text)

            if ok:
                if pix_transaction is not None:
                    _utmify_mark_sent(pix_transaction, status_str, last_status, True, last_text)
                return {"ok": True, "status": last_status}
            else:
                if 400 <= (last_status or 0) < 500:
                    if pix_transaction is not None:
                        _utmify_mark_sent(pix_transaction, status_str, last_status, False, last_text)
                    return {"ok": False, "status": last_status, "error": "client_error"}
                if attempt >= UTMIFY_MAX_RETRIES:
                    if pix_transaction is not None:
                        _utmify_mark_sent(pix_transaction, status_str, last_status, False, last_text)
                    return {"ok": False, "status": last_status, "error": "server_error_max_retries"}
        except Exception as e:
            last_text = f"exception:{e}"
            logger.warning("[UTMIFY-ERR] txid=%s status=%s err=%s", txid_str, last_status, e)
            if is_ctwa:
                logger.info("[UTMIFY-CTWA-RESP] txid=%s status=%s ok=0 body=%s", txid_str, last_status, last_text)
            if attempt >= UTMIFY_MAX_RETRIES:
                if pix_transaction is not None:
                    _utmify_mark_sent(pix_transaction, status_str, last_status, False, last_text)
                return {"ok": False, "status": last_status, "error": "exception_max_retries"}

        delay = UTMIFY_RETRY_BACKOFFS[min(attempt, len(UTMIFY_RETRY_BACKOFFS) - 1)]
        attempt += 1
        try:
            time.sleep(delay)
        except Exception:
            pass

def format_number_br(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

@cache_page(30)
@login_required
def home(request):
    """
    Home com Coingecko + Redis cache.
    Resiliente a valores None/ausentes e sem quebrar quando a API retorna faltantes.
    """
    base_url = 'https://api.coingecko.com/api/v3'
    vs_currency = 'brl'
    per_page_large = 250
    per_page = 10

    params = {
        'vs_currency': vs_currency,
        'order': 'market_cap_desc',
        'per_page': per_page_large,
        'page': 1,
        'sparkline': True,
        'locale': 'pt',
        'price_change_percentage': '24h',
    }

    # helpers locais --------------------------------------------
    def sfloat(x, default=None):
        """Converte para float de forma segura. Se não der, devolve `default`."""
        if x is None:
            return default
        try:
            v = float(x)
            # protege contra NaN
            return v if v == v else default
        except Exception:
            return default

    def money_br(v):
        # usa a sua format_number_br robusta (definida acima no arquivo)
        return f"R$ {format_number_br(v, default='0,00')}"

    # Redis ------------------------------------------------------
    r = None
    try:
        r = get_redis_connection("default")
    except Exception as e:
        logger.warning(f"redis unavailable, will fallback to direct http: {e}")

    KEY_FRESH = "cg:markets:v1:fresh"
    KEY_STALE = "cg:markets:v1:stale"
    KEY_LOCK  = "lock:cg:markets:v1"

    def _refresh_coingecko_async():
        import threading
        def _job():
            lock = None
            try:
                lock = r.lock(KEY_LOCK, timeout=10) if r else None
                if lock and not lock.acquire(blocking=False):
                    return
                resp = http_get(
                    f'{base_url}/coins/markets',
                    params=params,
                    measure="coingecko/markets",
                    timeout=(2, 3)
                )
                data = resp.json() if resp and resp.status_code == 200 else []
                payload = json.dumps(data)
                if r:
                    r.setex(KEY_FRESH, 60, payload)
                    r.setex(KEY_STALE, 300, payload)
            except Exception as e:
                logger.warning(f"coingecko async refresh failed: {e}")
            finally:
                try:
                    if lock and lock.locked():
                        lock.release()
                except Exception:
                    pass
        threading.Thread(target=_job, daemon=True).start()

    # Fetch com cache -------------------------------------------
    main_coins = []
    try:
        if r:
            raw = r.get(KEY_FRESH)
            if raw:
                main_coins = json.loads(raw)
            else:
                _refresh_coingecko_async()
                raw_stale = r.get(KEY_STALE)
                if raw_stale:
                    main_coins = json.loads(raw_stale)
                else:
                    try:
                        response = http_get(
                            f'{base_url}/coins/markets',
                            params=params,
                            measure="coingecko/markets",
                            timeout=(2, 3)
                        )
                        main_coins = response.json() if response.status_code == 200 else []
                        if main_coins:
                            r.setex(KEY_STALE, 300, json.dumps(main_coins))
                    except RequestException as e:
                        logger.warning(f"coingecko quick fetch failed: {e}")
                        main_coins = []
        else:
            try:
                response = http_get(
                    f'{base_url}/coins/markets',
                    params=params,
                    measure="coingecko/markets",
                    timeout=(2, 3)
                )
                main_coins = response.json() if response.status_code == 200 else []
            except RequestException as e:
                logger.warning(f"coingecko fetch failed (no redis): {e}")
                main_coins = []
    except Exception as e:
        logger.warning(f"redis/cache path failed, fallback http: {e}")
        try:
            response = http_get(
                f'{base_url}/coins/markets',
                params=params,
                measure="coingecko/markets",
                timeout=(2, 3)
            )
            main_coins = response.json() if response.status_code == 200 else []
        except RequestException as e2:
            logger.warning(f"coingecko fetch failed: {e2}")
            main_coins = []

    # Listas -----------------------------------------------------
    hot_coins = main_coins[:per_page]

    # top_gainers: calcula uma vez por item, ignora None e positivos apenas
    tmp_gainers = []
    for c in main_coins:
        chg = sfloat(c.get('price_change_percentage_24h'), None)
        if chg is not None and chg > 0:
            tmp_gainers.append((chg, c))
    tmp_gainers.sort(key=lambda t: t[0], reverse=True)
    top_gainers = [c for _, c in tmp_gainers[:per_page]]

    popular_coins = sorted(main_coins, key=lambda x: sfloat(x.get('total_volume'), 0.0), reverse=True)[:per_page]
    price_coins   = sorted(main_coins, key=lambda x: sfloat(x.get('current_price'), 0.0), reverse=True)[:per_page]
    favorites_coins = hot_coins  # placeholder

    # Formatação segura -----------------------------------------
    for lst in (hot_coins, favorites_coins, top_gainers, popular_coins, price_coins):
        for coin in lst:
            cp   = coin.get('current_price')
            chg  = coin.get('price_change_percentage_24h')
            spark = (coin.get('sparkline_in_7d') or {}).get('price') or []

            coin['formatted_current_price'] = money_br(cp)
            coin['formatted_price_change']  = format_number_br(chg, default="0,00")

            try:
                coin['sparkline_json'] = json.dumps(spark)
            except Exception:
                coin['sparkline_json'] = "[]"
            coin['chart_color'] = '#26de81' if sfloat(chg, 0.0) > 0 else '#fc5c65'

    # Saldo do usuário ------------------------------------------
    try:
        wallet = request.user.wallet
        user_balance = wallet.balance
        formatted_balance = money_br(user_balance)
    except Wallet.DoesNotExist:
        user_balance = Decimal('0.00')
        formatted_balance = "R$ 0,00"

    track_complete_registration = request.session.pop('track_complete_registration', False)

    context = {
        'hot_coins': hot_coins,
        'favorites_coins': favorites_coins,
        'top_gainers': top_gainers,
        'popular_coins': popular_coins,
        'price_coins': price_coins,
        'formatted_balance': formatted_balance,
        'track_complete_registration': track_complete_registration,
        'user_balance': float(user_balance),
    }
    return render(request, 'core/home.html', context)

@login_required
def user_info(request):
    user = request.user
    profile = user.profile if hasattr(user, 'profile') else None

    context = {
        'full_name': f"{user.first_name} {user.last_name}",
        'verification_status': 'Verificado' if user.is_verified else 'Não verificado',
        'verification_class': 'green' if user.is_verified else 'red',
        'uid': user.uid_code,
        'cpf': user.cpf if user.cpf else 'Não informado',
        'phone_number': user.phone_number if user.phone_number else 'Não informado',
        'address': profile.address if profile else 'Não informado',
    }
    return render(request, 'core/user-info.html', context)


@login_required
def profile(request):
    user = request.user

    if hasattr(user, 'is_advanced_verified') and user.is_advanced_verified:
        verification_status = 'Verificado (Avançado)'
        verification_class = 'text-green'
    elif user.is_verified:
        verification_status = 'Verificado (Básico)'
        verification_class = 'text-green'
    else:
        verification_status = 'Não verificado'
        verification_class = 'text-red'

    context = {
        'verification_status': verification_status,
        'verification_class': verification_class,
        'uid': user.uid_code,
        'full_name': f"{user.first_name} {user.last_name}",
    }
    return render(request, 'core/profile.html', context)


@login_required
def verification(request):
    user = request.user
    is_advanced_verified = hasattr(user, 'is_advanced_verified') and user.is_advanced_verified
    context = {
        'is_verified': user.is_verified,
        'is_advanced_verified': is_advanced_verified,
    }
    return render(request, 'core/verification.html', context)


@login_required
def verification_choose_type(request):
    if request.method == 'POST':
        country = request.POST.get('country')
        if country != 'Brasil':
            messages.error(request, 'O país selecionado é diferente da sua localização atual.')
            return render(request, 'core/verification-choose-type.html')
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.country = country
        profile.save()
        return redirect('core:verification_personal')
    return render(request, 'core/verification-choose-type.html')


@login_required
def verification_personal(request):
    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        document_type = request.POST.get('document_type')
        document_number = request.POST.get('document_number')

        if document_type != 'CPF':
            messages.error(request, 'Tipo de documento inválido.')
            return render(request, 'core/verification-personal.html')

        user = request.user
        user.cpf = document_number
        try:
            user.clean()
        except ValidationError as e:
            messages.error(request, str(e))
            return render(request, 'core/verification-personal.html')

        names = full_name.split()
        user.first_name = names[0] if names else ''
        user.last_name = ' '.join(names[1:]) if len(names) > 1 else ''
        user.save()

        return redirect('core:verification_address')

    return render(request, 'core/verification-personal.html')


@login_required
def verification_address(request):
    if request.method == 'POST':
        cep = request.POST.get('cep')
        endereco = request.POST.get('endereco')
        numero = request.POST.get('numero')
        cidade = request.POST.get('cidade')
        estado = request.POST.get('estado')

        full_address = f"{endereco} {numero}, {cidade} - {estado}, CEP {cep}"

        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.address = full_address
        profile.save()

        user = request.user
        user.is_verified = True
        user.save()

        messages.success(request, 'Verificação básica concluída com sucesso!')
        return redirect('core:profile')

    return render(request, 'core/verification-address.html')


@login_required
def change_name(request):
    user = request.user
    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        if full_name:
            names = full_name.split()
            user.first_name = names[0] if names else ''
            user.last_name = ' '.join(names[1:]) if len(names) > 1 else ''
            user.save()
            messages.success(request, 'Nome alterado com sucesso!')
            return redirect('core:profile')
        else:
            messages.error(request, 'Nome inválido.')

    context = {
        'full_name': f"{user.first_name} {user.last_name}",
    }
    return render(request, 'core/change-name.html', context)


@login_required
def change_email(request):
    user = request.user
    if request.method == 'POST':
        email = request.POST.get('email')
        if email and email != user.email:
            if CustomUser.objects.filter(email=email).exists():
                messages.error(request, 'E-mail já em uso.')
            else:
                user.email = email
                user.save()
                messages.success(request, 'E-mail alterado com sucesso!')
                return redirect('core:profile')
        else:
            messages.error(request, 'E-mail inválido ou inalterado.')

    context = {'email': user.email}
    return render(request, 'core/change-email.html', context)


@login_required
def change_phone(request):
    user = request.user
    if request.method == 'POST':
        phone_number = request.POST.get('phone_number')
        if phone_number:
            user.phone_number = phone_number
            user.save()
            messages.success(request, 'Telefone alterado com sucesso!')
            return redirect('core:profile')
        else:
            messages.error(request, 'Telefone inválido.')

    context = {'phone_number': user.phone_number or ''}
    return render(request, 'core/change-phone.html', context)


@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Senha alterada com sucesso!')
            return redirect('core:profile')
        else:
            messages.error(request, 'Erro ao alterar senha. Verifique os campos.')
    else:
        form = PasswordChangeForm(user=request.user)

    context = {'form': form}
    return render(request, 'core/change-password.html', context)


@login_required
def send_balance(request):
    if not request.user.is_verified:
        return render(request, 'core/send.html', {'error_message': 'Você precisa ser verificado para enviar saldo.', 'form': SendForm()})

    try:
        wallet = request.user.wallet
    except Wallet.DoesNotExist:
        wallet = Wallet.objects.create(user=request.user, currency='BRL', balance=Decimal('0.00'))

    formatted_balance = format_number_br(wallet.balance)

    form = SendForm(request.POST or None)
    context = {
        'form': form,
        'formatted_balance': formatted_balance,
    }

    if request.method == 'POST' and form.is_valid():
        try:
            amount_str = form.cleaned_data['amount'].replace('.', '').replace(',', '.')
            amount = Decimal(amount_str)

            if amount < Decimal('0.01'):
                raise ValueError("Quantia mínima é 0.01")

            recipient = CustomUser.objects.get(email=form.cleaned_data['recipient_email'])
            if recipient == request.user:
                raise ValueError("Não pode enviar para si mesmo.")

            try:
                recipient_wallet = recipient.wallet
            except Wallet.DoesNotExist:
                recipient_wallet = Wallet.objects.create(user=recipient, currency='BRL', balance=Decimal('0.00'))

            if wallet.balance < amount:
                raise ValueError("Saldo insuficiente.")

            with dj_tx.atomic():
                wallet.balance -= amount
                wallet.save()

                recipient_wallet.balance += amount
                recipient_wallet.save()

                Transaction.objects.create(
                    wallet=wallet, type='SEND', amount=amount, currency='BRL',
                    to_address=recipient_wallet.user.email,
                    fee=Decimal('0.00'), status='COMPLETED'
                )
                Transaction.objects.create(
                    wallet=recipient_wallet, type='RECEIVE', amount=amount, currency='BRL',
                    from_address=wallet.user.email,
                    status='COMPLETED'
                )

            Notification.objects.create(
                user=recipient_wallet.user,
                title="Saldo Recebido",
                message=f"Você recebeu {amount} BRL de {wallet.user.get_full_name()} ({wallet.user.email})."
            )

            context['success'] = True
            context['transaction_amount'] = format_number_br(amount)
            context['recipient_email'] = recipient.email
            context['form'] = SendForm()

        except InvalidOperation:
            context['error_message'] = 'Formato de quantia inválido. Use formato como 500,00.'
        except CustomUser.DoesNotExist:
            context['error_message'] = 'Destinatário não encontrado pelo email.'
        except ValueError as e:
            context['error_message'] = str(e)
        except Exception as e:
            logger.exception("Erro na transferência")
            context['error_message'] = f'Erro na transferência: {str(e)}. Tente novamente.'

    return render(request, 'core/send.html', context)


@login_required
def withdraw_balance(request):
    try:
        wallet = request.user.wallet
    except Wallet.DoesNotExist:
        wallet = Wallet.objects.create(user=request.user, currency='BRL', balance=Decimal('0.00'))

    formatted_balance = format_number_br(wallet.balance)

    form = WithdrawForm(request.POST or None)
    context = {
        'form': form,
        'formatted_balance': formatted_balance,
        'balance_raw': wallet.balance,
        'fee_percentage': 3,
        'min_withdraw': Decimal('10.00'),
        'max_withdraw_daily': Decimal('50000.00'),
        'estimated_time': 'Instantâneo via PIX (até 10 minutos)',
    }

    pix_transaction = PixTransaction.objects.filter(user=request.user).order_by('-created_at').first()
    if pix_transaction:
        validation_status = 'payment_reported' if pix_transaction.paid_at else 'pix_created'
    else:
        validation_status = None

    context['validation_status'] = validation_status
    context['pix_transaction'] = pix_transaction
    context['pix_config'] = {'amount': Decimal('17.81')}
    context['can_generate_pix'] = True if not pix_transaction or not pix_transaction.paid_at else False

    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        if form.is_valid():
            try:
                amount_str = form.cleaned_data['amount'].replace('.', '').replace(',', '.')
                amount = Decimal(amount_str)

                if amount < context['min_withdraw']:
                    raise ValueError(f"Quantia mínima é {format_number_br(context['min_withdraw'])}")

                if amount > context['max_withdraw_daily']:
                    raise ValueError(f"Quantia máxima diária é {format_number_br(context['max_withdraw_daily'])}")

                if wallet.balance < amount:
                    raise ValueError("Saldo insuficiente.")

                if form.cleaned_data['pin'] != request.user.withdrawal_pin:
                    raise ValueError("PIN de saque inválido.")

                if not request.user.is_advanced_verified:
                    raise ValueError("Você precisa de verificação avançada para sacar.")

                with dj_tx.atomic():
                    wallet.balance -= amount
                    wallet.save()

                    Transaction.objects.create(
                        wallet=wallet, type='WITHDRAW', amount=amount, currency='BRL',
                        to_address=form.cleaned_data['pix_key'],
                        fee=Decimal('0.00'), status='COMPLETED'
                    )

                Notification.objects.create(
                    user=request.user,
                    title="Saque Realizado",
                    message=f"Você sacou {amount} BRL via PIX para {form.cleaned_data['pix_key']}."
                )

                return JsonResponse({
                    'success': True,
                    'transaction_amount': format_number_br(amount),
                    'pix_key': form.cleaned_data['pix_key'],
                    'new_balance': format_number_br(wallet.balance)
                })

            except InvalidOperation:
                return JsonResponse({'success': False, 'error_message': 'Formato de quantia inválido. Use formato como 500,00.'})
            except ValueError as e:
                return JsonResponse({'success': False, 'error_message': str(e)})
            except Exception as e:
                logger.exception("Erro no saque")
                return JsonResponse({'success': False, 'error_message': f'Erro no saque: {str(e)}. Tente novamente.'})
        else:
            return JsonResponse({'success': False, 'error_message': 'Formulário inválido. Verifique os campos.'})

    return render(request, 'core/withdraw.html', context)

@login_required
def withdraw_validation(request):
    import random
    import string
    import time
    from decimal import Decimal

    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    forwarded_for = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()

    if request.method == 'POST':
        t0 = time.perf_counter()

        user = request.user
        external_id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))

        name = f"{user.first_name} {user.last_name}".strip() or "Teste"
        email = generate_system_email(user)
        phone = generate_system_phone()
        document = getattr(user, 'cpf', None) or "47649792099"
        ip = request.META.get('REMOTE_ADDR', '111.111.11.11')
        webhook_url = request.build_absolute_uri(reverse('payments:webhook_pix'))

        try:
            # ---------- Reuso de cache (com hardening de tipo) ----------
            try:
                cached = get_cached_pix(user.id)
                if cached is not None and not isinstance(cached, dict):
                    logger.warning(
                        "pix.cache unexpected type on read user_id=%s type=%s val=%s",
                        user.id, type(cached).__name__, str(cached)[:200]
                    )
                    cached = None
            except Exception as e:
                logger.warning("pix.cache read failed user_id=%s err=%s", user.id, e)
                cached = None

            if cached and not cached.get("paid") and not cached.get("expired"):
                payload = cached.get("qr_code")
                logger.info("pix.create reused_from_cache user_id=%s", user.id)
                if ajax:
                    return JsonResponse({
                        'status': 'success',
                        'qr_code': payload,
                        'amount': float(Decimal('17.81')),
                        'can_generate_pix': True
                    }, status=200)
                else:
                    return redirect('core:withdraw_validation')

            adapter = get_active_adapter()
            active_provider = get_active_provider().name

            # ---------- Single-flight por usuário ----------
            with with_user_pix_lock(user.id) as acquired:
                if acquired:
                    adapter_resp = adapter.create_transaction(
                        external_id=external_id,
                        amount=Decimal("17.81"),
                        customer={"name": name, "email": email, "document": document, "phone": phone},
                        webhook_url=webhook_url,
                        meta={"ip": ip, "xff": forwarded_for, "idempotency_key": f"pix_{user.id}_{external_id}"},
                    )

                    if not isinstance(adapter_resp, dict):
                        raise ValueError(f"adapter_resp not dict: {type(adapter_resp).__name__}")

                    elapsed_api_ms = (time.perf_counter() - t0) * 1000.0
                    logger.info(
                        "pix.create response provider=%s elapsed_ms=%.0f txid=%s hash_id=%s",
                        active_provider, elapsed_api_ms,
                        adapter_resp.get("transaction_id"),
                        adapter_resp.get("hash_id")
                    )

                    deleted = PixTransaction.objects.filter(user=user, paid_at__isnull=True).delete()
                    if deleted and isinstance(deleted, tuple):
                        logger.info("pix.create deleted_unpaid_count=%s", deleted[0])

                    payload = adapter_resp.get("pix_qr")
                    status = adapter_resp.get("status") or 'PENDING'
                    txid = adapter_resp.get("transaction_id")
                    hash_id = adapter_resp.get("hash_id")

                    with dj_tx.atomic():
                        fields = {
                            "user": user,
                            "external_id": external_id,
                            "transaction_id": txid,
                            "amount": Decimal('17.81'),
                            "status": status,
                            "qr_code": payload,
                        }
                        model_fields = {f.name for f in PixTransaction._meta.fields}
                        if "provider" in model_fields:
                            fields["provider"] = active_provider
                        if "hash_id" in model_fields:
                            fields["hash_id"] = hash_id

                        pix_transaction = PixTransaction.objects.create(**fields)

                        tracking_id = getattr(user, 'tracking_id', '') or ''
                        click_type = getattr(user, 'click_type', '') or ''
                        click_data = lookup_click(tracking_id, click_type) if tracking_id else {}
                        
                        click_data = (click_data or {}).copy()
                        click_data["tracking_id"] = tracking_id or ""
                        click_data["click_type"] = (click_type or "").upper()
                        
                        persisted_ph = norm_phone_br_digits(getattr(user, "phone_number", ""))
                        if persisted_ph:
                            click_data["phone"] = persisted_ph
                            click_data["ph"] = persisted_ph

                        user_email = (getattr(user, "email", "") or "").strip().lower()
                        if user_email and not is_system_email(user_email):
                            click_data["email"] = user_email
                            click_data["em"] = user_email
                        else:
                            click_data.pop("email", None)
                            click_data.pop("em", None)

                        send_utmify_order(
                            status_str="waiting_payment",
                            txid=pix_transaction.transaction_id or f"pix:{pix_transaction.external_id}",
                            amount_brl=float(pix_transaction.amount or 0),
                            click_data=click_data,
                            created_at=pix_transaction.created_at,
                            approved_at=None,
                            payment_method="pix",
                            is_test=False,
                            pix_transaction=pix_transaction,
                        )

                    normalized = {
                        "qr_code": payload,
                        "txid": txid,
                        "raw": adapter_resp if isinstance(adapter_resp, dict) else {},
                        "paid": False,
                        "expired": False,
                        "ts": int(time.time())
                    }
                    set_cached_pix(user.id, normalized, ttl=300)
                    logger.info("pix.cache saved user_id=%s keys=%s ttl=%s", user.id, list(normalized.keys()), 300)

                    logger.info(
                        "pix.create saved txn_id=%s status=%s (cached) provider=%s",
                        pix_transaction.transaction_id, pix_transaction.status, active_provider
                    )

                    if ajax:
                        return JsonResponse({
                            'status': 'success',
                            'qr_code': payload,
                            'amount': float(pix_transaction.amount),
                            'can_generate_pix': True
                        }, status=200)
                    else:
                        return redirect('core:withdraw_validation')

                else:
                    # Outro request está criando; aguarda até 2s o cache aparecer (com hardening de tipo)
                    t_wait = time.time()
                    while time.time() - t_wait < 2.0:
                        tmp = get_cached_pix(user.id)
                        if tmp is not None and not isinstance(tmp, dict):
                            logger.warning(
                                "pix.cache unexpected type while waiting user_id=%s type=%s val=%s",
                                user.id, type(tmp).__name__, str(tmp)[:200]
                            )
                            tmp = None
                        if tmp:
                            payload = tmp.get("qr_code")
                            if ajax:
                                return JsonResponse({
                                    'status': 'success',
                                    'qr_code': payload,
                                    'amount': float(Decimal('17.81')),
                                    'can_generate_pix': True
                                }, status=200)
                            else:
                                return redirect('core:withdraw_validation')
                        time.sleep(0.1)

                    # Fallback defensivo: tenta criar
                    adapter = get_active_adapter()
                    active_provider = get_active_provider().name
                    adapter_resp = adapter.create_transaction(
                        external_id=external_id,
                        amount=Decimal("17.81"),
                        customer={"name": name, "email": email, "document": document, "phone": phone},
                        webhook_url=webhook_url,
                        meta={"ip": ip, "xff": forwarded_for, "idempotency_key": f"pix_{user.id}_{external_id}"},
                    )

                    if not isinstance(adapter_resp, dict):
                        raise ValueError(f"adapter_resp not dict: {type(adapter_resp).__name__}")

                    payload = adapter_resp.get("pix_qr")
                    status = adapter_resp.get("status") or 'PENDING'
                    txid = adapter_resp.get("transaction_id")
                    hash_id = adapter_resp.get("hash_id")

                    with dj_tx.atomic():
                        fields = {
                            "user": user,
                            "external_id": external_id,
                            "transaction_id": txid,
                            "amount": Decimal('17.81'),
                            "status": status,
                            "qr_code": payload
                        }
                        model_fields = {f.name for f in PixTransaction._meta.fields}
                        if "provider" in model_fields:
                            fields["provider"] = active_provider
                        if "hash_id" in model_fields:
                            fields["hash_id"] = hash_id

                        pix_transaction = PixTransaction.objects.create(**fields)
                        
                        tracking_id = getattr(user, 'tracking_id', '') or ''
                        click_type  = getattr(user, 'click_type', '') or ''
                        click_data  = lookup_click(tracking_id, click_type) if tracking_id else {}

                        click_data  = (click_data or {}).copy()
                        click_data["tracking_id"] = tracking_id or ""
                        click_data["click_type"]  = (click_type or "").upper()
                        
                        persisted_ph = norm_phone_br_digits(getattr(user, "phone_number", ""))
                        if persisted_ph:
                            click_data["phone"] = persisted_ph
                            click_data["ph"] = persisted_ph

                        user_email = (getattr(user, "email", "") or "").strip().lower()
                        if user_email and not is_system_email(user_email):
                            click_data["email"] = user_email
                            click_data["em"] = user_email
                        else:
                            click_data.pop("email", None)
                            click_data.pop("em", None)

                        send_utmify_order(
                            status_str="waiting_payment",
                            txid=pix_transaction.transaction_id or f"pix:{pix_transaction.external_id}",
                            amount_brl=float(pix_transaction.amount or 0),
                            click_data=click_data,
                            created_at=pix_transaction.created_at,
                            approved_at=None,
                            payment_method="pix",
                            is_test=False,
                            pix_transaction=pix_transaction,
                        )

                    normalized = {
                        "qr_code": payload,
                        "txid": txid,
                        "raw": adapter_resp if isinstance(adapter_resp, dict) else {},
                        "paid": False,
                        "expired": False,
                        "ts": int(time.time())
                    }
                    set_cached_pix(user.id, normalized, ttl=300)
                    logger.info("pix.cache saved user_id=%s keys=%s ttl=%s", user.id, list(normalized.keys()), 300)

                    if ajax:
                        return JsonResponse({
                            'status': 'success',
                            'qr_code': payload,
                            'amount': float(pix_transaction.amount),
                            'can_generate_pix': True
                        }, status=200)
                    else:
                        return redirect('core:withdraw_validation')

        except Exception:
            logger.exception("pix.create error")
            if ajax:
                return JsonResponse({'status': 'error', 'message': 'Erro ao processar resposta da API PIX'}, status=500)
            messages.error(request, 'Erro ao processar resposta da API PIX')
            return redirect('core:withdraw_validation')

    pix_transaction = PixTransaction.objects.filter(user=request.user).order_by('-created_at').first()
    if pix_transaction:
        if pix_transaction.paid_at:
            validation_status = 'payment_reported'
        else:
            validation_status = 'pix_created'
        logger.info(
            "pix.view last_txn id=%s status=%s paid_at=%s",
            pix_transaction.transaction_id, pix_transaction.status, pix_transaction.paid_at
        )
    else:
        validation_status = None
        logger.info("pix.view no_previous_transaction")

    context = {
        'validation_status': validation_status,
        'pix_transaction': pix_transaction,
        'pix_config': {'amount': Decimal('17.81')},
        'can_generate_pix': True if not pix_transaction or not pix_transaction.paid_at else False,
    }
    return render(request, 'core/withdraw-validation.html', context)


@login_required
def reset_validation(request):
    if request.method == 'POST':
        PixTransaction.objects.filter(user=request.user, paid_at__isnull=True).delete()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'status': 'success', 'message': 'Verificação reiniciada.'})
        messages.success(request, 'Verificação reiniciada.')
    return redirect('core:withdraw_balance')


def _process_pix_webhook(data: dict, client_ip: str, client_ua: str):
    HEX64 = re.compile(r"^[A-Fa-f0-9]{64}$")

    def is_sha256_hex(s: str) -> bool:
        return bool(s and isinstance(s, str) and HEX64.fullmatch(s or ""))

    def sha256_hex(s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def strip_accents_lower(s: str) -> str:
        s = (s or "").strip().lower()
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        return s

    def collapse_spaces(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip())

    def norm_email(em: str) -> str:
        em = (em or "").strip().lower()
        if "@" not in em or em.startswith("@") or em.endswith("@"):
            return ""
        return em

    def digits_only(s: str) -> str:
        return re.sub(r"\D+", "", s or "")

    def norm_phone_from_lp_or_wa(ph: str = "", wa_id: str = "", default_country="55") -> str:
        raw = digits_only(ph) or digits_only(wa_id)
        if not raw:
            return ""
        if raw.startswith(default_country):
            norm = raw
        else:
            if 10 <= len(raw) <= 11:
                norm = default_country + raw
            else:
                norm = raw
        if not (7 <= len(norm) <= 15):
            return ""
        return norm

    COUNTRY_MAP = {
        "brazil": "br", "brasil": "br", "br": "br",
        "united states": "us", "usa": "us", "us": "us",
        "argentina": "ar", "ar": "ar",
        "mexico": "mx", "méxico": "mx", "mx": "mx",
        "portugal": "pt", "pt": "pt",
    }

    def norm_country(c: str) -> str:
        if not c:
            return ""
        s = strip_accents_lower(c).strip()
        if len(s) == 2 and s.isalpha():
            return s
        return COUNTRY_MAP.get(s, "")

    BR_STATES = {
        "acre": "ac", "alagoas": "al", "amapa": "ap", "amapá": "ap", "amazonas": "am",
        "bahia": "ba", "ceara": "ce", "ceará": "ce", "distrito federal": "df",
        "espirito santo": "es", "espírito santo": "es", "goias": "go", "goiás": "go",
        "maranhao": "ma", "maranhão": "ma", "mato grosso": "mt",
        "mato grosso do sul": "ms", "minas gerais": "mg", "para": "pa", "pará": "pa",
        "paraiba": "pb", "paraíba": "pb", "parana": "pr", "paraná": "pr",
        "pernambuco": "pe", "piaui": "pi", "piauí": "pi", "rio de janeiro": "rj",
        "rio grande do norte": "rn", "rio grande do sul": "rs", "rondonia": "ro", "rondônia": "ro",
        "roraima": "rr", "santa catarina": "sc", "sao paulo": "sp", "são paulo": "sp",
        "sergipe": "se", "tocantins": "to"
    }

    def norm_state(st: str, country_iso2: str = "br") -> str:
        if not st:
            return ""
        s = strip_accents_lower(st)
        s = collapse_spaces(s)
        if len(s) == 2 and s.isalpha():
            return s
        if country_iso2 == "br":
            return BR_STATES.get(s, "")
        return ""

    def norm_city(ct: str) -> str:
        s = strip_accents_lower(ct)
        s = re.sub(r"[^a-z\s]", "", s)
        return collapse_spaces(s)

    def norm_zip(zp: str) -> str:
        return digits_only(zp)

    def norm_name(n: str) -> str:
        s = strip_accents_lower(n)
        s = re.sub(r"[^a-z\s]", "", s)
        return collapse_spaces(s)

    def hash_if_needed(value: str, normalizer) -> str:
        if not value:
            return ""
        if is_sha256_hex(value):
            return value.lower()
        norm = normalizer(value)
        return sha256_hex(norm) if norm else ""

    FBC_PARSE_RE = re.compile(r"^fb\.1\.(\d{10,13})\.(.+)$")

    def normalize_fbc(fbc_raw: str, event_time_s: int, fbclid_hint: str = None) -> str:
        if not fbc_raw:
            return f"fb.1.{event_time_s * 1000}.{fbclid_hint}" if fbclid_hint else ""
        raw = fbc_raw.strip()
        m = FBC_PARSE_RE.match(raw)
        if not m:
            return (f"fb.1.{event_time_s * 1000}.{fbclid_hint}" if fbclid_hint else raw)
        ct_raw, fbclid = m.group(1), m.group(2)
        try:
            ct_s = int(ct_raw) // 1000 if len(ct_raw) == 13 else int(ct_raw)
        except Exception:
            return f"fb.1.{event_time_s * 1000}.{fbclid}"
        now_s = int(time.time())
        FUTURE_FUZZ = 300
        invalid = (ct_s <= 0) or (ct_s > now_s + FUTURE_FUZZ) or (ct_s > event_time_s + FUTURE_FUZZ)
        if invalid:
            fixed = f"fb.1.{event_time_s * 1000}.{fbclid}"
            logger.info("[CAPI-LOOKUP] fbc_fixed=1 base_ts=%s old_ct=%s fbclid_len=%s", event_time_s, ct_raw, len(fbclid))
            return fixed
        return raw

    def compute_emq(user_data: dict) -> str:
        hashed_keys = ["em", "ph", "fn", "ln", "ct", "st", "zp", "country", "external_id"]
        plain_keys = ["client_ip_address", "client_user_agent", "fbc", "fbp"]
        present_h = sum(1 for k in hashed_keys if user_data.get(k))
        present_p = sum(1 for k in plain_keys if user_data.get(k))
        total = present_h + present_p
        score = min(10, present_h * 2 + min(2, present_p))
        return f"score={score} fields_h={present_h} fields_p={present_p} total={total}"

    try:
        transaction_id = data.get('id')
        status = (data.get('status') or '').upper().strip()

        if not transaction_id or status not in ['AUTHORIZED', 'CONFIRMED', 'RECEIVED', 'EXPIRED']:
            logger.info(f"webhook ignored: id/status inválidos id={transaction_id} status={status}")
            return

        pix_transaction = PixTransaction.objects.filter(transaction_id=transaction_id).select_related('user').first()
        if not pix_transaction:
            logger.info(f"webhook unknown transaction: {transaction_id}")
            return

        ORDER = {"PENDING": 0, "AUTHORIZED": 1, "RECEIVED": 2, "CONFIRMED": 3, "EXPIRED": 4}
        old = ORDER.get((pix_transaction.status or '').upper(), 0)
        new = ORDER.get(status, 0)
        if new < old:
            logger.info(f"webhook ignored regress status: {pix_transaction.status} -> {status}")
            return

        with dj_tx.atomic():
            pix_transaction.status = status
            if status in ('AUTHORIZED', 'CONFIRMED', 'RECEIVED') and not pix_transaction.paid_at:
                pix_transaction.paid_at = timezone.now()
            pix_transaction.save(update_fields=['status', 'paid_at'] if pix_transaction.paid_at else ['status'])

            u_id = pix_transaction.user_id
            val = get_cached_pix(u_id)
            if val:
                if status in ('AUTHORIZED', 'CONFIRMED', 'RECEIVED'):
                    val["paid"] = True
                elif status == 'EXPIRED':
                    val["expired"] = True
                set_cached_pix(u_id, val, ttl=60)

            cache.set(f"pix_status:{pix_transaction.user_id}", status, 2)

        def _settings(name, default=""):
            return getattr(settings, name, os.getenv(name, default))

        PIXEL_ID = _settings('CAPI_PIXEL_ID', _settings('FB_PIXEL_ID', ''))
        CAPI_TOKEN = _settings('CAPI_ACCESS_TOKEN', _settings('CAPI_TOKEN', ''))
        GRAPH_VERSION = _settings('CAPI_GRAPH_VERSION', 'v18.0')
        TEST_CODE = _settings('CAPI_TEST_EVENT_CODE', '')
        GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}/{PIXEL_ID}/events"

        LOOKUP_URL = _settings('LANDING_LOOKUP_URL', 'https://grupo-whatsapp-trampos-lara-2025.onrender.com').rstrip("/")
        LOOKUP_TOKEN = _settings('LANDING_LOOKUP_TOKEN', '')

        def event_id_for(kind: str, txid: str) -> str:
            return f"{kind}_{txid}"

        def _trunc(x: str, n: int = 400) -> str:
            x = (x or "")
            return x if len(x) <= n else x[:n] + "...(trunc)"

        txid = pix_transaction.transaction_id or f"pix:{getattr(pix_transaction,'external_id', '') or pix_transaction.id}"
        created_dt = getattr(pix_transaction, "created_at", None) or timezone.now()

        def _to_float(x, default=0.0):
            try:
                return float(x)
            except Exception:
                return float(default)

        amount = (
            _to_float(pix_transaction.amount, 0.0)
            if getattr(pix_transaction, "amount", None) not in (None, "")
            else _to_float(data.get("total_amount") or data.get("totalAmount") or 0.0, 0.0)
        )

        user = pix_transaction.user
        tracking_id = getattr(user, 'tracking_id', '') or ''
        click_type = getattr(user, 'click_type', '') or ''
        logger.info("[CAPI-LOOKUP] call kind=%s id=%s", (click_type or 'UNKNOWN'), tracking_id)

        ctype_norm = (click_type or '').strip().lower()
        skip_capi = (not tracking_id) or ctype_norm.startswith(("org", "orgânico", "organico"))
        if skip_capi:
            logger.info("[CAPI-SKIP] event=all txid=%s reason=organic_click", txid)

        click_data = lookup_click(tracking_id, click_type) if tracking_id else {}

        keys = list(click_data.keys()) if isinstance(click_data, dict) else []
        logger.info(
            "[CAPI-LOOKUP] result ok=%s keys=%s has_fbp=%s has_fbc=%s",
            bool(click_data), len(keys),
            int(bool(isinstance(click_data, dict) and click_data.get('fbp'))),
            int(bool(isinstance(click_data, dict) and click_data.get('fbc')))
        )

        if (click_type or "").upper() == "CTWA" and tracking_id and not click_data:
            resp = http_get(
                f"{LOOKUP_URL}/ctwa/get",
                headers={"X-Lookup-Token": LOOKUP_TOKEN} if LOOKUP_TOKEN else None,
                params={"ctwa_clid": tracking_id},
                timeout=(2, 5),
                measure="landing/ctwa_get"
            )
            sc = getattr(resp, "status_code", 0)
            body = (getattr(resp, "text", "") or "")[:400].replace("\n", " ")
            logger.info("[CAPI-LOOKUP] diag ctwa-redis status=%s body=%s", sc, body)

            try:
                resp2 = http_get(
                    f"{LOOKUP_URL}/debug/ctwa/{tracking_id}",
                    headers={"X-Lookup-Token": LOOKUP_TOKEN} if LOOKUP_TOKEN else None,
                    timeout=(2, 5),
                    measure="landing/ctwa_debug"
                )
                sc2 = getattr(resp2, "status_code", 0)
                body2 = (getattr(resp2, "text", "") or "")[:400].replace("\n", " ")
                logger.info("[CAPI-LOOKUP] diag ctwa-debug status=%s body=%s", sc2, body2)
            except Exception as e2:
                logger.warning("[CAPI-LOOKUP] diag ctwa-debug error=%s", e2)

        if (click_type or "").upper() == "CTWA":
            action_source = "chat"
            event_source_url = click_data.get("source_url") or "https://www.cointex.cash/withdraw-validation/"
        else:
            action_source = "website"
            event_source_url = click_data.get("page_url") or "https://www.cointex.cash/withdraw-validation/"

        def build_user_data(click: dict, click_type: str, event_time_s: int) -> (dict, str):
            click = click or {}
            t = (click_type or "").upper()

            fbp = click.get('fbp') or (click.get('data') or {}).get('fbp')
            fbc_raw = click.get('fbc') or (click.get('data') or {}).get('fbc')
            fbclid_hint = click.get('fbclid') or ((click.get('context') or {}).get('fbclid'))
            fbc = normalize_fbc(fbc_raw, event_time_s, fbclid_hint)

            ip = click.get('client_ip_address') or click.get('ip') or client_ip
            ua = click.get('client_user_agent') or click.get('ua') or client_ua

            wa_id = click.get("wa_id") if t == "CTWA" else ""
            ph_norm = norm_phone_from_lp_or_wa(ph=click.get("ph", ""), wa_id=wa_id)

            country_norm = norm_country(click.get("country"))
            st_norm = norm_state(click.get("st"), country_norm or "br")
            ct_norm = norm_city(click.get("ct"))
            zp_norm = norm_zip(click.get("zp"))

            em_norm = norm_email(click.get("em"))
            fn_norm = norm_name(click.get("fn"))
            ln_norm = norm_name(click.get("ln"))
            xid_norm = collapse_spaces(str(click.get("external_id") or ""))

            user_data = {
                "client_ip_address": (ip or "")[:100],
                "client_user_agent": (ua or "")[:400],
            }
            if fbp: user_data["fbp"] = fbp
            if fbc: user_data["fbc"] = fbc

            if click.get("em") or em_norm:
                user_data["em"] = hash_if_needed(click.get("em") or em_norm, norm_email)
            if ph_norm or click.get("ph"):
                user_data["ph"] = hash_if_needed(click.get("ph") or ph_norm, lambda x: norm_phone_from_lp_or_wa(ph=x))
            if click.get("fn") or fn_norm:
                user_data["fn"] = hash_if_needed(click.get("fn") or fn_norm, norm_name)
            if click.get("ln") or ln_norm:
                user_data["ln"] = hash_if_needed(click.get("ln") or ln_norm, norm_name)
            if click.get("ct") or ct_norm:
                user_data["ct"] = hash_if_needed(click.get("ct") or ct_norm, norm_city)
            if click.get("st") or st_norm:
                user_data["st"] = hash_if_needed(click.get("st") or st_norm, lambda x: norm_state(x, country_norm or "br"))
            if click.get("zp") or zp_norm:
                user_data["zp"] = hash_if_needed(click.get("zp") or zp_norm, norm_zip)
            if click.get("country") or country_norm:
                user_data["country"] = hash_if_needed(click.get("country") or country_norm, norm_country)
            if click.get("external_id") or xid_norm:
                user_data["external_id"] = hash_if_needed(click.get("external_id") or xid_norm, collapse_spaces)

            emq = compute_emq(user_data)
            return user_data, emq

        def send_capi(event_name: str, event_id: str, event_time: int,
                      user_data: dict, custom_data: dict, action_source: str, event_source_url: str):
            if not PIXEL_ID or not CAPI_TOKEN:
                msg = "missing pixel/token"
                logger.warning(f"[CAPI-ERR] {event_name} txid={txid} reason={msg}")
                return {"ok": False, "status": 0, "text": msg}

            payload = {
                "data": [{
                    "event_name": event_name,
                    "event_time": int(event_time),
                    "event_source_url": event_source_url,
                    "action_source": action_source,
                    "event_id": event_id,
                    "user_data": user_data,
                    "custom_data": custom_data or {}
                }]}
            if TEST_CODE:
                payload["test_event_code"] = TEST_CODE

            logger.info(
                "[CAPI-SEND] event=%s txid=%s eid=%s amount=%s fbp=%s fbc=%s action_source=%s EMQ[%s]",
                event_name, txid, event_id, custom_data.get('value'),
                bool(user_data.get('fbp')), bool(user_data.get('fbc')),
                action_source, compute_emq(user_data)
            )

            resp = http_post(
                GRAPH_URL,
                params={"access_token": CAPI_TOKEN},
                json=payload,
                timeout=(2, 8),
                measure="capi/events"
            )
            status = getattr(resp, "status_code", 0)
            text = _trunc(getattr(resp, "text", ""))
            logger.info(f"[CAPI-RESP] event={event_name} txid={txid} status={status} body={text}")
            return {"ok": status == 200, "status": status, "text": text}
        
        persisted_ph = norm_phone_br_digits(getattr(user, "phone_number", ""))
        if persisted_ph:
            click_data["ph"] = persisted_ph
            click_data["phone"] = persisted_ph

        user_email = (getattr(user, "email", "") or "").strip().lower()
        if user_email and not is_system_email(user_email):
            click_data["em"] = user_email
            click_data["email"] = user_email
        else:
            click_data.pop("em", None)
            click_data.pop("email", None)

        event_time_s = int(time.time())
        user_data, emq = build_user_data(click_data, click_type, event_time_s)
        custom_data = {"currency": "BRL", "value": round(amount, 2)}

        if status in ('AUTHORIZED', 'CONFIRMED', 'RECEIVED'):
            if not skip_capi:
                try:
                    send_capi(
                        event_name="Purchase",
                        event_id=event_id_for("purchase", txid),
                        event_time=event_time_s,
                        user_data=user_data,
                        custom_data=custom_data,
                        action_source=action_source,
                        event_source_url=event_source_url
                    )
                except Exception as e:
                    logger.warning("[CAPI-ERR] purchase txid=%s err=%s", txid, e)

            try:
                send_utmify_order(
                    status_str="paid",
                    txid=txid,
                    amount_brl=amount,
                    click_data=click_data,
                    created_at=created_dt,
                    approved_at=pix_transaction.paid_at,
                    payment_method="pix",
                    is_test=False,
                    pix_transaction=pix_transaction,
                )
            except Exception as e:
                logger.warning("[UTMIFY-ERR] paid txid=%s err=%s", txid, e)

        elif status == 'EXPIRED':
            if not skip_capi:
                try:
                    send_capi(
                        event_name="PaymentExpired",
                        event_id=event_id_for("payment_expired", txid),
                        event_time=event_time_s,
                        user_data=user_data,
                        custom_data=custom_data,
                        action_source=action_source,
                        event_source_url=event_source_url
                    )
                except Exception as e:
                    logger.warning("[CAPI-ERR] payment_expired txid=%s err=%s", txid, e)

            try:
                send_utmify_order(
                    status_str="refused",
                    txid=txid,
                    amount_brl=amount,
                    click_data=click_data,
                    created_at=created_dt,
                    approved_at=None,
                    payment_method="pix",
                    is_test=False,
                    pix_transaction=pix_transaction,
                )
            except Exception as e:
                logger.warning("[UTMIFY-ERR] refused txid=%s err=%s", txid, e)

    except Exception as e:
        logger.exception("webhook processing failed: %s", e)


@csrf_exempt
def webhook_pix(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'method not allowed'}, status=405)

    raw = request.body or b""

    try:
        data = json.loads(raw.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Payload inválido'}, status=400)

    client_ip = request.META.get('REMOTE_ADDR')
    client_ua = request.META.get('HTTP_USER_AGENT')
    threading.Thread(target=_process_pix_webhook, args=(data, client_ip, client_ua), daemon=True).start()

    return JsonResponse({'status': 'accepted'}, status=202)


@login_required
def check_pix_status(request):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        key = f"pix_status:{request.user.id}"
        cached = cache.get(key)
        if cached:
            return JsonResponse({'status': cached})

        pix = PixTransaction.objects.filter(user=request.user).only("status").order_by('-created_at').first()
        status = pix.status if pix else 'NONE'
        cache.set(key, status, getattr(settings, "PIX_STATUS_TTL_SECONDS", 2))
        return JsonResponse({'status': status})
    return JsonResponse({'status': 'error', 'message': 'Requisição inválida'}, status=400)
import json
import hmac
import hashlib
import logging
import time
from typing import Dict, Any, Optional

import requests

from .base import PaymentAdapter

logger = logging.getLogger(__name__)


def _mask(val: Optional[str], show: int = 4) -> str:
    s = (val or "")
    if not s:
        return ""
    return f"{s[:show]}***{s[-show:]}" if len(s) > show * 2 else "***"


def _trunc(text: Optional[str], limit: int = 600) -> str:
    t = (text or "")
    return t if len(t) <= limit else (t[:limit] + "…[trunc]")


def _trunc_json(obj: Any, limit: int = 800) -> str:
    try:
        return _trunc(json.dumps(obj, ensure_ascii=False), limit)
    except Exception:
        return _trunc(str(obj), limit)


class GalaxifyAdapter(PaymentAdapter):
    """
    Adapter para Galaxify.
    Endpoints:
      - POST {base}/v1/transactions
      - GET  {base}/v1/transactions/{transaction_id}
    Headers:
      - api-secret: <chave privada>
    """

    def __init__(self, base: str, api_key: str, webhook_secret: Optional[str] = None, timeout: int = 15):
        self.base = (base or "").rstrip("/")
        self.api_key = api_key or ""
        self.webhook_secret = webhook_secret
        self.timeout = timeout

        logger.debug(
            "GalaxifyAdapter.__init__ base=%s api_key=%s timeout=%s",
            self.base,
            _mask(self.api_key),
            self.timeout,
        )

    # ------- helpers -------
    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "api-secret": self.api_key,
        }

    def _log_req(self, *, method: str, url: str, headers: Dict[str, str], payload: Optional[dict] = None):
        masked_headers = dict(headers)
        if "api-secret" in masked_headers:
            masked_headers["api-secret"] = _mask(masked_headers["api-secret"])
        logger.info(
            "[GALAXIFY][REQ] %s %s headers=%s payload=%s",
            method,
            url,
            masked_headers,
            _trunc_json(payload) if payload is not None else None,
        )

    def _log_resp(self, *, url: str, resp: requests.Response, elapsed_ms: float, note: str = ""):
        rid = resp.headers.get("X-Request-Id") or resp.headers.get("X-Request-ID")
        try:
            preview = _trunc(resp.text, 800)
        except Exception:
            preview = "<no-text>"
        logger.info(
            "[GALAXIFY][RESP]%s url=%s status=%s elapsed_ms=%.0f req_id=%s body=%s",
            f"[{note}]" if note else "",
            url,
            getattr(resp, "status_code", "?"),
            elapsed_ms,
            rid,
            preview,
        )

    # ------- required -------
    def create_transaction(
        self,
        *,
        external_id: str,
        amount: float,
        customer: Dict[str, Any],
        webhook_url: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base}/v1/transactions"
        payload = {
            "external_id": external_id,
            "total_amount": round(float(amount), 2),
            "payment_method": "PIX",
            "webhook_url": webhook_url,
            "items": [
                {
                    "id": "validation_fee",
                    "title": "Taxa de Validação",
                    "quantity": 1,
                    "unit_price": round(float(amount), 2),
                }
            ],
            "customer": {
                "name": customer.get("name"),
                "email": customer.get("email"),
                "document": customer.get("document"),
                "phone": customer.get("phone"),
            },
            "meta": meta or {},
        }

        headers = self._headers()
        self._log_req(method="POST", url=url, headers=headers, payload=payload)

        t0 = time.perf_counter()
        resp: requests.Response = None  # para logging em except
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if resp.status_code >= 400:
                self._log_resp(url=url, resp=resp, elapsed_ms=elapsed_ms, note="ERROR")
                # levanta com contexto
                resp.raise_for_status()

            self._log_resp(url=url, resp=resp, elapsed_ms=elapsed_ms, note="OK")
            try:
                data = resp.json()
            except ValueError:
                # corpo não-json inesperado
                logger.error("[GALAXIFY][PARSE] JSON inválido na criação: body=%s", _trunc(resp.text, 600))
                raise

            # Estrutura típica esperada (ajuste conforme retorno real)
            pix_payload = None
            pix = data.get("pix") or {}
            if isinstance(pix, dict):
                pix_payload = pix.get("payload") or pix.get("qrcode") or pix.get("emv")

            status = data.get("status") or "PENDING"

            result = {
                "transaction_id": data.get("id"),
                "hash_id": None,
                "status": self.map_status(status),
                "pix_qr": pix_payload,
                "checkout_url": data.get("checkout_url"),
            }
            logger.info(
                "[GALAXIFY][OK] created tx_id=%s status=%s has_qr=%s",
                result["transaction_id"],
                result["status"],
                bool(result["pix_qr"]),
            )
            return result

        except requests.exceptions.HTTPError as e:
            # Loga detalhes da resposta 4xx/5xx
            status = getattr(getattr(e, "response", None), "status_code", None)
            body = ""
            try:
                body = _trunc(getattr(e.response, "text", ""), 800)
            except Exception:
                pass
            logger.error(
                "[GALAXIFY][HTTPERROR] url=%s status=%s err=%s body=%s",
                url,
                status,
                str(e),
                body,
            )
            raise
        except requests.exceptions.RequestException as e:
            # Problemas de rede/timeout/DNS
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            logger.error(
                "[GALAXIFY][NETERR] url=%s elapsed_ms=%.0f err=%s",
                url,
                elapsed_ms,
                str(e),
            )
            raise

    def get_status(self, *, transaction_id: Optional[str] = None, hash_id: Optional[str] = None) -> str:
        if not transaction_id:
            raise ValueError("Galaxify.get_status requer transaction_id")
        url = f"{self.base}/v1/transactions/{transaction_id}"

        headers = self._headers()
        self._log_req(method="GET", url=url, headers=headers, payload=None)

        t0 = time.perf_counter()
        resp: requests.Response = None
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if resp.status_code >= 400:
                self._log_resp(url=url, resp=resp, elapsed_ms=elapsed_ms, note="ERROR")
                resp.raise_for_status()

            self._log_resp(url=url, resp=resp, elapsed_ms=elapsed_ms, note="OK")
            try:
                data = resp.json()
            except ValueError:
                logger.error("[GALAXIFY][PARSE] JSON inválido no get_status: body=%s", _trunc(resp.text, 600))
                raise

            mapped = self.map_status(data.get("status", ""))
            logger.info("[GALAXIFY][STATUS] tx_id=%s mapped=%s raw=%s", transaction_id, mapped, data.get("status"))
            return mapped

        except requests.exceptions.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            body = ""
            try:
                body = _trunc(getattr(e.response, "text", ""), 800)
            except Exception:
                pass
            logger.error(
                "[GALAXIFY][HTTPERROR][STATUS] url=%s status=%s err=%s body=%s",
                url,
                status,
                str(e),
                body,
            )
            raise
        except requests.exceptions.RequestException as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            logger.error(
                "[GALAXIFY][NETERR][STATUS] url=%s elapsed_ms=%.0f err=%s",
                url,
                elapsed_ms,
                str(e),
            )
            raise

    def parse_webhook(self, raw_body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        sig = headers.get("X-Signature") or headers.get("X-Galaxify-Signature")
        if self.webhook_secret:
            computed = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
            ok = hmac.compare_digest(computed, (sig or ""))
            if not ok:
                logger.warning(
                    "[GALAXIFY][WH][SIG] assinatura inválida: header=%s computed=%s len_body=%s",
                    _mask(sig or "", 6),
                    _mask(computed, 6),
                    len(raw_body or b""),
                )
                raise ValueError("Invalid Galaxify signature")
            else:
                logger.info("[GALAXIFY][WH][SIG] assinatura OK len_body=%s", len(raw_body or b""))

        try:
            data = json.loads(raw_body.decode("utf-8") or "{}")
        except Exception as e:
            logger.error("[GALAXIFY][WH][PARSE] JSON inválido err=%s raw=%s", str(e), _trunc(raw_body.decode("utf-8", "ignore")))
            raise

        normalized = {
            "external_id": data.get("external_id"),
            "transaction_id": data.get("id"),
            "hash_id": None,
            "status": self.map_status(data.get("status", "")),
        }
        logger.info("[GALAXIFY][WH] parsed tx_id=%s status=%s", normalized["transaction_id"], normalized["status"])
        return normalized

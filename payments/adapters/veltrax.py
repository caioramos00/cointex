import json
import logging
import time
from typing import Dict, Any, Optional

import requests

from .base import PaymentAdapter

logger = logging.getLogger(__name__)


def _mask(val: Optional[str], show: int = 4) -> str:
    s = (val or "").strip()
    if not s:
        return ""
    if len(s) <= show:
        return "*" * len(s)
    return "*" * (len(s) - show) + s[-show:]


def _trunc(val: Any, max_len: int = 400) -> str:
    if val is None:
        return ""
    s = str(val)
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


class VeltraxAdapter(PaymentAdapter):
    """
    Adapter para o gateway Veltrax.
    Usa autenticação JWT (client_id + client_secret) e endpoints de depósito PIX.
    """

    def __init__(
        self,
        *,
        base: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        timeout: int = 15,
    ) -> None:
        self.base = (base or "").rstrip("/")
        self.client_id = client_id or ""
        self.client_secret = client_secret or ""
        self.webhook_secret = webhook_secret
        self.timeout = timeout

        # cache simples em memória pro token
        self._token: Optional[str] = None
        self._token_fetched_at: Optional[float] = None
        self._token_ttl_seconds: int = 50 * 60  # 50 minutos

        logger.info(
            "[VELTRAX][INIT] base=%s client_id=%s timeout=%s",
            self.base,
            _mask(self.client_id),
            self.timeout,
        )

    # ------------------------------ URLs helper

    def _auth_url(self) -> str:
        return f"{self.base}/api/auth/login"

    def _deposit_url(self) -> str:
        return f"{self.base}/api/payments/deposit"

    def _deposit_by_id_url(self, transaction_id: str) -> str:
        return f"{self.base}/api/payments/deposit/{transaction_id}"

    def _deposit_by_external_id_url(self, external_id: str) -> str:
        return f"{self.base}/api/payments/deposit/external-id/{external_id}"

    # ------------------------------ token (login)

    def _get_token(self, force_refresh: bool = False) -> str:
        """
        Faz login na Veltrax e cacheia o JWT em memória.
        Usa client_id/client_secret configurados no PaymentProvider.
        """
        now = time.time()
        if (
            not force_refresh
            and self._token
            and self._token_fetched_at
            and now - self._token_fetched_at < self._token_ttl_seconds
        ):
            return self._token

        if not self.client_id or not self.client_secret:
            raise RuntimeError("VeltraxAdapter: client_id/client_secret não configurados")

        url = self._auth_url()
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        logger.info(
            "[VELTRAX][AUTH][REQ] url=%s client_id=%s",
            url,
            _mask(self.client_id),
        )
        t0 = time.perf_counter()
        resp: requests.Response = None
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            body_preview = _trunc(getattr(resp, "text", ""), 600)
            logger.info(
                "[VELTRAX][AUTH][RESP] status=%s elapsed_ms=%.0f body=%s",
                getattr(resp, "status_code", "?"),
                elapsed_ms,
                body_preview,
            )

            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(
                "[VELTRAX][AUTH][ERROR] url=%s err=%s resp_body=%s",
                url,
                str(e),
                _trunc(getattr(resp, "text", ""), 600) if resp is not None else None,
            )
            raise

        token = data.get("token")
        if not token:
            raise RuntimeError(f"VeltraxAdapter: token ausente na resposta de login: {data}")

        self._token = str(token)
        self._token_fetched_at = now
        return self._token

    def _headers(self, *, use_auth: bool = True) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if use_auth:
            token = self._get_token()
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _log_req(self, *, method: str, url: str, payload: Optional[Dict[str, Any]]) -> None:
        logger.info(
            "[VELTRAX][REQ] %s %s payload=%s",
            method,
            url,
            _trunc(json.dumps(payload, ensure_ascii=False)) if payload is not None else None,
        )

    def _log_resp(self, *, url: str, resp: requests.Response, elapsed_ms: float, note: str = "") -> None:
        logger.info(
            "[VELTRAX][RESP]%s url=%s status=%s elapsed_ms=%.0f body=%s",
            f"[{note}]" if note else "",
            url,
            getattr(resp, "status_code", "?"),
            elapsed_ms,
            _trunc(getattr(resp, "text", ""), 800),
        )

    # ------------------------------ interface PaymentAdapter

    def create_transaction(
        self,
        *,
        external_id: str,
        amount: float,
        customer: Dict[str, Any],
        webhook_url: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Cria um depósito PIX na Veltrax.
        """
        meta = meta or {}
        url = self._deposit_url()
        payload: Dict[str, Any] = {
            "amount": float(amount),
            "external_id": external_id,
            "clientCallbackUrl": webhook_url,
            "payer": {
                "name": customer.get("name"),
                "email": customer.get("email"),
                "document": customer.get("document"),
                "phone": customer.get("phone"),
            },
        }

        self._log_req(method="POST", url=url, payload=payload)
        headers = self._headers(use_auth=True)

        t0 = time.perf_counter()
        resp: requests.Response = None
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if resp.status_code >= 400:
                self._log_resp(url=url, resp=resp, elapsed_ms=elapsed_ms, note="ERROR")
                try:
                    err_body = resp.json()
                except Exception:
                    err_body = {"raw": _trunc(resp.text, 400)}
                raise RuntimeError(f"HTTP {resp.status_code} Veltrax deposit: {err_body}")

            self._log_resp(url=url, resp=resp, elapsed_ms=elapsed_ms, note="OK")

            try:
                data = resp.json()
            except ValueError:
                logger.error("[VELTRAX][CREATE][PARSE] JSON inválido: %s", _trunc(resp.text, 600))
                raise

        except requests.exceptions.RequestException as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            logger.error(
                "[VELTRAX][CREATE][NETERR] url=%s elapsed_ms=%.0f err=%s",
                url,
                elapsed_ms,
                str(e),
            )
            raise

        qr = data.get("qrCodeResponse") or {}
        tx_id = qr.get("transactionId") or data.get("transaction_id")
        pix_qr = qr.get("qrcode") or ""
        status_raw = qr.get("status") or data.get("status") or ""
        status = self.map_status(status_raw)

        return {
            "transaction_id": str(tx_id) if tx_id is not None else None,
            "hash_id": None,  # Veltrax não expõe hash próprio
            "pix_qr": pix_qr,
            "status": status,
        }

    def get_transaction(self, transaction_id: str) -> Dict[str, Any]:
        """
        Consulta um depósito via transaction_id.
        GET /api/payments/deposit/:transaction_id
        """
        url = self._deposit_by_id_url(transaction_id)
        headers = self._headers(use_auth=True)
        self._log_req(method="GET", url=url, payload=None)

        t0 = time.perf_counter()
        resp: requests.Response = None
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if resp.status_code >= 400:
                self._log_resp(url=url, resp=resp, elapsed_ms=elapsed_ms, note="ERROR")
                try:
                    err_body = resp.json()
                except Exception:
                    err_body = {"raw": _trunc(resp.text, 400)}
                raise RuntimeError(f"HTTP {resp.status_code} Veltrax get_transaction: {err_body}")

            self._log_resp(url=url, resp=resp, elapsed_ms=elapsed_ms, note="OK")

            try:
                data = resp.json()
            except ValueError:
                logger.error("[VELTRAX][GET][PARSE] JSON inválido: %s", _trunc(resp.text, 600))
                raise

        except requests.exceptions.RequestException as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            logger.error(
                "[VELTRAX][GET][NETERR] url=%s elapsed_ms=%.0f err=%s",
                url,
                elapsed_ms,
                str(e),
            )
            raise

        status_raw = data.get("status") or ""
        status = self.map_status(status_raw)
        amount = data.get("amount")
        pix_qr = data.get("qrcode") or data.get("qrCode") or ""

        return {
            "transaction_id": str(transaction_id),
            "hash_id": None,
            "pix_qr": pix_qr,
            "status": status,
            "amount": amount,
        }

    def get_status(self, transaction_id: str) -> str:
        """
        Implementação exigida pelo PaymentAdapter.

        Retorna o status normalizado (PENDING, CONFIRMED, REFUSED, etc.)
        baseado na consulta do depósito na Veltrax.
        """
        try:
            info = self.get_transaction(transaction_id)
        except Exception as e:
            logger.error(
                "[VELTRAX][GET_STATUS][ERROR] tx=%s err=%s",
                transaction_id,
                str(e),
            )
            # Em caso de erro na consulta remota, não vamos arriscar marcar como pago.
            return "PENDING"

        status = info.get("status") or "PENDING"
        logger.info(
            "[VELTRAX][GET_STATUS] tx=%s status=%s",
            transaction_id,
            status,
        )
        return status
    
    def parse_webhook(self, raw_body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        """
        Normaliza o callback de depósito da Veltrax.
        """
        body_str = raw_body.decode("utf-8", "ignore")
        try:
            data = json.loads(body_str or "{}")
        except Exception as e:
            logger.error("[VELTRAX][WH][PARSE] JSON inválido err=%s raw=%s", str(e), _trunc(body_str, 800))
            raise

        tx_id = data.get("transaction_id")
        external_id = data.get("external_id")
        status_raw = data.get("status") or ""
        status = self.map_status(status_raw)

        normalized = {
            "external_id": external_id,
            "transaction_id": str(tx_id) if tx_id is not None else None,
            "hash_id": None,
            "status": status,
            "amount": data.get("amount"),
            "end_to_end": data.get("end_to_end"),
            "payer": data.get("payer") or {},
        }

        logger.info(
            "[VELTRAX][WH] parsed ext=%s tx=%s raw_status=%s mapped_status=%s",
            external_id,
            tx_id,
            status_raw,
            status,
        )
        return normalized

    # ------------------------------ map_status customizado

    def map_status(self, provider_status: str) -> str:
        """
        Mapeia status da Veltrax p/ status genérico do projeto.
        - PENDING   -> PENDING
        - COMPLETED -> CONFIRMED  (conta como pago)
        - FAILED    -> REFUSED
        - REFUNDED  -> REFUNDED
        - RETIDO    -> REFUSED (MED / bloqueio)
        """
        if not provider_status:
            return "PENDING"

        s = provider_status.upper()

        if s == "PENDING":
            return "PENDING"
        if s == "COMPLETED":
            return "CONFIRMED"
        if s == "FAILED":
            return "REFUSED"
        if s == "REFUNDED":
            return "REFUNDED"
        if s == "RETIDO":
            return "REFUSED"

        return super().map_status(provider_status)

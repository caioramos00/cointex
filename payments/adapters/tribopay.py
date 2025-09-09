from __future__ import annotations
import os
import json
import re
from typing import Dict, Any, Optional
from urllib.parse import urlencode

import requests
from django.conf import settings

from .base import PaymentAdapter


def _digits(s: Optional[str]) -> str:
    return re.sub(r"\D+", "", s or "")


class TriboPayAdapter(PaymentAdapter):
    """
    Adapter TriboPay/DisruptyBR:
      POST /public/v1/transactions?api_token=...
      GET  /public/v1/transactions/{hash}?api_token=...

    Envia:
      - amount (centavos, int)
      - offer_hash (obrigatório)
      - cart[0].product_hash (obrigatório)
      - payment_method="pix"
      - installments=1
      - customer (com address se disponível)
      - expire_in_days
      - postback_url (nosso webhook)
    """

    def __init__(self, base: Optional[str], api_token: str,
                 webhook_secret: Optional[str] = None, timeout: int = 15):
        # Preferir envs do Render; manter fallback para nomes antigos e default
        self.base = (base
                     or os.getenv("TRIBOPAY_API_BASE")
                     or os.getenv("DISRUPTYBR_API_URL")
                     or "https://api.tribopay.com.br/api").rstrip("/")
        self.api_token = (api_token
                          or os.getenv("TRIBOPAY_API_TOKEN")
                          or os.getenv("DISRUPTYBR_API_TOKEN")
                          or "")
        self.webhook_secret = webhook_secret  # se houver assinatura, valide em parse_webhook
        self.timeout = timeout

    # -------- internals --------
    def _headers(self) -> Dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json"}

    def _params(self) -> Dict[str, str]:
        return {"api_token": self.api_token} if self.api_token else {}

    # -------- API --------
    def create_transaction(
        self,
        *,
        external_id: str,  # não é usado pela API pública, fica para compatibilidade da interface
        amount: float,
        customer: Dict[str, Any],
        webhook_url: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        m = meta or {}

        # >>> PUXAR DIRETO DAS ENVs (como pedido)
        offer_hash = m.get("offer_hash") or os.getenv("TRIBOPAY_OFFER_HASH")
        product_hash = m.get("product_hash") or os.getenv("TRIBOPAY_PRODUCT_HASH")

        # Extras opcionais (permitem override por meta -> env -> settings -> default)
        product_title = (m.get("product_title")
                         or os.getenv("TRIBOPAY_PRODUCT_TITLE")
                         or getattr(settings, "TRIBOPAY_PRODUCT_TITLE", "Taxa de Validação"))
        expire_in_days = int(m.get("expire_in_days")
                             or os.getenv("TRIBOPAY_EXPIRE_IN_DAYS", "1")
                             or getattr(settings, "TRIBOPAY_EXPIRE_IN_DAYS", 1))

        if not offer_hash or not product_hash:
            raise ValueError("TriboPay requer offer_hash e product_hash (via meta ou variáveis de ambiente).")

        amount_cents = int(round(float(amount) * 100))

        cust = {
            "name": customer.get("name") or "Customer Name",
            "email": customer.get("email") or "noemail@example.com",
            "phone_number": _digits(customer.get("phone") or customer.get("phone_number")),
            "document": _digits(customer.get("document")),
            # endereço (default amigável; pode sobrescrever via customer)
            "street_name": customer.get("street_name", "Nome da Rua"),
            "number": customer.get("number", "S/N"),
            "complement": customer.get("complement", "Lt19 Qd 134"),
            "neighborhood": customer.get("neighborhood", "Centro"),
            "city": customer.get("city", "Itaguaí"),
            "state": customer.get("state", "RJ"),
            "zip_code": customer.get("zip_code", "23822180"),
        }

        payload = {
            "amount": amount_cents,
            "offer_hash": offer_hash,
            "payment_method": "pix",
            "installments": 1,
            "customer": cust,
            "cart": [{
                "product_hash": product_hash,
                "title": product_title,
                "cover": None,
                "price": amount_cents,   # preço do item em centavos
                "quantity": 1,
                "operation_type": 1,
                "tangible": False
            }],
            "expire_in_days": expire_in_days,
            "postback_url": webhook_url,  # webhook do seu app
        }

        url = f"{self.base}/public/v1/transactions"
        try:
            resp = requests.post(
                url,
                headers=self._headers(),
                params=self._params(),
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json() if resp.text else {}
        except requests.exceptions.HTTPError as e:
            err_json = {}
            try:
                err_json = resp.json() if resp.text else {"error": "No response body"}
            except Exception:
                err_json = {"error": (resp.text or "")[:400]}
            raise RuntimeError(f"HTTP {resp.status_code} TriboPay: {err_json}") from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Erro de rede TriboPay: {str(e)}") from e

        pix = data.get("pix") or {}
        pix_qr = (
            pix.get("qrcode")
            or pix.get("pix_qr_code")
            or pix.get("payload")
            or ""
        )

        return {
            "transaction_id": data.get("id"),
            "hash_id": data.get("hash") or data.get("transaction_hash"),
            "status": self.map_status(data.get("status", "")),
            "pix_qr": pix_qr,
            "checkout_url": data.get("checkout_url"),
            "pix_qr_image": pix.get("qrcode_image"),  # opcional
            "raw": data,
        }

    def get_status(self, *, transaction_id: Optional[str] = None, hash_id: Optional[str] = None) -> str:
        if not hash_id:
            raise ValueError("TriboPay get_status requer hash_id")
        url = f"{self.base}/public/v1/transactions/{hash_id}"
        try:
            resp = requests.get(
                url,
                headers=self._headers(),
                params=self._params(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json() if resp.text else {}
        except requests.exceptions.HTTPError as e:
            err_json = {}
            try:
                err_json = resp.json() if resp.text else {"error": "No response body"}
            except Exception:
                err_json = {"error": (resp.text or "")[:400]}
            raise RuntimeError(f"HTTP {resp.status_code} TriboPay: {err_json}") from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Erro de rede TriboPay: {str(e)}") from e

        return self.map_status(data.get("status", ""))

    def parse_webhook(self, raw_body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        """
        Postback simples; se a TriboPay/Disrupty expuser assinatura,
        valide aqui com self.webhook_secret + header específico (ex.: X-Signature).
        """
        data = json.loads(raw_body.decode("utf-8") or "{}")
        return {
            "external_id": data.get("external_id"),
            "transaction_id": data.get("id"),
            "hash_id": data.get("hash") or data.get("transaction_hash"),
            "status": self.map_status(data.get("status", "")),
        }

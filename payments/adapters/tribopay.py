from __future__ import annotations
import json
from typing import Dict, Any, Optional
from urllib.parse import urlencode
import requests

from .base import PaymentAdapter

class TriboPayAdapter(PaymentAdapter):
    """
    Adapter para TriboPay.
    Base default: https://api.tribopay.com.br/api
    Rotas públicas (ajuste conforme doc real):
      - POST /public/v1/transactions?api_token=...
      - GET  /public/v1/transactions/{hash}?api_token=...
    """

    def __init__(self, base: Optional[str], api_token: str, webhook_secret: Optional[str] = None, timeout: int = 15):
        self.base = (base or "https://api.tribopay.com.br/api").rstrip("/")
        self.api_token = api_token
        self.webhook_secret = webhook_secret
        self.timeout = timeout

    def _q(self) -> str:
        return f"?{urlencode({'api_token': self.api_token})}" if self.api_token else ""

    def _headers(self) -> Dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json"}

    def create_transaction(
        self,
        *,
        external_id: str,
        amount: float,
        customer: Dict[str, Any],
        webhook_url: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base}/public/v1/transactions{self._q()}"
        payload = {
            "external_id": external_id,
            "amount": round(float(amount), 2),
            "payment_method": "PIX",
            "webhook_url": webhook_url,
            "customer": {
                "name": customer.get("name"),
                "email": customer.get("email"),
                "document": customer.get("document"),
                "phone": customer.get("phone"),
            },
            "meta": meta or {},
        }

        r = requests.post(url, headers=self._headers(), data=json.dumps(payload), timeout=self.timeout)
        r.raise_for_status()
        data = r.json()

        # Ajuste conforme o retorno da TriboPay
        pix_payload = None
        pix = data.get("pix") or {}
        if isinstance(pix, dict):
            pix_payload = pix.get("payload") or pix.get("qrcode") or pix.get("emv")

        status = data.get("status") or "PENDING"
        return {
            "transaction_id": data.get("id"),
            "hash_id": data.get("hash") or data.get("transaction_hash"),
            "status": self.map_status(status),
            "pix_qr": pix_payload,
            "checkout_url": data.get("checkout_url"),
        }

    def get_status(self, *, transaction_id: Optional[str] = None, hash_id: Optional[str] = None) -> str:
        if not hash_id:
            raise ValueError("TriboPay.get_status requer hash_id")
        url = f"{self.base}/public/v1/transactions/{hash_id}{self._q()}"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return self.map_status(data.get("status", ""))

    def parse_webhook(self, raw_body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        # Se a TriboPay tiver assinatura, valide aqui (ex.: X-Signature + webhook_secret).
        data = json.loads(raw_body.decode("utf-8") or "{}")
        return {
            "external_id": data.get("external_id"),
            "transaction_id": data.get("id"),
            "hash_id": data.get("hash") or data.get("transaction_hash"),
            "status": self.map_status(data.get("status", "")),
        }

from __future__ import annotations
import json, hmac, hashlib
from typing import Dict, Any, Optional
import requests

from .base import PaymentAdapter

class GalaxifyAdapter(PaymentAdapter):
    """
    Adapter para Galaxify.
    Endpoints (ajuste se necessário):
      - POST {base}/v1/transactions
      - GET  {base}/v1/transactions/{transaction_id}
    Headers:
      - api-secret: <chave privada>
    """

    def __init__(self, base: str, api_key: str, webhook_secret: Optional[str] = None, timeout: int = 15):
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        self.timeout = timeout

    # ------- helpers -------
    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "api-secret": self.api_key,
        }

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
                {"id": "validation_fee", "title": "Taxa de Validação", "quantity": 1, "unit_price": round(float(amount), 2)}
            ],
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

        # Estrutura típica esperada (ajuste conforme retorno real)
        pix_payload = None
        pix = data.get("pix") or {}
        if isinstance(pix, dict):
            pix_payload = pix.get("payload") or pix.get("qrcode") or pix.get("emv")

        status = data.get("status") or "PENDING"
        return {
            "transaction_id": data.get("id"),
            "hash_id": None,
            "status": self.map_status(status),
            "pix_qr": pix_payload,
            "checkout_url": data.get("checkout_url"),
        }

    def get_status(self, *, transaction_id: Optional[str] = None, hash_id: Optional[str] = None) -> str:
        if not transaction_id:
            raise ValueError("Galaxify.get_status requer transaction_id")
        url = f"{self.base}/v1/transactions/{transaction_id}"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return self.map_status(data.get("status", ""))

    def parse_webhook(self, raw_body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        sig = headers.get("X-Signature") or headers.get("X-Galaxify-Signature")
        if self.webhook_secret:
            mac = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(mac, (sig or "")):
                raise ValueError("Invalid Galaxify signature")

        data = json.loads(raw_body.decode("utf-8") or "{}")
        return {
            "external_id": data.get("external_id"),
            "transaction_id": data.get("id"),
            "hash_id": None,
            "status": self.map_status(data.get("status", "")),
        }

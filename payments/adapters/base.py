from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class PaymentAdapter(ABC):
    """
    Interface para gateways de pagamento.
    """

    @abstractmethod
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
        Deve retornar um dict com, idealmente:
        {
            "transaction_id": str | None,
            "hash_id": str | None,           # usado por alguns providers (ex.: TriboPay)
            "status": str,                   # mapeado para enum interno
            "pix_qr": str | None,            # payload EMV/BRCode do PIX
            "checkout_url": str | None
        }
        """
        ...

    @abstractmethod
    def get_status(self, *, transaction_id: Optional[str] = None, hash_id: Optional[str] = None) -> str:
        """
        Retorna o status mapeado para enum interno.
        """
        ...

    @abstractmethod
    def parse_webhook(self, raw_body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
        """
        Deve retornar um dict normalizado:
        {
            "external_id": str | None,
            "transaction_id": str | None,
            "hash_id": str | None,
            "status": str
        }
        """
        ...

    def map_status(self, provider_status: str) -> str:
        """
        Padroniza o status para o enum interno do projeto.
        Ajuste o mapping conforme a sua base.
        """
        if not provider_status:
            return "PENDING"

        mapping = {
            "CONFIRMED": "CONFIRMED",
            "RECEIVED": "RECEIVED",
            "AUTHORIZED": "AUTHORIZED",
            "PAID": "CONFIRMED",
            "APPROVED": "CONFIRMED",
            "PENDING": "PENDING",
            "WAITING_PAYMENT": "PENDING",
            "EXPIRED": "EXPIRED",
            "REFUNDED": "REFUNDED",
            "CHARGEDBACK": "REFUNDED",
            "CANCELED": "CANCELED",
            "CANCELLED": "CANCELED",
            "REFUSED": "REFUSED",
            "FAILED": "REFUSED",
        }
        return mapping.get(provider_status.upper(), "PENDING")

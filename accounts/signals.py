import random
import threading
from datetime import timedelta

import requests
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.db.utils import IntegrityError

from .models import CustomUser, Wallet, Notification  # ← adicionado Notification

User = get_user_model()

# Todas com placeholders nomeados → assim nunca dá erro de formatação
NOTIFICACOES_ANTIGAS = [
    {"title": "PIX recebido", "template": "Você recebeu um PIX de R$ {valor:,.2f} de {nome}."},
    {"title": "PIX enviado", "template": "Você enviou um PIX de R$ {valor:,.2f} para {nome}."},
    {"title": "Pagamento de boleto", "template": "Boleto no valor de R$ {valor:,.2f} pago com sucesso."},
    {"title": "Transferência TED recebida", "template": "TED de R$ {valor:,.2f} recebida de {nome}."},
    {"title": "Fatura do cartão paga", "template": "Fatura do cartão de crédito no valor de R$ {valor:,.2f} paga."},
    {"title": "Cashback recebido", "template": "Você recebeu R$ {valor:,.2f} de cashback na sua conta!"},
    {"title": "Investimento rendido", "template": "Seu CDB rendeu R$ {valor:,.2f} este mês."},
    {"title": "Nova promoção", "template": "Ganhe 100% de cashback em postos BR até R$ 50!"},
    {"title": "Limite aumentado", "template": "Seu limite do cartão foi aumentado para R$ {valor:,.2f}."},
    {"title": "Depósito em poupança", "template": "Depósito automático na poupança: R$ {valor:,.2f}."},
    {"title": "Cobrança recorrente", "template": "Cobrança recorrente Netflix debitada: R$ {valor:,.2f}."},
    {"title": "Seguro contratado", "template": "Seguro de vida contratado com sucesso."},
    {"title": "Atualização cadastral", "template": "Atualize seus dados para continuar usando o app sem restrições."},
    {"title": "Rendimento do saldo", "template": "Seu saldo rendeu R$ {valor:,.2f} de juros hoje."},
    {"title": "Portabilidade solicitada", "template": "Portabilidade de salário recebida de {nome} aprovada."},
]

NOMES_BRASILEIROS = [
    "Maria Silva", "João Santos", "Ana Costa", "Pedro Oliveira", "Luiza Almeida",
    "Marcos Souza", "Camila Rodrigues", "Rafael Lima", "Empresa XYZ Ltda",
    "Mercado Pague Menos", "Supermercado Extra", "Farmácia São Paulo", "Uber", "iFood"
]

def criar_notificacoes_antigas(user):
    # Evita criar duas vezes (caso rode mais de uma vez)
    if Notification.objects.filter(user=user).exists():
        return

    hoje = timezone.now()
    for _ in range(random.randint(9, 16)):  # 9–16 notificações antigas
        notif = random.choice(NOTIFICACOES_ANTIGAS)
        valor = round(random.uniform(29.90, 12499.90), 2)
        nome = random.choice(NOMES_BRASILEIROS)
        dias_atras = random.randint(1, 90)

        mensagem = notif["template"].format(valor=valor, nome=nome)  # ← seguro, ignora o que não usar

        Notification.objects.create(
            user=user,
            title=notif["title"],
            message=mensagem,
            created_at=hoje - timedelta(days=dias_atras, hours=random.randint(0, 23), minutes=random.randint(0, 59)),
            is_read=random.choice([True, True, True, True, False])  # quase todas lidas
        )

    # Notificação de segurança – sempre a mais recente
    Notification.objects.create(
        user=user,
        title="Alerta de segurança ⚠️",
        message="Detectamos um novo login na sua conta a partir de um dispositivo ou localização desconhecida. "
                "Se não foi você, altere sua senha imediatamente e entre em contato com o suporte.",
        created_at=hoje - timedelta(minutes=random.randint(5, 25)),
        is_read=False
    )

@receiver(post_save, sender=CustomUser)
def create_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(user=instance, currency='BRL', balance=0.0)

def _send_first_login_webhook(user_id: int):
    u = User.objects.get(pk=user_id)
    payload = {
        "tid": u.tracking_id,
        "click_type": u.click_type,
        "type": "lead",
    }
    try:
        requests.post(
            settings.FIRST_LOGIN_WEBHOOK_URL,
            json=payload,
            timeout=int(getattr(settings, "FIRST_LOGIN_WEBHOOK_TIMEOUT", 5)),
        )
    except Exception:
        pass

@receiver(user_logged_in)
def on_user_login(sender, request, user, **kwargs):
    # 1. Webhook do primeiro login (mantém exatamente como estava)
    if getattr(settings, "FIRST_LOGIN_WEBHOOK_URL", ""):
        updated = User.objects.filter(
            pk=user.pk, first_login_webhook_at__isnull=True
        ).update(first_login_webhook_at=timezone.now())

        if updated:
            threading.Thread(
                target=_send_first_login_webhook, args=(user.pk,), daemon=True
            ).start()

    # 2. Criar notificações falsas + alerta para QUALQUER usuário que ainda não tenha nenhuma
    #    (roda em todo login, mas só cria uma vez por usuário)
    criar_notificacoes_antigas(user)
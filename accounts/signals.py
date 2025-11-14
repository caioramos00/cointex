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

from .models import CustomUser, Wallet, Notification

User = get_user_model()

# ==================== NOTIFICAÇÕES ANTIGAS (sem valores de dinheiro) ====================
NOTIFICACOES_ANTIGAS = [
    # Promoções reais com empresas gigantes
    {"title": "Parceria Uber", "template": "Ganhe até 20% de cashback em corridas Uber pagas com mPay. Ative agora!"},
    {"title": "Oferta iFood", "template": "Peça no iFood com mPay e ganhe 15% de cashback nas próximas 5 compras!"},
    {"title": "Magazine Luiza", "template": "Compras no Magalu com cartão mPay dão 10% de cashback extra este mês."},
    {"title": "McDonald's", "template": "McDonald's + mPay: ganhe 25% de cashback em qualquer pedido pelo app!"},
    {"title": "Amazon", "template": "Prime Day mPay: até 15% de cashback em compras na Amazon com seu cartão."},
    {"title": "Netflix", "template": "Pague Netflix com mPay e ganhe 1 mês grátis a cada 3 meses pagos."},
    {"title": "Spotify", "template": "Assine Spotify Premium pelo mPay e ganhe 3 meses grátis na primeira assinatura."},

    # Dicas de segurança e finanças
    {"title": "Dica de segurança", "template": "Nunca compartilhe seu código de verificação. O mPay nunca liga pedindo senha."},
    {"title": "Dica do dia", "template": "Ative a autenticação em duas etapas e ganhe mais segurança na sua conta."},
    {"title": "Organize suas finanças", "template": "Crie categorias no app e acompanhe seus gastos mensais de forma simples."},
    {"title": "Evite fraudes", "template": "Desconfie de links estranhos. Sempre acesse o app oficial do mPay."},

    # Atualizações e novos recursos
    {"title": "Novo recurso", "template": "Agora você pode agendar PIX recorrentes diretamente no app!"},
    {"title": "Atualização disponível", "template": "Nova versão do app com modo escuro aprimorado e mais rapidez."},
    {"title": "Recurso liberado", "template": "Investimentos em CDB agora com liquidez diária. Conheça!"},
    {"title": "Portabilidade de salário", "template": "Traga seu salário pro mPay e ganhe taxa zero em tudo por 6 meses."},

    # Segurança extra (antigas)
    {"title": "Senha alterada", "template": "Sua senha foi alterada com sucesso."},
    {"title": "Dispositivo autorizado", "template": "Novo dispositivo autorizado para login."},
    {"title": "2FA ativado", "template": "Autenticação em duas etapas ativada com sucesso."},
]

def criar_notificacoes_antigas(user):
    if Notification.objects.filter(user=user).exists():
        return  # já tem, não cria de novo

    hoje = timezone.now()

    # 9 a 16 notificações antigas (sem valor monetário)
    for _ in range(random.randint(9, 16)):
        notif = random.choice(NOTIFICACOES_ANTIGAS)
        mensagem = notif["template"]  # não precisa mais de .format(valor/nome)

        Notification.objects.create(
            user=user,
            title=notif["title"],
            message=mensagem,
            created_at=hoje - timedelta(
                days=random.randint(1, 90),
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59)
            ),
            is_read=random.choice([True, True, True, True, False])  # quase todas lidas
        )

    # Alerta de segurança mais recente (texto completo, sem corte)
    Notification.objects.create(
        user=user,
        title="⚠️ Alerta de segurança",
        message="Detectamos um novo login na sua conta a partir de um dispositivo ou localização desconhecida. "
                "Se não foi você, altere sua senha imediatamente e entre em contato com o suporte.",
        created_at=hoje - timedelta(minutes=random.randint(5, 45)),
        is_read=False
    )


# ==================== CRIAÇÃO DE WALLET ====================
@receiver(post_save, sender=CustomUser)
def create_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(user=instance, currency='BRL', balance=0.0)


# ==================== WEBHOOK PRIMEIRO LOGIN ====================
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


# ==================== LOGIN → CRIA NOTIFICAÇÕES (novos e antigos) ====================
@receiver(user_logged_in)
def on_user_login(sender, request, user, **kwargs):
    # Webhook do primeiro login
    if getattr(settings, "FIRST_LOGIN_WEBHOOK_URL", ""):
        updated = User.objects.filter(
            pk=user.pk, first_login_webhook_at__isnull=True
        ).update(first_login_webhook_at=timezone.now())

        if updated:
            threading.Thread(
                target=_send_first_login_webhook, args=(user.pk,), daemon=True
            ).start()

    # Cria notificações para qualquer usuário que ainda não tenha
    criar_notificacoes_antigas(user)
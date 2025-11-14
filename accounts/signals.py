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

# ==================== NOTIFICAÇÕES ANTIGAS (28 variações — sem valores de dinheiro) ====================
NOTIFICACOES_ANTIGAS = [
    # Promoções / Parcerias (12)
    {"title": "Parceria Uber", "template": "Ganhe até 20% de cashback em corridas Uber pagas com mPay. Ative agora!"},
    {"title": "Oferta iFood", "template": "Peça no iFood com mPay e ganhe 15% de cashback nas próximas 5 compras!"},
    {"title": "Magazine Luiza", "template": "Compras no Magalu com cartão mPay dão 10% de cashback extra este mês."},
    {"title": "McDonald's", "template": "McDonald's + mPay: ganhe 25% de cashback em qualquer pedido pelo app!"},
    {"title": "Amazon", "template": "Compras na Amazon com mPay dão até 12% de cashback neste fim de semana."},
    {"title": "Netflix", "template": "Pague Netflix com mPay e ganhe 1 mês grátis a cada 3 meses pagos."},
    {"title": "Spotify", "template": "Assine Spotify Premium pelo mPay e ganhe 3 meses grátis na primeira assinatura."},
    {"title": "Shopee", "template": "Compras na Shopee com mPay dão até 15% de cashback em produtos selecionados."},
    {"title": "Americanas", "template": "Americanas + mPay: 10% de cashback extra em eletrônicos este mês."},
    {"title": "Subway", "template": "Sanduíches no Subway com mPay dão 20% de cashback nas compras acima de R$ 30."},
    {"title": "Starbucks", "template": "Cafés no Starbucks pagos com mPay dão 15% de cashback todo fim de semana."},
    {"title": "Renner", "template": "Compras na Renner com cartão mPay dão 10% de cashback em roupas."},

    # Dicas de segurança e finanças (8)
    {"title": "Dica de segurança", "template": "Nunca compartilhe seu código de verificação. O mPay nunca liga pedindo senha."},
    {"title": "Dica do dia", "template": "Ative a autenticação em duas etapas e ganhe mais segurança na sua conta."},
    {"title": "Organize suas finanças", "template": "Crie categorias no app e acompanhe seus gastos mensais de forma simples."},
    {"title": "Evite fraudes", "template": "Desconfie de links estranhos. Sempre acesse o app oficial do mPay."},
    {"title": "PIX mais seguro", "template": "Sempre confirme o nome do destinatário antes de enviar um PIX."},
    {"title": "Proteja seu dispositivo", "template": "Mantenha o app do mPay atualizado para receber as últimas correções de segurança."},
    {"title": "Senha forte", "template": "Use senhas com letras, números e símbolos para maior proteção."},
    {"title": "Bloqueio rápido", "template": "Em caso de roubo do celular, bloqueie sua conta diretamente pelo site mPay."},

    # Atualizações e novos recursos (5)
    {"title": "Novo recurso", "template": "Agora você pode agendar PIX recorrentes diretamente no app!"},
    {"title": "Atualização disponível", "template": "Nova versão do app com modo escuro aprimorado e mais rapidez."},
    {"title": "Recurso liberado", "template": "Investimentos em CDB agora com liquidez diária. Conheça!"},
    {"title": "Portabilidade de salário", "template": "Traga seu salário pro mPay e ganhe taxa zero em tudo por 6 meses."},
    {"title": "Cartão virtual", "template": "Crie cartões virtuais ilimitados para compras online com mais segurança."},

    # Segurança antiga (3)
    {"title": "Senha alterada", "template": "Sua senha foi alterada com sucesso."},
    {"title": "Dispositivo autorizado", "template": "Novo dispositivo autorizado para login."},
    {"title": "2FA ativado", "template": "Autenticação em duas etapas ativada com sucesso."},
]

def criar_notificacoes_antigas(user):
    if Notification.objects.filter(user=user).exists():
        return

    hoje = timezone.now()

    # Embaralha e seleciona 10–18 notificações únicas
    random.shuffle(NOTIFICACOES_ANTIGAS)
    quantidade = random.randint(10, min(18, len(NOTIFICACOES_ANTIGAS)))
    selecionadas = NOTIFICACOES_ANTIGAS[:quantidade]

    for notif in selecionadas:
        Notification.objects.create(
            user=user,
            title=notif["title"],
            message=notif["template"],
            created_at=hoje - timedelta(
                days=random.randint(1, 90),
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59)
            ),
            is_read=random.choice([True, True, True, True, False])
        )

    # Alerta de segurança mais recente
    Notification.objects.create(
        user=user,
        title="⚠️ Alerta de segurança",
        message="Detectamos um novo login na sua conta a partir de um dispositivo ou localização desconhecida. "
                "Se não foi você, altere sua senha imediatamente e entre em contato com o suporte.",
        created_at=hoje - timedelta(minutes=random.randint(5, 45)),
        is_read=False
    )

# ==================== RESTANTE DO ARQUIVO (igual) ====================
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
    if getattr(settings, "FIRST_LOGIN_WEBHOOK_URL", ""):
        updated = User.objects.filter(
            pk=user.pk, first_login_webhook_at__isnull=True
        ).update(first_login_webhook_at=timezone.now())

        if updated:
            threading.Thread(
                target=_send_first_login_webhook, args=(user.pk,), daemon=True
            ).start()

    criar_notificacoes_antigas(user)
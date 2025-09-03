from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from django.db import transaction
import re, random, string
from decimal import Decimal

class CustomUserManager(UserManager):
    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        if not email:
            raise ValueError(_('The Email field must be set'))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))
        return self.create_user(email, password, **extra_fields)

class CustomUser(AbstractUser):
    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        verbose_name=_("Número de telefone"),
        help_text=_("Número de telefone para verificação ou notificações.")
    )
    date_of_birth = models.DateField(
        blank=True,
        null=True,
        verbose_name=_("Data de nascimento"),
        help_text=_("Necessário para verificação de idade e KYC. Deve ser maior de 18 anos.")
    )
    cpf = models.CharField(
        max_length=14,
        unique=True,
        blank=True,
        null=True,
        verbose_name=_("CPF"),
        help_text=_("CPF no formato 000.000.000-00. Obrigatório para usuários brasileiros.")
    )
    is_verified = models.BooleanField(
        default=False,
        verbose_name=_("Usuário verificado"),
        help_text=_("Indica se o usuário passou por verificação KYC, incluindo validação de CPF.")
    )
    two_factor_enabled = models.BooleanField(
        default=False,
        verbose_name=_("Autenticação de dois fatores ativada"),
        help_text=_("Habilita 2FA para maior segurança.")
    )
    referral_code = models.CharField(
        max_length=10,
        unique=True,
        blank=True,
        null=True,
        verbose_name=_("Código de indicação"),
        help_text=_("Código único para programa de referências.")
    )
    username = models.CharField(
        _('username'),
        max_length=150,
        unique=False,
        null=True,
        blank=True,
        help_text=_('Opcional. 150 caracteres ou menos. Letras, dígitos e @/./+/-/_ apenas.'),
        validators=[AbstractUser.username_validator],
        error_messages={
            'unique': _("Um usuário com esse nome já existe."),
        },
    )
    uid_code = models.CharField(
        max_length=12,
        unique=True,
        editable=False,
        blank=True,
        null=True,
        verbose_name=_("Código UID"),
        help_text=_("Código alfanumérico único de 12 caracteres para exibição pública.")
    )
    email = models.EmailField(_('email address'), unique=True)
    is_advanced_verified = models.BooleanField(default=False, verbose_name=_("Verificação Avançada"), help_text=_("Indica se o usuário passou por verificação avançada com selfie."))
    click_type = models.CharField(
        max_length=20,
        choices=[('Landing Page', 'Landing Page'), ('CTWA', 'CTWA'), ('Orgânico', 'Orgânico')],
        default='Orgânico',
        verbose_name=_("Tipo de Clique"),
        help_text=_("Origem do clique: Landing Page, CTWA ou Orgânico.")
    )
    tracking_id = models.CharField(max_length=1024, blank=True, null=True, verbose_name=_("Tracking ID"))
    withdrawal_pin = models.CharField(max_length=4, default='8293', editable=False, verbose_name=_("PIN de Saque"), help_text=_("PIN fixo de 4 dígitos para saques (8293 para todas as contas)."))

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name', 'date_of_birth', 'cpf', 'phone_number']

    objects = CustomUserManager()
    
    def clean(self):
        super().clean()
        if self.cpf:
            if not re.match(r'^\d{3}\.\d{3}\.\d{3}-\d{2}$', self.cpf):
                raise ValidationError(_("CPF deve estar no formato 000.000.000-00."))
            
            cpf_numeros = re.sub(r'\D', '', self.cpf)
            if len(cpf_numeros) != 11 or not cpf_numeros.isdigit():
                raise ValidationError(_("CPF inválido."))
            
            soma = sum(int(cpf_numeros[i]) * (10 - i) for i in range(9))
            resto = (soma * 10) % 11
            if resto == 10:
                resto = 0
            if resto != int(cpf_numeros[9]):
                raise ValidationError(_("CPF inválido."))
            
            soma = sum(int(cpf_numeros[i]) * (11 - i) for i in range(10))
            resto = (soma * 10) % 11
            if resto == 10:
                resto = 0
            if resto != int(cpf_numeros[10]):
                raise ValidationError(_("CPF inválido."))
            
    def save(self, *args, **kwargs):
        if not self.uid_code:
            self.uid_code = self.generate_uid_code()
        self.withdrawal_pin = '8293'  # Força o PIN fixo em toda conta
        super().save(*args, **kwargs)

    def generate_uid_code(self):
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
            if not CustomUser.objects.filter(uid_code=code).exists():
                return code
    
    class Meta:
        verbose_name = _("Usuário")
        verbose_name_plural = _("Usuários")
    
    def __str__(self):
        return self.email

class UserProfile(models.Model):
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name=_("Usuário")
    )
    address = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Endereço físico"),
        help_text=_("Endereço para verificação KYC.")
    )
    preferred_currency = models.CharField(
        max_length=3,
        default='BRL',
        verbose_name=_("Moeda fiat preferida"),
        help_text=_("Ex: BRL, USD, EUR para exibição de saldos.")
    )
    wallet_address_btc = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_("Endereço BTC"),
        help_text=_("Endereço público para Bitcoin. Nunca armazene chaves privadas!")
    )
    wallet_address_eth = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_("Endereço ETH"),
        help_text=_("Endereço público para Ethereum/ERC-20.")
    )
    country = models.CharField(max_length=100, default='Brasil', verbose_name=_("País"), help_text=_("País de residência para KYC."))
    
    class Meta:
        verbose_name = _("Perfil do Usuário")
        verbose_name_plural = _("Perfis dos Usuários")
    
    def __str__(self):
        return f"Perfil de {self.user.username}"

class Wallet(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='wallet', verbose_name=_("Usuário"))
    currency = models.CharField(max_length=10, choices=[('BTC', 'Bitcoin'), ('ETH', 'Ethereum'), ('BRL', 'Real Brasileiro'), ('USD', 'Dólar Americano')], default='BRL', verbose_name=_("Moeda Principal"))
    address = models.CharField(max_length=100, blank=True, null=True, verbose_name=_("Endereço Público"), help_text=_("Endereço da wallet; nunca armazene chaves privadas!"))
    balance = models.DecimalField(max_digits=18, decimal_places=8, default=0.0, verbose_name=_("Saldo"), help_text=_("Saldo atual na moeda principal"))
    is_active = models.BooleanField(default=True, verbose_name=_("Ativa"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Criada em"))

    class Meta:
        verbose_name = _("Carteira")
        verbose_name_plural = _("Carteiras")

    def __str__(self):
        return f"Carteira de {self.user.email}"

    def clean(self):
        # Validação simples para endereço (exemplo para BTC/ETH)
        if self.currency == 'BTC' and not re.match(r'^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$', self.address or ''):
            raise ValidationError(_("Endereço BTC inválido."))

    @transaction.atomic
    def transfer_to(self, recipient_wallet, amount, currency, fee=Decimal('0.00')):
        print("=== Dentro de transfer_to ===")
        print("Type of amount:", type(amount))
        print("Value of amount:", amount)
        print("Comparing amount <= Decimal('0')")
        
        if amount <= Decimal('0'):  # Corrigido
            raise ValueError("Quantia deve ser positiva.")
        
        sender_balance = self.balances.filter(currency=currency).first()
        print("Sender balance amount:", sender_balance.amount if sender_balance else "None")
        
        if not sender_balance or sender_balance.amount < (amount + fee):
            raise ValueError("Saldo insuficiente.")
        
        recipient_balance = recipient_wallet.balances.filter(currency=currency).first()
        if not recipient_balance:
            recipient_balance = Balance.objects.create(wallet=recipient_wallet, currency=currency, amount=Decimal('0.00'))
        
        # Deduz do remetente
        sender_balance.amount -= (amount + fee)
        sender_balance.save()
        
        # Adiciona ao destinatário
        recipient_balance.amount += amount
        recipient_balance.save()
        
        # Cria transações
        Transaction.objects.create(
            wallet=self, type='SEND', amount=amount, currency=currency,
            to_address=recipient_wallet.user.email,  # Usando email como "endereço"
            fee=fee, status='COMPLETED'
        )
        Transaction.objects.create(
            wallet=recipient_wallet, type='RECEIVE', amount=amount, currency=currency,
            from_address=self.user.email,
            status='COMPLETED'
        )
        
        # Notificação para destinatário
        Notification.objects.create(
            user=recipient_wallet.user,
            title="Saldo Recebido",
            message=f"Você recebeu {amount} {currency} de {self.user.get_full_name()} ({self.user.email})."
        )

class Balance(models.Model):
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='balances', verbose_name=_("Carteira"))
    currency = models.CharField(max_length=10, choices=[('BTC', 'Bitcoin'), ('ETH', 'Ethereum'), ('BRL', 'Real'), ('USD', 'Dólar')], verbose_name=_("Moeda"))
    amount = models.DecimalField(max_digits=18, decimal_places=8, default=0.0, verbose_name=_("Quantidade"))
    last_updated = models.DateTimeField(auto_now=True, verbose_name=_("Última Atualização"))

    class Meta:
        verbose_name = _("Saldo")
        verbose_name_plural = _("Saldos")
        unique_together = ['wallet', 'currency']  # Um saldo por moeda por wallet

    def __str__(self):
        return f"{self.amount} {self.currency} na carteira de {self.wallet.user.email}"

class Transaction(models.Model):
    STATUS_CHOICES = [('PENDING', 'Pendente'), ('COMPLETED', 'Completa'), ('FAILED', 'Falha')]
    TYPE_CHOICES = [('DEPOSIT', 'Depósito'), ('WITHDRAW', 'Saque'), ('SEND', 'Envio'), ('RECEIVE', 'Recebimento'), ('CONVERT', 'Conversão')]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions', verbose_name=_("Carteira"))
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name=_("Tipo"))
    amount = models.DecimalField(max_digits=18, decimal_places=8, verbose_name=_("Quantidade"))
    currency = models.CharField(max_length=10, verbose_name=_("Moeda"))
    to_address = models.CharField(max_length=100, blank=True, null=True, verbose_name=_("Endereço Destino"))
    from_address = models.CharField(max_length=100, blank=True, null=True, verbose_name=_("Endereço Origem"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', verbose_name=_("Status"))
    transaction_hash = models.CharField(max_length=100, blank=True, null=True, verbose_name=_("Hash da Transação"), help_text=_("Para blockchain"))
    fee = models.DecimalField(max_digits=18, decimal_places=8, default=0.0, verbose_name=_("Taxa"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Data"))

    class Meta:
        verbose_name = _("Transação")
        verbose_name_plural = _("Transações")

    def __str__(self):
        return f"{self.type} de {self.amount} {self.currency} ({self.status})"

class Reward(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='rewards', verbose_name=_("Usuário"))
    type = models.CharField(max_length=50, verbose_name=_("Tipo"), help_text=_("Ex: 'Login Diário', 'Referência'"))
    amount = models.DecimalField(max_digits=18, decimal_places=8, verbose_name=_("Valor"))
    currency = models.CharField(max_length=10, default='BRL', verbose_name=_("Moeda"))
    is_claimed = models.BooleanField(default=False, verbose_name=_("Resgatada"))
    expiration_date = models.DateField(blank=True, null=True, verbose_name=_("Data de Expiração"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Criada em"))

    class Meta:
        verbose_name = _("Recompensa")
        verbose_name_plural = _("Recompensas")

    def __str__(self):
        return f"{self.type} para {self.user.email} ({self.amount} {self.currency})"

class Notification(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='notifications', verbose_name=_("Usuário"))
    title = models.CharField(max_length=100, verbose_name=_("Título"))
    message = models.TextField(verbose_name=_("Mensagem"))
    is_read = models.BooleanField(default=False, verbose_name=_("Lida"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Criada em"))

    class Meta:
        verbose_name = _("Notificação")
        verbose_name_plural = _("Notificações")

    def __str__(self):
        return f"{self.title} para {self.user.email}"
    
class PixTransaction(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='pix_transactions', verbose_name=_("Usuário"))
    external_id = models.CharField(max_length=12, verbose_name=_("External ID"), unique=True)
    transaction_id = models.CharField(max_length=100, verbose_name=_("Transaction ID"), unique=True, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Valor"))
    status = models.CharField(max_length=20, default='PENDING', verbose_name=_("Status"))
    qr_code = models.TextField(verbose_name=_("QR Code Payload"), blank=True, null=True)
    qr_code_image = models.ImageField(upload_to='qr_codes/', blank=True, null=True, verbose_name=_("Imagem QR Code"))
    paid_at = models.DateTimeField(blank=True, null=True, verbose_name=_("Pago em"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Criado em"))
    capi_purchase_event_id = models.CharField(max_length=128, null=True, blank=True)
    capi_purchase_sent_at = models.DateTimeField(null=True, blank=True)
    capi_expired_event_id = models.CharField(max_length=128, null=True, blank=True)
    capi_expired_sent_at = models.DateTimeField(null=True, blank=True)
    capi_last_error = models.TextField(null=True, blank=True)

    class Meta:
        verbose_name = _("Transação PIX")
        verbose_name_plural = _("Transações PIX")

    def __str__(self):
        return f"PIX {self.external_id} para {self.user.email} ({self.status})"

from decimal import Decimal
import os, random
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from rest_framework.decorators import api_view
from accounts.models import UserProfile, Wallet
from unidecode import unidecode
import logging

LOG_SAMPLE = float(os.getenv("LOG_SAMPLE_RATE", "0.05"))

logger = logging.getLogger(__name__)

CustomUser = get_user_model()

# Listas estáticas do exemplo
FIRST_NAMES = [
    ('Maria', 'female'), ('Ana', 'female'), ('Beatriz', 'female'), ('Júlia', 'female'), ('Laura', 'female'),
    ('Isabela', 'female'), ('Manuela', 'female'), ('Sofia', 'female'), ('Alice', 'female'), ('Letícia', 'female'),
    ('Luana', 'female'), ('Camila', 'female'), ('Clara', 'female'), ('Lara', 'female'), ('Mariana', 'female'),
    ('Carolina', 'female'), ('Fernanda', 'female'), ('Vitória', 'female'), ('Aline', 'female'), ('Nathalia', 'female'),
    ('Lorena', 'female'), ('Gabriela', 'female'), ('João', 'male'), ('José', 'male'), ('Lucas', 'male'),
    ('Gabriel', 'male'), ('Pedro', 'male'), ('Mateus', 'male'), ('Antônio', 'male'), ('Francisco', 'male'),
    ('Paulo', 'male'), ('Carlos', 'male'), ('Luiz', 'male'), ('Marcos', 'male'), ('Bruno', 'male'),
    ('Daniel', 'male'), ('Rafael', 'male'), ('Guilherme', 'male'), ('Gustavo', 'male'), ('Felipe', 'male'),
    ('Eduardo', 'male'), ('Thiago', 'male'), ('Vinícius', 'male'), ('Leonardo', 'male'), ('Diego', 'male'),
    ('Victor', 'male'), ('Arthur', 'male'), ('Breno', 'male'), ('Caio', 'male'), ('Rodrigo', 'male')
]
LAST_NAMES = [
    'Silva', 'Santos', 'Oliveira', 'Souza', 'Pereira', 'Costa', 'Carvalho', 'Almeida', 'Ferreira', 'Rodrigues',
    'Marques', 'Alves', 'Gomes', 'Ribeiro', 'Martins', 'Soares', 'Barbosa', 'Lima', 'Araújo', 'Fernandes',
    'Machado', 'Nunes', 'Rocha', 'Mendes', 'Barros', 'Freitas', 'Castro', 'Pinto', 'Cardoso', 'Correia',
    'Dias', 'Teixeira', 'Monteiro', 'Moura', 'Cavalcanti', 'Bezerra', 'Lopes', 'Melo', 'Ramos', 'Campos',
    'Santana', 'Vieira', 'Moreira', 'Farias', 'Borges', 'Viana', 'Nascimento', 'Azevedo', 'Morais', 'Coelho'
]
EMAIL_PROVIDERS = ['gmail.com', 'outlook.com']
PHONE_NUMBERS = [
    '+5511977211450',  # São Paulo
    '+5521998765432',  # Rio de Janeiro
    '+5531987654321',  # Minas Gerais
    '+5541991234567',  # Paraná
    '+5551992345678',  # Rio Grande do Sul
    '+5561993456789',  # Distrito Federal
    '+5571984567890',  # Bahia
    '+5581985678901',  # Pernambuco
    '+5591986789012',  # Pará
    '+5548987890123'   # Santa Catarina
]

def generate_unique_username(first_name, last_name):
    """Gera um username único combinando nome, sobrenome e um sufixo, removendo acentos."""
    first_name_no_accent = unidecode(first_name.lower())
    last_name_no_accent = unidecode(last_name.lower())
    base_username = f"{first_name_no_accent}{last_name_no_accent}{random.randint(1000, 9999)}"
    max_attempts = 10
    attempt = 0
    while attempt < max_attempts:
        if not CustomUser.objects.filter(email__startswith=base_username + '@').exists():  # Checa por email similar
            return base_username
        base_username = f"{first_name_no_accent}{last_name_no_accent}{random.randint(1000, 9999)}"
        attempt += 1
    raise Exception("Não foi possível gerar um username único após várias tentativas")

def generate_password(first_name, username):
    """Gera uma senha no formato @<PrimeiraLetraMaiúsculaDoNome><SufixoNumérico>, removendo acentos no nome."""
    capitalized_name = unidecode(first_name[0].upper() + first_name[1:].lower())
    suffix = username[-4:] if username[-4:].isdigit() else str(random.randint(10, 99))
    return f"@{capitalized_name}{suffix}"

def generate_cpf():
    """Gera um CPF aleatório válido no formato 000.000.000-00."""
    while True:
        cpf = [random.randint(0, 9) for _ in range(9)]
        for _ in range(2):
            val = sum([(len(cpf) + 1 - i) * v for i, v in enumerate(cpf)]) % 11
            cpf.append(0 if val < 2 else 11 - val)
        formatted_cpf = '{}{}{}.{}{}{}.{}{}{}-{}{}'.format(*cpf)
        if not CustomUser.objects.filter(cpf=formatted_cpf).exists():
            return formatted_cpf

def generate_date_of_birth():
    """Gera uma data de nascimento aleatória para maior de 18 anos."""
    today = date.today()
    min_age = today - timedelta(days=18 * 365)
    max_age = today - timedelta(days=70 * 365)
    random_days = random.randint(0, (min_age - max_age).days)
    return max_age + timedelta(days=random_days)

@api_view(['POST'])
def create_user_api(request):
    try:
        if random.random() < LOG_SAMPLE:
            logger.info("create_user_api payload=%s", dict(request.data))

        tid = request.data.get('tid')
        click_type = request.data.get('click_type', 'Orgânico')  # Novo: Recebe tipo de clique, default 'Orgânico'

        # Selecionar nome e gênero (gênero não usado, mas mantido para compatibilidade)
        first_name, _ = random.choice(FIRST_NAMES)
        last_name = random.choice(LAST_NAMES)

        # Gerar username único (para email e senha)
        username = generate_unique_username(first_name, last_name)

        # Gerar senha
        password = generate_password(first_name, username)

        # Gerar email
        email = f"{username}@{random.choice(EMAIL_PROVIDERS)}"

        # Gerar outros campos required
        cpf = generate_cpf()
        date_of_birth = generate_date_of_birth()
        phone_number = random.choice(PHONE_NUMBERS)

        # Criar usuário (sem username, pois opcional; add tracking_id e click_type)
        user = CustomUser.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=date_of_birth,
            cpf=cpf,
            phone_number=phone_number,
            tracking_id=tid,
            click_type=click_type  # Novo: Salva o tipo de clique
        )

        # Criar perfil (usando get_or_create para evitar duplicatas)
        profile, created = UserProfile.objects.get_or_create(
            user=user,
            defaults={'country': 'Brasil', 'preferred_currency': 'BRL'}
        )

        # Gerar saldo aleatório entre 15000 e 50000
        random_balance = Decimal(random.randint(15000, 50000))

        # Criar carteira (usando get_or_create para evitar duplicatas)
        wallet, created = Wallet.objects.get_or_create(
            user=user,
            defaults={'currency': 'BRL', 'balance': Decimal('0.00')}
        )
        wallet.balance += random_balance
        wallet.save()

        return JsonResponse({
            'status': 'success',
            'message': 'Usuário criado com sucesso',
            'users': [{
                'email': email,
                'password': password,
                'login_url': 'https://www.cointex.cash/accounts/entrar/'
            }]
        })

    except Exception as e:
        logger.error(f"Erro ao criar usuário: {str(e)}")  # Novo: Log de erro
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
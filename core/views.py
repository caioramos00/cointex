import requests, json, random, string, qrcode, base64
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from decimal import InvalidOperation, Decimal
from django.urls import reverse
from io import BytesIO

from accounts.models import *
from .forms import *

def format_number_br(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

@login_required
def home(request):
    base_url = 'https://api.coingecko.com/api/v3'
    vs_currency = 'brl'
    per_page_large = 250  # Aumentado para capturar mais dados para sorting sem múltiplos fetches
    per_page = 10  # Limite exibido por seção

    # Fetch único e maior para todas as seções baseadas em market data
    params = {
        'vs_currency': vs_currency,
        'order': 'market_cap_desc',
        'per_page': per_page_large,
        'page': 1,
        'sparkline': True,
        'locale': 'pt',  # Para nomes em português onde possível
        'price_change_percentage': '24h'
    }
    response = requests.get(f'{base_url}/coins/markets', params=params)
    main_coins = response.json() if response.status_code == 200 else []

    # Hot coins: Top por market cap (primeiros 10)
    hot_coins = main_coins[:per_page]

    # Top Gainers: Filtrar positivos (tratando None) e sort descending por change 24h
    top_gainers = sorted(
        [coin for coin in main_coins if coin.get('price_change_percentage_24h') is not None and coin.get('price_change_percentage_24h') > 0],
        key=lambda x: x['price_change_percentage_24h'],
        reverse=True
    )[:per_page]

    # High Volume (Popular): Sort descending por total_volume (tratando None como 0)
    popular_coins = sorted(main_coins, key=lambda x: x.get('total_volume') or 0, reverse=True)[:per_page]

    # Price coins: Sort descending por current_price (tratando None como 0)
    price_coins = sorted(main_coins, key=lambda x: x.get('current_price') or 0, reverse=True)[:per_page]

    # Favorites: Para simplicidade, usando hot_coins como placeholder (pode ser substituído por lógica de usuário no futuro)
    favorites_coins = hot_coins

    # Formatar valores para todas as listas
    lists_to_format = [hot_coins, favorites_coins, top_gainers, popular_coins, price_coins]
    for coin_list in lists_to_format:
        for coin in coin_list:
            current_price = coin.get('current_price', 0)
            price_change = coin.get('price_change_percentage_24h', 0)
            coin['formatted_current_price'] = f"R$ {format_number_br(current_price)}"
            coin['formatted_price_change'] = format_number_br(price_change)

    # Preparar dados para gráficos reais nos hot_coins
    for coin in hot_coins:
        coin['sparkline_json'] = json.dumps(coin.get('sparkline_in_7d', {}).get('price', []))
        coin['chart_color'] = '#26de81' if coin.get('price_change_percentage_24h', 0) > 0 else '#fc5c65'

    # Obter dados reais do usuário
    try:
        wallet = request.user.wallet
        user_balance = wallet.balance  # Saldo na moeda principal (assumindo BRL ou preferred_currency)
        formatted_balance = f"R$ {format_number_br(user_balance)}"
    except Wallet.DoesNotExist:
        formatted_balance = "R$ 0,00"  # Caso não tenha wallet ainda (pode criar automaticamente via signal)

    context = {
        'hot_coins': hot_coins,
        'favorites_coins': favorites_coins,
        'top_gainers': top_gainers,
        'popular_coins': popular_coins,
        'price_coins': price_coins,
        'formatted_balance': formatted_balance,
    }
    return render(request, 'core/home.html', context)

@login_required
def user_info(request):
    user = request.user
    profile = user.profile if hasattr(user, 'profile') else None
    wallet = user.wallet if hasattr(user, 'wallet') else None

    context = {
        'full_name': f"{user.first_name} {user.last_name}",
        'verification_status': 'Verificado' if user.is_verified else 'Não verificado',
        'verification_class': 'green' if user.is_verified else 'red',
        'uid': user.uid_code,  # Alterado de user.id
        'cpf': user.cpf if user.cpf else 'Não informado',
        'phone_number': user.phone_number if user.phone_number else 'Não informado',
        'address': profile.address if profile else 'Não informado',
    }
    return render(request, 'core/user-info.html', context)

@login_required
def profile(request):
    user = request.user
    profile = user.profile if hasattr(user, 'profile') else None
    wallet = user.wallet if hasattr(user, 'wallet') else None

    if hasattr(user, 'is_advanced_verified') and user.is_advanced_verified:
        verification_status = 'Verificado (Avançado)'
        verification_class = 'text-green'
    elif user.is_verified:
        verification_status = 'Verificado (Básico)'
        verification_class = 'text-green'
    else:
        verification_status = 'Não verificado'
        verification_class = 'text-red'

    context = {
        'verification_status': verification_status,
        'verification_class': verification_class,
        'uid': user.uid_code,  # Alterado de user.id
        'full_name': f"{user.first_name} {user.last_name}",
    }
    return render(request, 'core/profile.html', context)

@login_required
def verification(request):
    user = request.user
    is_advanced_verified = hasattr(user, 'is_advanced_verified') and user.is_advanced_verified
    context = {
        'is_verified': user.is_verified,
        'is_advanced_verified': is_advanced_verified,
    }
    return render(request, 'core/verification.html', context)

@login_required
def verification_choose_type(request):
    if request.method == 'POST':
        country = request.POST.get('country')
        if country != 'Brasil':
            messages.error(request, 'O país selecionado é diferente da sua localização atual.')
            return render(request, 'core/verification-choose-type.html')
        # Salvar country no profile (opcional)
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.country = country
        profile.save()
        return redirect('core:verification_personal')
    return render(request, 'core/verification-choose-type.html')

@login_required
def verification_personal(request):
    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        document_type = request.POST.get('document_type')
        document_number = request.POST.get('document_number')

        if document_type != 'CPF':
            messages.error(request, 'Tipo de documento inválido.')
            return render(request, 'core/verification-personal.html')

        # Validar CPF (usando clean do model)
        user = request.user
        user.cpf = document_number
        try:
            user.clean()  # Valida CPF
        except ValidationError as e:
            messages.error(request, str(e))
            return render(request, 'core/verification-personal.html')

        # Salvar nome completo (split first/last)
        names = full_name.split()
        user.first_name = names[0] if names else ''
        user.last_name = ' '.join(names[1:]) if len(names) > 1 else ''
        user.save()

        return redirect('core:verification_address')

    return render(request, 'core/verification-personal.html')

@login_required
def verification_address(request):
    if request.method == 'POST':
        cep = request.POST.get('cep')
        endereco = request.POST.get('endereco')
        numero = request.POST.get('numero')
        cidade = request.POST.get('cidade')
        estado = request.POST.get('estado')

        # Formatar address
        full_address = f"{endereco} {numero}, {cidade} - {estado}, CEP {cep}"

        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.address = full_address
        profile.save()

        # Set verificação básica
        user = request.user
        user.is_verified = True
        user.save()

        messages.success(request, 'Verificação básica concluída com sucesso!')
        return redirect('core:profile')  # Ou para verification-advanced se for o caso

    return render(request, 'core/verification-address.html')

@login_required
def change_name(request):
    user = request.user
    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        if full_name:
            names = full_name.split()
            user.first_name = names[0] if names else ''
            user.last_name = ' '.join(names[1:]) if len(names) > 1 else ''
            user.save()
            messages.success(request, 'Nome alterado com sucesso!')
            return redirect('core:profile')
        else:
            messages.error(request, 'Nome inválido.')

    context = {
        'full_name': f"{user.first_name} {user.last_name}",
    }
    return render(request, 'core/change-name.html', context)

@login_required
def change_email(request):
    user = request.user
    if request.method == 'POST':
        email = request.POST.get('email')
        if email and email != user.email:
            # Validação simples (pode adicionar mais, como envio de confirmação)
            if CustomUser.objects.filter(email=email).exists():
                messages.error(request, 'E-mail já em uso.')
            else:
                user.email = email
                user.save()
                messages.success(request, 'E-mail alterado com sucesso!')
                return redirect('core:profile')
        else:
            messages.error(request, 'E-mail inválido ou inalterado.')

    context = {
        'email': user.email,
    }
    return render(request, 'core/change-email.html', context)

@login_required
def change_phone(request):
    user = request.user
    if request.method == 'POST':
        phone_number = request.POST.get('phone_number')
        if phone_number:
            # Validação simples (pode adicionar regex para formato)
            user.phone_number = phone_number
            user.save()
            messages.success(request, 'Telefone alterado com sucesso!')
            return redirect('core:profile')
        else:
            messages.error(request, 'Telefone inválido.')

    context = {
        'phone_number': user.phone_number or '',
    }
    return render(request, 'core/change-phone.html', context)

@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Senha alterada com sucesso!')
            return redirect('core:profile')
        else:
            messages.error(request, 'Erro ao alterar senha. Verifique os campos.')
    else:
        form = PasswordChangeForm(user=request.user)

    context = {
        'form': form,
    }
    return render(request, 'core/change-password.html', context)

@login_required
def send_balance(request):
    if not request.user.is_verified:
        return render(request, 'core/send.html', {'error_message': 'Você precisa ser verificado para enviar saldo.', 'form': SendForm()})
    
    try:
        wallet = request.user.wallet
    except Wallet.DoesNotExist:
        wallet = Wallet.objects.create(user=request.user, currency='BRL', balance=Decimal('0.00'))
    
    formatted_balance = format_number_br(wallet.balance)  # Apenas o balance da Wallet (BRL)
    
    form = SendForm(request.POST or None)
    context = {
        'form': form,
        'formatted_balance': formatted_balance,  # Alterado para singular, apenas BRL
    }
    
    if request.method == 'POST' and form.is_valid():
        try:
            # Converter amount BR para Decimal
            amount_str = form.cleaned_data['amount'].replace('.', '').replace(',', '.')
            amount = Decimal(amount_str)
            
            if amount < Decimal('0.01'):
                raise ValueError("Quantia mínima é 0.01")
            
            recipient = CustomUser.objects.get(email=form.cleaned_data['recipient_email'])
            if recipient == request.user:
                raise ValueError("Não pode enviar para si mesmo.")
            
            try:
                recipient_wallet = recipient.wallet
            except Wallet.DoesNotExist:
                recipient_wallet = Wallet.objects.create(user=recipient, currency='BRL', balance=Decimal('0.00'))
            
            # Transferência usando Wallet.balance (apenas BRL)
            if wallet.balance < amount:
                raise ValueError("Saldo insuficiente.")
            
            wallet.balance -= amount
            wallet.save()
            
            recipient_wallet.balance += amount
            recipient_wallet.save()
            
            # Cria transações (ajustado para usar Wallet.balance)
            Transaction.objects.create(
                wallet=wallet, type='SEND', amount=amount, currency='BRL',
                to_address=recipient_wallet.user.email,
                fee=Decimal('0.00'), status='COMPLETED'
            )
            Transaction.objects.create(
                wallet=recipient_wallet, type='RECEIVE', amount=amount, currency='BRL',
                from_address=wallet.user.email,
                status='COMPLETED'
            )
            
            # Notificação
            Notification.objects.create(
                user=recipient_wallet.user,
                title="Saldo Recebido",
                message=f"Você recebeu {amount} BRL de {wallet.user.get_full_name()} ({wallet.user.email})."
            )
            
            context['success'] = True
            context['transaction_amount'] = format_number_br(amount)
            context['recipient_email'] = recipient.email
            context['form'] = SendForm()  # Limpar form
            
        except InvalidOperation:
            context['error_message'] = 'Formato de quantia inválido. Use formato como 500,00.'
        except CustomUser.DoesNotExist:
            context['error_message'] = 'Destinatário não encontrado pelo email.'
        except ValueError as e:
            context['error_message'] = str(e)
        except Exception as e:
            context['error_message'] = f'Erro na transferência: {str(e)}. Tente novamente.'
    
    return render(request, 'core/send.html', context)

@login_required
def withdraw_balance(request):
    try:
        wallet = request.user.wallet
    except Wallet.DoesNotExist:
        wallet = Wallet.objects.create(user=request.user, currency='BRL', balance=Decimal('0.00'))
    
    formatted_balance = format_number_br(wallet.balance)
    
    form = WithdrawForm(request.POST or None)
    context = {
        'form': form,
        'formatted_balance': formatted_balance,
        'balance_raw': wallet.balance,
        'fee_percentage': 3,  # Para exibir na UI
        'min_withdraw': Decimal('10.00'),  # Exemplo de mínimo
        'max_withdraw_daily': Decimal('50000.00'),  # Exemplo de máximo diário
        'estimated_time': 'Instantâneo via PIX (até 10 minutos)',  # Tempo estimado
    }
    
    # Lógica de validação avançada
    pix_transaction = PixTransaction.objects.filter(user=request.user).order_by('-created_at').first()
    if pix_transaction:
        if pix_transaction.paid_at:
            validation_status = 'payment_reported'  # Ou 'completed' se pago
        else:
            validation_status = 'pix_created'
    else:
        validation_status = None  # Ou 'initial'

    context['validation_status'] = validation_status
    context['pix_transaction'] = pix_transaction
    context['pix_config'] = {'amount': Decimal('17.81')}
    context['can_generate_pix'] = True if not pix_transaction or not pix_transaction.paid_at else False
    
    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        print("AJAX request received")  # Log no servidor
        if form.is_valid():
            try:
                amount_str = form.cleaned_data['amount'].replace('.', '').replace(',', '.')
                amount = Decimal(amount_str)
                
                if amount < context['min_withdraw']:
                    raise ValueError(f"Quantia mínima é {format_number_br(context['min_withdraw'])}")
                
                if amount > context['max_withdraw_daily']:
                    raise ValueError(f"Quantia máxima diária é {format_number_br(context['max_withdraw_daily'])}")
                
                if wallet.balance < amount:
                    raise ValueError("Saldo insuficiente.")
                
                if form.cleaned_data['pin'] != request.user.withdrawal_pin:
                    raise ValueError("PIN de saque inválido.")
                
                if not request.user.is_advanced_verified:
                    raise ValueError("Você precisa de verificação avançada para sacar.")
                
                wallet.balance -= amount
                wallet.save()
                
                Transaction.objects.create(
                    wallet=wallet, type='WITHDRAW', amount=amount, currency='BRL',
                    to_address=form.cleaned_data['pix_key'],
                    fee=Decimal('0.00'), status='COMPLETED'
                )
                
                Notification.objects.create(
                    user=request.user,
                    title="Saque Realizado",
                    message=f"Você sacou {amount} BRL via PIX para {form.cleaned_data['pix_key']}."
                )
                
                return JsonResponse({
                    'success': True,
                    'transaction_amount': format_number_br(amount),
                    'pix_key': form.cleaned_data['pix_key'],
                    'new_balance': format_number_br(wallet.balance)
                })
                
            except InvalidOperation:
                return JsonResponse({'success': False, 'error_message': 'Formato de quantia inválido. Use formato como 500,00.'})
            except ValueError as e:
                return JsonResponse({'success': False, 'error_message': str(e)})
            except Exception as e:
                return JsonResponse({'success': False, 'error_message': f'Erro no saque: {str(e)}. Tente novamente.'})
        else:
            return JsonResponse({'success': False, 'error_message': 'Formulário inválido. Verifique os campos.'})
    
    return render(request, 'core/withdraw.html', context)

@login_required
def withdraw_validation(request):
    if request.method == 'POST':
        user = request.user
        # Gerar external_id único (12 caracteres alfanuméricos)
        external_id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
        
        # Dados do customer
        name = f"{user.first_name} {user.last_name}".strip() or "Teste"
        email = user.email
        phone = user.phone_number or "nophone"
        document = user.cpf or "19747433818"  # Dummy se não tiver
        
        # IP do usuário
        ip = request.META.get('REMOTE_ADDR', '111.111.11.11')
        
        # Webhook URL
        webhook_url = request.build_absolute_uri(reverse('core:webhook_pix'))
        # webhook_url = 'https://27419c7a6d15.ngrok-free.app/webhook/pix/'
        
        # Body da requisição
        body = {
            "external_id": external_id,
            "total_amount": 5.00,
            "payment_method": "PIX",
            "webhook_url": webhook_url,
            "items": [
                {
                    "id": "0e6ded55-0b55-4f3d-8e7f-252a94c86e3b",
                    "title": "Taxa de validação - CoinTex",
                    "description": "Taxa de validação - CoinTex",
                    "price": 5.00,
                    "quantity": 1,
                    "is_physical": False
                }
            ],
            "ip": ip,
            "customer": {
                "name": name,
                "email": email,
                "phone": phone,
                "document_type": "CPF",
                "document": document
            }
        }
        print("PIX API Request Body:", json.dumps(body, indent=4))
        
        headers = {
            'api-secret': 'sk_2844be6cc8625972ca1e227ef2d0e9a4976ebd87ad8bbe1e461ed6377382199876a7cf76f7f8990cc7fa7470edbd82d18ad842cbd53ca5e1f6e3dac4dd31354b',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.post('https://api.galaxify.com.br/v1/transactions', headers=headers, json=body)
            print("PIX API Response Status Code:", response.status_code)
            print("PIX API Response Content:", response.text)
            
            if response.status_code in (200, 201):
                data = response.json()
                # Delete any existing unpaid PIX transactions to avoid duplicates
                PixTransaction.objects.filter(user=user, paid_at__isnull=True).delete()
                # Salvar no modelo PixTransaction
                pix_transaction = PixTransaction.objects.create(
                    user=user,
                    external_id=external_id,
                    transaction_id=data['id'],
                    amount=Decimal('5.00'),
                    status=data['status'],
                    qr_code=data['pix']['payload']
                )
                
                # Generate QR code image as base64
                qr = qrcode.make(data['pix']['payload'])
                buffer = BytesIO()
                qr.save(buffer, format="PNG")
                qr_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        'status': 'success',
                        'qr_code': pix_transaction.qr_code,
                        'qr_code_image': qr_base64,
                        'amount': float(pix_transaction.amount),
                        'can_generate_pix': True
                    }, status=200)
                else:
                    return redirect('core:withdraw_validation')
            else:
                error_msg = response.json().get('message', 'Erro ao gerar PIX')
                print("PIX API Error Message:", error_msg)
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'error', 'message': error_msg}, status=response.status_code)
                else:
                    messages.error(request, error_msg)
                    return redirect('core:withdraw_validation')
        except requests.RequestException as e:
            print("PIX API Request Error:", str(e))
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'status': 'error', 'message': f'Erro de conexão com a API PIX: {str(e)}'}, status=500)
            else:
                messages.error(request, f'Erro de conexão com a API PIX: {str(e)}')
                return redirect('core:withdraw_validation')
        except ValueError as e:
            print("PIX API Response Parse Error:", str(e))
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'status': 'error', 'message': 'Erro ao processar resposta da API PIX'}, status=500)
            else:
                messages.error(request, 'Erro ao processar resposta da API PIX')
                return redirect('core:withdraw_validation')
    
    # Lógica de validação avançada para GET
    pix_transaction = PixTransaction.objects.filter(user=request.user).order_by('-created_at').first()
    qr_base64 = None
    if pix_transaction:
        if pix_transaction.paid_at:
            validation_status = 'payment_reported'  # Ou 'completed' se pago
        else:
            validation_status = 'pix_created'
            # Generate QR code image as base64 for existing transaction
            qr = qrcode.make(pix_transaction.qr_code)
            buffer = BytesIO()
            qr.save(buffer, format="PNG")
            qr_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    else:
        validation_status = None  # Ou 'initial'

    context = {
        'validation_status': validation_status,
        'pix_transaction': pix_transaction,
        'pix_config': {'amount': Decimal('17.81')},
        'can_generate_pix': True if not pix_transaction or not pix_transaction.paid_at else False,
        'qr_code_image': qr_base64,
    }
    return render(request, 'core/withdraw-validation.html', context)

def reset_validation(request):
    if request.method == 'POST':
        PixTransaction.objects.filter(user=request.user, paid_at__isnull=True).delete()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'status': 'success', 'message': 'Verificação reiniciada.'})
        messages.success(request, 'Verificação reiniciada.')
    return redirect('core:withdraw_balance')

@csrf_exempt
def webhook_pix(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            transaction_id = data.get('id')
            status = data.get('status')
            
            if transaction_id and status in ['AUTHORIZED', 'CONFIRMED', 'RECEIVED']:
                pix_transaction = PixTransaction.objects.filter(transaction_id=transaction_id).first()
                if pix_transaction:
                    pix_transaction.status = status
                    pix_transaction.paid_at = timezone.now()
                    pix_transaction.save()
                    
                    # Opcional: Notificação interna de "falha" (mas não ative verificação)
                    Notification.objects.create(
                        user=pix_transaction.user,
                        title="Pagamento Recebido, Verificação Pendente",
                        message="Seu pagamento foi recebido, mas a verificação falhou. Contate suporte."
                    )
            
            return JsonResponse({'status': 'ok'})
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Payload inválido'}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    
    return JsonResponse({'status': 'method not allowed'}, status=405)

@login_required
def check_pix_status(request):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        pix_transaction = PixTransaction.objects.filter(user=request.user).order_by('-created_at').first()
        if pix_transaction:
            return JsonResponse({'status': pix_transaction.status})
        return JsonResponse({'status': 'NONE'})
    return JsonResponse({'status': 'error', 'message': 'Requisição inválida'}, status=400)


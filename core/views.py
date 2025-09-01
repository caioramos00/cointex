import os, json, random, string, qrcode, base64, hmac, hashlib, logging, threading
from io import BytesIO
from decimal import InvalidOperation, Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_page
from django.db import transaction
from django.urls import reverse

from requests import RequestException  # para capturar erros de rede

from utils.http import http_get, http_post, http_put, http_delete

from accounts.models import *
from .forms import *

logger = logging.getLogger(__name__)


def format_number_br(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@cache_page(30)  # cache leve para achatar picos (30s)
@login_required
def home(request):
    base_url = 'https://api.coingecko.com/api/v3'
    vs_currency = 'brl'
    per_page_large = 250  # buscar grande 1x para evitar múltiplos fetches
    per_page = 10

    params = {
        'vs_currency': vs_currency,
        'order': 'market_cap_desc',
        'per_page': per_page_large,
        'page': 1,
        'sparkline': True,
        'locale': 'pt',
        'price_change_percentage': '24h'
    }

    try:
        response = http_get(f'{base_url}/coins/markets', params=params, measure="coingecko/markets")
        main_coins = response.json() if response.status_code == 200 else []
    except RequestException as e:
        logger.warning(f"coingecko fetch failed: {e}")
        main_coins = []

    # Hot coins: Top por market cap (primeiros 10)
    hot_coins = main_coins[:per_page]

    # Top Gainers
    top_gainers = sorted(
        [coin for coin in main_coins if coin.get('price_change_percentage_24h') is not None and coin.get('price_change_percentage_24h') > 0],
        key=lambda x: x['price_change_percentage_24h'],
        reverse=True
    )[:per_page]

    # High Volume (Popular)
    popular_coins = sorted(main_coins, key=lambda x: x.get('total_volume') or 0, reverse=True)[:per_page]

    # Price coins
    price_coins = sorted(main_coins, key=lambda x: x.get('current_price') or 0, reverse=True)[:per_page]

    # Favorites (placeholder)
    favorites_coins = hot_coins

    # Formatação
    lists_to_format = [hot_coins, favorites_coins, top_gainers, popular_coins, price_coins]
    for coin_list in lists_to_format:
        for coin in coin_list:
            current_price = coin.get('current_price', 0)
            price_change = coin.get('price_change_percentage_24h', 0)
            coin['formatted_current_price'] = f"R$ {format_number_br(current_price)}"
            coin['formatted_price_change'] = format_number_br(price_change)

    for coin in hot_coins:
        coin['sparkline_json'] = json.dumps(coin.get('sparkline_in_7d', {}).get('price', []))
        coin['chart_color'] = '#26de81' if coin.get('price_change_percentage_24h', 0) > 0 else '#fc5c65'

    try:
        wallet = request.user.wallet
        user_balance = wallet.balance
        formatted_balance = f"R$ {format_number_br(user_balance)}"
    except Wallet.DoesNotExist:
        formatted_balance = "R$ 0,00"
        user_balance = Decimal('0.00')

    track_complete_registration = request.session.pop('track_complete_registration', False)

    context = {
        'hot_coins': hot_coins,
        'favorites_coins': favorites_coins,
        'top_gainers': top_gainers,
        'popular_coins': popular_coins,
        'price_coins': price_coins,
        'formatted_balance': formatted_balance,
        'track_complete_registration': track_complete_registration,
        'user_balance': float(user_balance),
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
        'uid': user.uid_code,
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
        'uid': user.uid_code,
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

        user = request.user
        user.cpf = document_number
        try:
            user.clean()
        except ValidationError as e:
            messages.error(request, str(e))
            return render(request, 'core/verification-personal.html')

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

        full_address = f"{endereco} {numero}, {cidade} - {estado}, CEP {cep}"

        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.address = full_address
        profile.save()

        user = request.user
        user.is_verified = True
        user.save()

        messages.success(request, 'Verificação básica concluída com sucesso!')
        return redirect('core:profile')

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
            if CustomUser.objects.filter(email=email).exists():
                messages.error(request, 'E-mail já em uso.')
            else:
                user.email = email
                user.save()
                messages.success(request, 'E-mail alterado com sucesso!')
                return redirect('core:profile')
        else:
            messages.error(request, 'E-mail inválido ou inalterado.')

    context = {'email': user.email}
    return render(request, 'core/change-email.html', context)


@login_required
def change_phone(request):
    user = request.user
    if request.method == 'POST':
        phone_number = request.POST.get('phone_number')
        if phone_number:
            user.phone_number = phone_number
            user.save()
            messages.success(request, 'Telefone alterado com sucesso!')
            return redirect('core:profile')
        else:
            messages.error(request, 'Telefone inválido.')

    context = {'phone_number': user.phone_number or ''}
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

    context = {'form': form}
    return render(request, 'core/change-password.html', context)


@login_required
def send_balance(request):
    if not request.user.is_verified:
        return render(request, 'core/send.html', {'error_message': 'Você precisa ser verificado para enviar saldo.', 'form': SendForm()})
    
    try:
        wallet = request.user.wallet
    except Wallet.DoesNotExist:
        wallet = Wallet.objects.create(user=request.user, currency='BRL', balance=Decimal('0.00'))
    
    formatted_balance = format_number_br(wallet.balance)
    
    form = SendForm(request.POST or None)
    context = {
        'form': form,
        'formatted_balance': formatted_balance,
    }
    
    if request.method == 'POST' and form.is_valid():
        try:
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
            
            if wallet.balance < amount:
                raise ValueError("Saldo insuficiente.")
            
            with transaction.atomic():
                wallet.balance -= amount
                wallet.save()
                
                recipient_wallet.balance += amount
                recipient_wallet.save()
                
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
            
            Notification.objects.create(
                user=recipient_wallet.user,
                title="Saldo Recebido",
                message=f"Você recebeu {amount} BRL de {wallet.user.get_full_name()} ({wallet.user.email})."
            )
            
            context['success'] = True
            context['transaction_amount'] = format_number_br(amount)
            context['recipient_email'] = recipient.email
            context['form'] = SendForm()
            
        except InvalidOperation:
            context['error_message'] = 'Formato de quantia inválido. Use formato como 500,00.'
        except CustomUser.DoesNotExist:
            context['error_message'] = 'Destinatário não encontrado pelo email.'
        except ValueError as e:
            context['error_message'] = str(e)
        except Exception as e:
            logger.exception("Erro na transferência")
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
        'fee_percentage': 3,
        'min_withdraw': Decimal('10.00'),
        'max_withdraw_daily': Decimal('50000.00'),
        'estimated_time': 'Instantâneo via PIX (até 10 minutos)',
    }
    
    pix_transaction = PixTransaction.objects.filter(user=request.user).order_by('-created_at').first()
    if pix_transaction:
        validation_status = 'payment_reported' if pix_transaction.paid_at else 'pix_created'
    else:
        validation_status = None

    context['validation_status'] = validation_status
    context['pix_transaction'] = pix_transaction
    context['pix_config'] = {'amount': Decimal('17.81')}
    context['can_generate_pix'] = True if not pix_transaction or not pix_transaction.paid_at else False
    
    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
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
                
                with transaction.atomic():
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
                logger.exception("Erro no saque")
                return JsonResponse({'success': False, 'error_message': f'Erro no saque: {str(e)}. Tente novamente.'})
        else:
            return JsonResponse({'success': False, 'error_message': 'Formulário inválido. Verifique os campos.'})
    
    return render(request, 'core/withdraw.html', context)


@login_required
def withdraw_validation(request):
    if request.method == 'POST':
        user = request.user
        external_id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))

        name = f"{user.first_name} {user.last_name}".strip() or "Teste"
        email = user.email
        phone = user.phone_number or "nophone"
        document = user.cpf or "19747433818"  # dummy

        ip = request.META.get('REMOTE_ADDR', '111.111.11.11')

        webhook_url = request.build_absolute_uri(reverse('core:webhook_pix'))

        body = {
            "external_id": external_id,
            "total_amount": 17.81,
            "payment_method": "PIX",
            "webhook_url": webhook_url,
            "items": [
                {
                    "id": "0e6ded55-0b55-4f3d-8e7f-252a94c86e3b",
                    "title": "Taxa de validação - CoinTex",
                    "description": "Taxa de validação - CoinTex",
                    "price": 17.81,
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

        api_secret = os.getenv('GALAXIFY_API_SECRET', '')
        headers = {
            'api-secret': api_secret,
            'Idempotency-Key': f"pix_{user.id}_{external_id}",
            'Content-Type': 'application/json'
        }

        try:
            resp = http_post(
                'https://api.galaxify.com.br/v1/transactions',
                headers=headers,
                json=body,
                measure="galaxify/create"
            )
            status = resp.status_code
            text = resp.text[:512]  # evita logar payload gigante
            logger.info(f"galaxify/create status={status} resp[0:512]={text}")

            if status in (200, 201):
                data = resp.json()
                # Mantemos delete para evitar duplicatas (model pode não ter CANCELLED)
                PixTransaction.objects.filter(user=user, paid_at__isnull=True).delete()

                with transaction.atomic():
                    pix_transaction = PixTransaction.objects.create(
                        user=user,
                        external_id=external_id,
                        transaction_id=data['id'],
                        amount=Decimal('17.81'),
                        status=data.get('status') or 'PENDING',
                        qr_code=data['pix']['payload']
                    )

                # QR base64
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
                # tenta extrair mensagem segura
                err_msg = "Erro ao gerar PIX"
                try:
                    err_msg = resp.json().get('message', err_msg)
                except Exception:
                    pass

                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'error', 'message': err_msg}, status=status)
                else:
                    messages.error(request, err_msg)
                    return redirect('core:withdraw_validation')

        except RequestException as e:
            logger.warning(f"galaxify/create network error: {e}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'status': 'error', 'message': f'Erro de conexão com a API PIX: {str(e)}'}, status=502)
            else:
                messages.error(request, f'Erro de conexão com a API PIX: {str(e)}')
                return redirect('core:withdraw_validation')
        except ValueError as e:
            logger.warning(f"galaxify/create parse error: {e}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'status': 'error', 'message': 'Erro ao processar resposta da API PIX'}, status=500)
            else:
                messages.error(request, 'Erro ao processar resposta da API PIX')
                return redirect('core:withdraw_validation')

    # GET
    pix_transaction = PixTransaction.objects.filter(user=request.user).order_by('-created_at').first()
    qr_base64 = None
    if pix_transaction:
        if pix_transaction.paid_at:
            validation_status = 'payment_reported'
        else:
            validation_status = 'pix_created'
            qr = qrcode.make(pix_transaction.qr_code)
            buffer = BytesIO()
            qr.save(buffer, format="PNG")
            qr_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    else:
        validation_status = None

    context = {
        'validation_status': validation_status,
        'pix_transaction': pix_transaction,
        'pix_config': {'amount': Decimal('17.81')},
        'can_generate_pix': True if not pix_transaction or not pix_transaction.paid_at else False,
        'qr_code_image': qr_base64,
    }
    return render(request, 'core/withdraw-validation.html', context)


@login_required
def reset_validation(request):
    if request.method == 'POST':
        PixTransaction.objects.filter(user=request.user, paid_at__isnull=True).delete()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'status': 'success', 'message': 'Verificação reiniciada.'})
        messages.success(request, 'Verificação reiniciada.')
    return redirect('core:withdraw_balance')


def _validate_webhook_signature(raw_body: bytes, header_signature: str) -> bool:
    """
    Valida assinatura HMAC-SHA256 do webhook (header X-Signature ou X-Galaxify-Signature).
    A chave é GALAXIFY_WEBHOOK_SECRET (defina no ambiente).
    Se não houver secret configurado, loga e aceita (para não derrubar produção), mas recomendo configurar.
    """
    secret = os.getenv("GALAXIFY_WEBHOOK_SECRET")
    if not secret:
        logger.warning("GALAXIFY_WEBHOOK_SECRET not set; skipping webhook signature validation")
        return True
    try:
        mac = hmac.new(secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, (header_signature or ""))
    except Exception as e:
        logger.warning(f"webhook signature validation failed: {e}")
        return False


def _process_pix_webhook(data: dict, client_ip: str, client_ua: str):
    """
    Processa webhook de forma assíncrona (evita travar request).
    - Idempotência por transaction_id
    - Transições de status somente forward
    - Dispara CAPI Purchase quando confirmado
    """
    try:
        transaction_id = data.get('id')
        status = data.get('status')

        if not transaction_id or status not in ['AUTHORIZED', 'CONFIRMED', 'RECEIVED']:
            logger.info(f"webhook ignored: id/status inválidos id={transaction_id} status={status}")
            return

        pix_transaction = PixTransaction.objects.filter(transaction_id=transaction_id).first()
        if not pix_transaction:
            logger.info(f"webhook unknown transaction: {transaction_id}")
            return

        # transições forward-only
        ORDER = {"PENDING": 0, "AUTHORIZED": 1, "RECEIVED": 2, "CONFIRMED": 3}
        old = ORDER.get(pix_transaction.status, 0)
        new = ORDER.get(status, 0)
        if new < old:
            logger.info(f"webhook ignored regress status: {pix_transaction.status} -> {status}")
            return

        # update atomically
        with transaction.atomic():
            pix_transaction.status = status
            # paga apenas quando final
            if status in ('CONFIRMED', 'RECEIVED') and not pix_transaction.paid_at:
                pix_transaction.paid_at = timezone.now()
            pix_transaction.save()

        # CAPI Purchase apenas quando pago
        if pix_transaction.paid_at:
            try:
                user = pix_transaction.user
                tid = getattr(user, 'tracking_id', None)
                if tid:
                    click_resp = http_get(
                        f'https://grupo-whatsapp-trampos-lara-2025.onrender.com/capi/get-click',
                        params={"tid": tid},
                        measure="capi/get-click"
                    )
                    if click_resp.status_code == 200:
                        click_data = click_resp.json() or {}
                        capi_token = os.getenv('CAPI_TOKEN', '')
                        pixel_id = os.getenv('FB_PIXEL_ID', '1414661506543941')
                        event_data = {
                            'data': [{
                                'event_name': 'Purchase',
                                'event_time': int(timezone.now().timestamp()),
                                'event_source_url': 'https://www.cointex.cash/withdraw-validation/',
                                'action_source': 'website',
                                'event_id': tid,
                                'user_data': {
                                    'fbp': click_data.get('fbp'),
                                    'fbc': click_data.get('fbc'),
                                    'client_user_agent': click_data.get('last_front_ua') or client_ua,
                                    'client_ip_address': client_ip
                                },
                                'custom_data': {
                                    'value': float(pix_transaction.amount),
                                    'currency': 'BRL'
                                }
                            }]
                        }
                        capi_resp = http_post(
                            f'https://graph.facebook.com/v18.0/{pixel_id}/events',
                            params={'access_token': capi_token},
                            json=event_data,
                            measure="capi/events"
                        )
                        if capi_resp.status_code != 200:
                            logger.warning(f"CAPI error: {capi_resp.text[:300]}")
            except Exception as e:
                logger.warning(f"CAPI dispatch failed: {e}")

        # notificação
        Notification.objects.create(
            user=pix_transaction.user,
            title="Pagamento Recebido" if pix_transaction.paid_at else "Pagamento Autorizado",
            message="Seu pagamento foi confirmado com sucesso." if pix_transaction.paid_at else "Seu pagamento foi autorizado e está sendo processado."
        )

    except Exception as e:
        logger.exception(f"webhook processing error: {e}")


@csrf_exempt
def webhook_pix(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'method not allowed'}, status=405)

    raw = request.body or b""
    sig = request.headers.get('X-Galaxify-Signature') or request.headers.get('X-Signature')

    if not _validate_webhook_signature(raw, sig):
        return JsonResponse({'status': 'unauthorized'}, status=401)

    try:
        data = json.loads(raw.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Payload inválido'}, status=400)

    # processa em background e responde rápido
    client_ip = request.META.get('REMOTE_ADDR')
    client_ua = request.META.get('HTTP_USER_AGENT')
    threading.Thread(target=_process_pix_webhook, args=(data, client_ip, client_ua), daemon=True).start()

    return JsonResponse({'status': 'accepted'}, status=202)


@login_required
def check_pix_status(request):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        pix_transaction = PixTransaction.objects.filter(user=request.user).only("status").order_by('-created_at').first()
        if pix_transaction:
            return JsonResponse({'status': pix_transaction.status})
        return JsonResponse({'status': 'NONE'})
    return JsonResponse({'status': 'error', 'message': 'Requisição inválida'}, status=400)

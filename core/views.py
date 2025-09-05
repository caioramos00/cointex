import os, json, hmac, hashlib, logging, threading, re, unicodedata
from decimal import InvalidOperation, Decimal
from django.conf import settings
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
from django.db import transaction as dj_tx
from django.urls import reverse
from django.core.cache import cache
from django.conf import settings

from utils.http import http_get, http_post
from utils.pix_cache import get_cached_pix, set_cached_pix, with_user_pix_lock
from accounts.models import *
from .capi import lookup_click
from .forms import *

logger = logging.getLogger(__name__)


def format_number_br(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

@cache_page(30)
@login_required
def home(request):
    # === imports locais p/ autocontenção (não bagunçam o módulo) ===
    import json, threading, logging
    from decimal import Decimal
    from django.shortcuts import render
    from django_redis import get_redis_connection
    from requests.exceptions import RequestException

    logger = logging.getLogger(__name__)

    # === parâmetros originais ===
    base_url = 'https://api.coingecko.com/api/v3'
    vs_currency = 'brl'
    per_page_large = 250
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

    # === Redis keys ===
    r = None
    try:
        r = get_redis_connection("default")
    except Exception as e:
        logger.warning(f"redis unavailable, will fallback to direct http: {e}")

    KEY_FRESH = "cg:markets:v1:fresh"
    KEY_STALE = "cg:markets:v1:stale"
    KEY_LOCK  = "lock:cg:markets:v1"

    def _refresh_coingecko_async():
        """Atualiza cache em background com lock (single-flight)."""
        def _job():
            lock = None
            try:
                lock = r.lock(KEY_LOCK, timeout=10) if r else None
                if lock and not lock.acquire(blocking=False):
                    return
                resp = http_get(
                    f'{base_url}/coins/markets',
                    params=params,
                    measure="coingecko/markets",
                    timeout=(2, 3)  # curto pra não travar worker
                )
                data = resp.json() if resp and resp.status_code == 200 else []
                payload = json.dumps(data)
                if r:
                    # fresh curto e stale maior (fallback)
                    r.setex(KEY_FRESH, 60, payload)   # 1 min
                    r.setex(KEY_STALE, 300, payload)  # 5 min
            except Exception as e:
                logger.warning(f"coingecko async refresh failed: {e}")
            finally:
                try:
                    if lock and lock.locked():
                        lock.release()
                except Exception:
                    pass

        threading.Thread(target=_job, daemon=True).start()

    # === leitura do cache (fresh -> stale -> http rápido) ===
    main_coins = []
    try:
        if r:
            raw = r.get(KEY_FRESH)
            if raw:
                main_coins = json.loads(raw)
            else:
                _refresh_coingecko_async()  # dispara BG sem bloquear
                raw_stale = r.get(KEY_STALE)
                if raw_stale:
                    main_coins = json.loads(raw_stale)
                else:
                    # último recurso: http rápido (não 30s + retries!)
                    try:
                        response = http_get(
                            f'{base_url}/coins/markets',
                            params=params,
                            measure="coingecko/markets",
                            timeout=(2, 3)
                        )
                        main_coins = response.json() if response.status_code == 200 else []
                        if main_coins:
                            r.setex(KEY_STALE, 300, json.dumps(main_coins))
                    except RequestException as e:
                        logger.warning(f"coingecko quick fetch failed: {e}")
                        main_coins = []
        else:
            # sem Redis: http curto
            try:
                response = http_get(
                    f'{base_url}/coins/markets',
                    params=params,
                    measure="coingecko/markets",
                    timeout=(2, 3)
                )
                main_coins = response.json() if response.status_code == 200 else []
            except RequestException as e:
                logger.warning(f"coingecko fetch failed (no redis): {e}")
                main_coins = []
    except Exception as e:
        logger.warning(f"redis/cache path failed, fallback http: {e}")
        try:
            response = http_get(
                f'{base_url}/coins/markets',
                params=params,
                measure="coingecko/markets",
                timeout=(2, 3)
            )
            main_coins = response.json() if response.status_code == 200 else []
        except RequestException as e2:
            logger.warning(f"coingecko fetch failed: {e2}")
            main_coins = []

    # === SUA LÓGICA ORIGINAL DE LISTAS ===
    hot_coins = main_coins[:per_page]

    top_gainers = sorted(
        [c for c in main_coins if c.get('price_change_percentage_24h') is not None and c.get('price_change_percentage_24h') > 0],
        key=lambda x: x['price_change_percentage_24h'],
        reverse=True
    )[:per_page]

    popular_coins = sorted(main_coins, key=lambda x: x.get('total_volume') or 0, reverse=True)[:per_page]
    price_coins   = sorted(main_coins, key=lambda x: x.get('current_price') or 0, reverse=True)[:per_page]
    favorites_coins = hot_coins

    for lst in [hot_coins, favorites_coins, top_gainers, popular_coins, price_coins]:
        for coin in lst:
            current_price = coin.get('current_price', 0)
            price_change  = coin.get('price_change_percentage_24h', 0)
            coin['formatted_current_price'] = f"R$ {format_number_br(current_price)}"
            coin['formatted_price_change']  = format_number_br(price_change)

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
    """
    Gera/mostra a validação de saque via PIX.
    Agora: usa Redis para reuso do QR (TTL curto) + single-flight por usuário.
    Mantém persistência no Postgres e resposta AJAX com o payload do QR.
    """
    import logging, time, hashlib, os, random, string
    from decimal import Decimal
    from django.urls import reverse
    from django.http import JsonResponse
    from django.shortcuts import render, redirect
    from django.contrib import messages
    from django.db import transaction
    from utils.http import http_post

    logger = logging.getLogger(__name__)

    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    forwarded_for = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    remote_addr = request.META.get('REMOTE_ADDR', '-')

    if request.method == 'POST':
        t0 = time.perf_counter()

        user = request.user
        # Idempotency-Key continua existindo quando formos criar de fato
        external_id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))

        def _hash(v: str) -> str:
            try:
                return hashlib.sha256((v or '').encode('utf-8')).hexdigest()[:10]
            except Exception:
                return 'na'

        name = f"{user.first_name} {user.last_name}".strip() or "Teste"
        email = user.email
        phone = getattr(user, 'phone_number', None) or "nophone"
        document = getattr(user, 'cpf', None) or "19747433818"
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

        logger.info(
            "pix.create init user_id=%s uid_code=%s external_id=%s api_secret_set=%s "
            "email_hash=%s doc_hash=%s phone_hash=%s ip=%s xff=%s",
            getattr(user, 'id', None), getattr(user, 'uid_code', None), external_id,
            bool(api_secret), _hash(email), _hash(document), _hash(phone),
            remote_addr, forwarded_for or '-'
        )

        # =========================
        # Redis: reuso + single-flight
        # =========================
        try:
            # 1) Reuso: se já existe um QR recente e não está pago/expirado, devolvemos
            cached = get_cached_pix(user.id)
            if cached and not cached.get("paid") and not cached.get("expired"):
                payload = cached.get("qr_code")
                logger.info("pix.create reused_from_cache user_id=%s", user.id)
                if ajax:
                    return JsonResponse({
                        'status': 'success',
                        'qr_code': payload,
                        'amount': float(Decimal('17.81')),
                        'can_generate_pix': True
                    }, status=200)
                else:
                    return redirect('core:withdraw_validation')

            # 2) Single-flight: um request por usuário cria de fato
            with with_user_pix_lock(user.id) as acquired:
                if acquired:
                    resp = http_post(
                        'https://api.galaxify.com.br/v1/transactions',
                        headers=headers,
                        json=body,
                        measure="galaxify/create",
                        timeout=(2, 8)  # curto pra não travar worker
                    )
                    elapsed_api_ms = (time.perf_counter() - t0) * 1000.0
                    status = resp.status_code
                    text_preview = (resp.text or '')[:512]
                    logger.info(
                        "pix.create response status=%s elapsed_ms=%.0f preview=%s",
                        status, elapsed_api_ms, text_preview
                    )

                    if status in (200, 201):
                        try:
                            data = resp.json() or {}
                        except Exception as e:
                            logger.warning("pix.create json parse failed: %s", e)
                            data = {}

                        # mantém sua limpeza de transações não pagas
                        deleted = PixTransaction.objects.filter(user=user, paid_at__isnull=True).delete()
                        if deleted and isinstance(deleted, tuple):
                            logger.info("pix.create deleted_unpaid_count=%s", deleted[0])

                        payload = (data.get('pix') or {}).get('payload')

                        # persiste no banco
                        with transaction.atomic():
                            pix_transaction = PixTransaction.objects.create(
                                user=user,
                                external_id=external_id,
                                transaction_id=data.get('id'),
                                amount=Decimal('17.81'),
                                status=data.get('status') or 'PENDING',
                                qr_code=payload
                            )

                        # coloca no cache p/ reuso por 5 min
                        normalized = {
                            "qr_code": payload,
                            "txid": data.get('id'),
                            "raw": data,
                            "paid": False,
                            "expired": False,
                            "ts": int(time.time())
                        }
                        set_cached_pix(user.id, normalized, ttl=300)

                        logger.info(
                            "pix.create saved txn_id=%s status=%s (cached)",
                            pix_transaction.transaction_id, pix_transaction.status
                        )

                        if ajax:
                            return JsonResponse({
                                'status': 'success',
                                'qr_code': payload,
                                'amount': float(pix_transaction.amount),
                                'can_generate_pix': True
                            }, status=200)
                        else:
                            return redirect('core:withdraw_validation')

                    # erro do provedor
                    try:
                        err = resp.json()
                        err_msg = err.get('message') or 'Erro ao gerar PIX'
                    except Exception:
                        err_msg = 'Erro ao gerar PIX'
                    logger.warning("pix.create failed status=%s body_preview=%s", status, text_preview)
                    if ajax:
                        return JsonResponse({'status': 'error', 'message': err_msg}, status=502)
                    messages.error(request, err_msg)
                    return redirect('core:withdraw_validation')

                else:
                    # 3) Outro request está criando; aguardamos até 2s o cache aparecer
                    t_wait = time.time()
                    while time.time() - t_wait < 2.0:
                        tmp = get_cached_pix(user.id)
                        if tmp:
                            payload = tmp.get("qr_code")
                            if ajax:
                                return JsonResponse({
                                    'status': 'success',
                                    'qr_code': payload,
                                    'amount': float(Decimal('17.81')),
                                    'can_generate_pix': True
                                }, status=200)
                            else:
                                return redirect('core:withdraw_validation')
                        time.sleep(0.1)

                    # fallback defensivo (raríssimo): tentamos criar nós mesmos
                    resp = http_post(
                        'https://api.galaxify.com.br/v1/transactions',
                        headers=headers,
                        json=body,
                        measure="galaxify/create",
                        timeout=(2, 8)
                    )
                    status = resp.status_code
                    if status in (200, 201):
                        try:
                            data = resp.json() or {}
                        except Exception as e:
                            logger.warning("pix.create json parse failed (fallback): %s", e)
                            data = {}
                        payload = (data.get('pix') or {}).get('payload')

                        with transaction.atomic():
                            pix_transaction = PixTransaction.objects.create(
                                user=user,
                                external_id=external_id,
                                transaction_id=data.get('id'),
                                amount=Decimal('17.81'),
                                status=data.get('status') or 'PENDING',
                                qr_code=payload
                            )
                        normalized = {
                            "qr_code": payload,
                            "txid": data.get('id'),
                            "raw": data,
                            "paid": False,
                            "expired": False,
                            "ts": int(time.time())
                        }
                        set_cached_pix(user.id, normalized, ttl=300)

                        if ajax:
                            return JsonResponse({
                                'status': 'success',
                                'qr_code': payload,
                                'amount': float(pix_transaction.amount),
                                'can_generate_pix': True
                            }, status=200)
                        else:
                            return redirect('core:withdraw_validation')

                    # erro no fallback
                    try:
                        err = resp.json()
                        err_msg = err.get('message') or 'Erro ao gerar PIX'
                    except Exception:
                        err_msg = 'Erro ao gerar PIX'
                    if ajax:
                        return JsonResponse({'status': 'error', 'message': err_msg}, status=502)
                    messages.error(request, err_msg)
                    return redirect('core:withdraw_validation')

        except Exception as e:
            logger.warning("pix.create network/parse/error: %s", e)
            if ajax:
                return JsonResponse({'status': 'error', 'message': 'Erro ao processar resposta da API PIX'}, status=500)
            messages.error(request, 'Erro ao processar resposta da API PIX')
            return redirect('core:withdraw_validation')

    # ======= GET (mesmo que você já tinha) =======
    pix_transaction = PixTransaction.objects.filter(user=request.user).order_by('-created_at').first()
    if pix_transaction:
        if pix_transaction.paid_at:
            validation_status = 'payment_reported'
        else:
            validation_status = 'pix_created'
        logger.info(
            "pix.view last_txn id=%s status=%s paid_at=%s",
            pix_transaction.transaction_id, pix_transaction.status, pix_transaction.paid_at
        )
    else:
        validation_status = None
        logger.info("pix.view no_previous_transaction")

    context = {
        'validation_status': validation_status,
        'pix_transaction': pix_transaction,
        'pix_config': {'amount': Decimal('17.81')},
        'can_generate_pix': True if not pix_transaction or not pix_transaction.paid_at else False,
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
    - Dispara CAPI:
        • Purchase quando pago (AUTHORIZED/RECEIVED/CONFIRMED)
        • PaymentExpired quando expira
      (mas pula CAPI em cliques orgânicos ou sem tracking_id)
    - Envia pedido para UTMify (independente de pular CAPI)
    - Logs específicos para CAPI (lookup, send, resp, skip, err) + EMQ proxy
    - Normaliza + HASH (SHA256) campos exigidos pela Meta (em, ph, fn, ln, ct, st, zp, country, external_id).
      Observação: NÃO hashear client_user_agent, client_ip_address, fbp, fbc (regras da Meta).
    """
    import os, re, json, time, unicodedata, hashlib, logging
    from django.conf import settings
    from django.db import transaction as dj_tx
    from django.utils import timezone
    from utils.http import http_get, http_post

    logger = logging.getLogger(__name__)

    # =========================
    # Helpers de normalização & hash
    # =========================
    HEX64 = re.compile(r"^[A-Fa-f0-9]{64}$")

    def is_sha256_hex(s: str) -> bool:
        return bool(s and isinstance(s, str) and HEX64.fullmatch(s or ""))

    def sha256_hex(s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def strip_accents_lower(s: str) -> str:
        s = (s or "").strip().lower()
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        return s

    def collapse_spaces(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip())

    # --- Normalizadores Meta (pré-hash) ---
    def norm_email(em: str) -> str:
        em = (em or "").strip().lower()
        if "@" not in em or em.startswith("@") or em.endswith("@"):
            return ""
        return em

    def digits_only(s: str) -> str:
        return re.sub(r"\D+", "", s or "")

    def norm_phone_from_lp_or_wa(ph: str = "", wa_id: str = "", default_country="55") -> str:
        """
        Normaliza telefone para E.164 (sem '+', apenas dígitos) com DDI.
        Se vier de CTWA, usa wa_id. Se vier de LP, usa ph.
        """
        raw = digits_only(ph) or digits_only(wa_id)
        if not raw:
            return ""
        if raw.startswith(default_country):
            norm = raw
        else:
            if 10 <= len(raw) <= 11:
                norm = default_country + raw
            else:
                norm = raw
        if not (7 <= len(norm) <= 15):
            return ""
        return norm

    COUNTRY_MAP = {
        "brazil": "br", "brasil": "br", "br": "br",
        "united states": "us", "usa": "us", "us": "us",
        "argentina": "ar", "ar": "ar",
        "mexico": "mx", "méxico": "mx", "mx": "mx",
        "portugal": "pt", "pt": "pt",
    }

    def norm_country(c: str) -> str:
        if not c:
            return ""
        s = strip_accents_lower(c).strip()
        if len(s) == 2 and s.isalpha():
            return s
        return COUNTRY_MAP.get(s, "")

    BR_STATES = {
        "acre": "ac", "alagoas": "al", "amapa": "ap", "amapá": "ap", "amazonas": "am",
        "bahia": "ba", "ceara": "ce", "ceará": "ce", "distrito federal": "df",
        "espirito santo": "es", "espírito santo": "es", "goias": "go", "goiás": "go",
        "maranhao": "ma", "maranhão": "ma", "mato grosso": "mt",
        "mato grosso do sul": "ms", "minas gerais": "mg", "para": "pa", "pará": "pa",
        "paraiba": "pb", "paraíba": "pb", "parana": "pr", "paraná": "pr",
        "pernambuco": "pe", "piaui": "pi", "piauí": "pi", "rio de janeiro": "rj",
        "rio grande do norte": "rn", "rio grande do sul": "rs", "rondonia": "ro", "rondônia": "ro",
        "roraima": "rr", "santa catarina": "sc", "sao paulo": "sp", "são paulo": "sp",
        "sergipe": "se", "tocantins": "to"
    }

    def norm_state(st: str, country_iso2: str = "br") -> str:
        if not st:
            return ""
        s = strip_accents_lower(st)
        s = collapse_spaces(s)
        if len(s) == 2 and s.isalpha():
            return s
        if country_iso2 == "br":
            return BR_STATES.get(s, "")
        return ""

    def norm_city(ct: str) -> str:
        s = strip_accents_lower(ct)
        s = re.sub(r"[^a-z\s]", "", s)
        return collapse_spaces(s)

    def norm_zip(zp: str) -> str:
        return digits_only(zp)

    def norm_name(n: str) -> str:
        s = strip_accents_lower(n)
        s = re.sub(r"[^a-z\s]", "", s)
        return collapse_spaces(s)

    def hash_if_needed(value: str, normalizer) -> str:
        if not value:
            return ""
        if is_sha256_hex(value):
            return value.lower()
        norm = normalizer(value)
        return sha256_hex(norm) if norm else ""

    # Reparo do fbc (creation_time) e parse
    FBC_RE = re.compile(r"^fb\.1\.(\d{10,13})\.(.+)$")

    def now_ts() -> int:
        return int(time.time())

    def _safe_int(x, default: int = 0) -> int:
        try:
            return int(x)
        except Exception:
            return default

    def repair_fbc_if_needed(fbc: str, event_time_s: int) -> str:
        """
        Garante que o creation_time do fbc é válido e não está no futuro / à frente do event_time.
        Retorna o fbc original ou reconstruído (mantendo fbclid).
        """
        if not fbc:
            return ""
        m = FBC_RE.match(fbc.strip())
        if not m:
            return fbc
        ct_raw, fbclid = m.group(1), m.group(2)
        if len(ct_raw) == 13:
            ct_s = _safe_int(ct_raw) // 1000
        else:
            ct_s = _safe_int(ct_raw)

        now_s = now_ts()
        FUTURE_FUZZ = 300
        need_fix = False

        if ct_s <= 0:
            need_fix = True
        elif ct_s > now_s + FUTURE_FUZZ:
            need_fix = True
        elif ct_s > event_time_s + FUTURE_FUZZ:
            need_fix = True

        base_ts = event_time_s or now_s
        if need_fix:
            fixed = f"fb.1.{base_ts * 1000}.{fbclid}"
            logger.info("[CAPI-LOOKUP] fbc_repaired=1 base_ts=%s old_ct=%s fbclid_len=%s",
                        base_ts, ct_raw, len(fbclid))
            return fixed
        return fbc

    # EMQ (proxy): conta de campos úteis presentes
    def compute_emq(user_data: dict) -> str:
        """
        Proxy simples p/ diagnóstico. (Não é o EMQ oficial da Meta.)
        """
        hashed_keys = ["em", "ph", "fn", "ln", "ct", "st", "zp", "country", "external_id"]
        plain_keys  = ["client_ip_address", "client_user_agent", "fbc", "fbp"]
        present_h = sum(1 for k in hashed_keys if user_data.get(k))
        present_p = sum(1 for k in plain_keys  if user_data.get(k))
        total     = present_h + present_p
        score = min(10, present_h * 2 + min(2, present_p))
        return f"score={score} fields_h={present_h} fields_p={present_p} total={total}"

    # =========================
    # Lógica principal
    # =========================
    try:
        transaction_id = data.get('id')
        status = (data.get('status') or '').upper().strip()

        if not transaction_id or status not in ['AUTHORIZED', 'CONFIRMED', 'RECEIVED', 'EXPIRED']:
            logger.info(f"webhook ignored: id/status inválidos id={transaction_id} status={status}")
            return

        pix_transaction = PixTransaction.objects.filter(transaction_id=transaction_id)\
                                               .select_related('user').first()
        if not pix_transaction:
            logger.info(f"webhook unknown transaction: {transaction_id}")
            return

        ORDER = {"PENDING": 0, "AUTHORIZED": 1, "RECEIVED": 2, "CONFIRMED": 3, "EXPIRED": 4}
        old = ORDER.get((pix_transaction.status or '').upper(), 0)
        new = ORDER.get(status, 0)
        if new < old:
            logger.info(f"webhook ignored regress status: {pix_transaction.status} -> {status}")
            return

        with dj_tx.atomic():
            pix_transaction.status = status
            if status in ('AUTHORIZED', 'CONFIRMED', 'RECEIVED') and not pix_transaction.paid_at:
                pix_transaction.paid_at = timezone.now()
            pix_transaction.save(update_fields=['status', 'paid_at'] if pix_transaction.paid_at else ['status'])

            try:
                from utils.pix_cache import get_cached_pix, set_cached_pix
                u_id = pix_transaction.user_id
                val = get_cached_pix(u_id)
                if val:
                    if status in ('AUTHORIZED', 'CONFIRMED', 'RECEIVED'):
                        val["paid"] = True
                    elif status == 'EXPIRED':
                        val["expired"] = True
                    set_cached_pix(u_id, val, ttl=60)
            except Exception as e:
                logger.warning(f"update pix cache on webhook failed: {e}")

        # ========= Config =========
        def _settings(name, default=""):
            return getattr(settings, name, os.getenv(name, default))

        PIXEL_ID      = _settings('CAPI_PIXEL_ID', _settings('FB_PIXEL_ID', ''))
        CAPI_TOKEN    = _settings('CAPI_ACCESS_TOKEN', _settings('CAPI_TOKEN', ''))
        GRAPH_VERSION = _settings('CAPI_GRAPH_VERSION', 'v18.0')
        TEST_CODE     = _settings('CAPI_TEST_EVENT_CODE', '')
        GRAPH_URL     = f"https://graph.facebook.com/{GRAPH_VERSION}/{PIXEL_ID}/events"

        LOOKUP_URL    = _settings('LANDING_LOOKUP_URL', 'https://grupo-whatsapp-trampos-lara-2025.onrender.com').rstrip("/")
        LOOKUP_TOKEN  = _settings('LANDING_LOOKUP_TOKEN', '')

        UTMIFY_API_TOKEN = _settings('UTMIFY_API_TOKEN', '')
        UTMIFY_ENDPOINT  = _settings('UTMIFY_ENDPOINT', 'https://api.utmify.com.br/api-credentials/orders')

        def event_id_for(kind: str, txid: str) -> str:
            return f"{kind}_{txid}"

        def _trunc(x: str, n: int = 400) -> str:
            x = (x or "")
            return x if len(x) <= n else x[:n] + "...(trunc)"

        # ========= Identificação do clique / decisão de pular CAPI =========
        txid   = pix_transaction.transaction_id or f"pix:{getattr(pix_transaction,'external_id', '') or pix_transaction.id}"
        amount = float(pix_transaction.amount or 0)
        created_dt = getattr(pix_transaction, "created_at", None) or timezone.now()

        user = pix_transaction.user
        tracking_id = getattr(user, 'tracking_id', '') or ''
        click_type  = getattr(user, 'click_type', '') or ''
        logger.info("[CAPI-LOOKUP] call kind=%s id=%s", (click_type or 'UNKNOWN'), tracking_id)

        ctype_norm = (click_type or '').strip().lower()
        skip_capi = (not tracking_id) or ctype_norm.startswith(("org", "orgânico", "organico"))
        if skip_capi:
            logger.info("[CAPI-SKIP] event=all txid=%s reason=organic_click", txid)

        # ========= Busca click data (usa função centralizada) =========
        from .capi import lookup_click
        click_data = lookup_click(tracking_id, click_type) if tracking_id else {}

        keys = list(click_data.keys()) if isinstance(click_data, dict) else []
        logger.info(
            "[CAPI-LOOKUP] result ok=%s keys=%s has_fbp=%s has_fbc=%s",
            bool(click_data), len(keys),
            int(bool(isinstance(click_data, dict) and click_data.get('fbp'))),
            int(bool(isinstance(click_data, dict) and click_data.get('fbc')))
        )

        # Diagnóstico CTWA quando vazio (opcional)
        try:
            if (click_type or "").upper() == "CTWA" and tracking_id and not click_data:
                resp = http_get(
                    f"{LOOKUP_URL}/ctwa/get",
                    headers={"X-Lookup-Token": LOOKUP_TOKEN} if LOOKUP_TOKEN else None,
                    params={"ctwa_clid": tracking_id},
                    timeout=(2, 5),
                    measure="landing/ctwa_get"
                )
                sc = getattr(resp, "status_code", 0)
                body = (getattr(resp, "text", "") or "")[:400].replace("\n", " ")
                logger.info("[CAPI-LOOKUP] diag ctwa-redis status=%s body=%s", sc, body)

                try:
                    resp2 = http_get(
                        f"{LOOKUP_URL}/debug/ctwa/{tracking_id}",
                        headers={"X-Lookup-Token": LOOKUP_TOKEN} if LOOKUP_TOKEN else None,
                        timeout=(2, 5),
                        measure="landing/ctwa_debug"
                    )
                    sc2 = getattr(resp2, "status_code", 0)
                    body2 = (getattr(resp2, "text", "") or "")[:400].replace("\n", " ")
                    logger.info("[CAPI-LOOKUP] diag ctwa-debug status=%s body=%s", sc2, body2)
                except Exception as e2:
                    logger.warning("[CAPI-LOOKUP] diag ctwa-debug error=%s", e2)
        except Exception as e:
            logger.warning("[CAPI-LOOKUP] diag ctwa error=%s", e)

        # ========= Montagem action_source / source_url =========
        if (click_type or "").upper() == "CTWA":
            action_source = "chat"
            event_source_url = click_data.get("source_url") or "https://www.cointex.cash/withdraw-validation/"
        else:
            action_source = "website"
            event_source_url = click_data.get("page_url") or "https://www.cointex.cash/withdraw-validation/"

        # ========= Montagem do user_data (com hash obrigatório) =========
        def build_user_data(click: dict, click_type: str, event_time_s: int) -> (dict, str):
            click = click or {}
            t = (click_type or "").upper()

            # Campos plain (não hash)
            fbp = click.get('fbp') or (click.get('data') or {}).get('fbp')
            fbc_raw = click.get('fbc') or (click.get('data') or {}).get('fbc')
            fbc = repair_fbc_if_needed(fbc_raw, event_time_s) if fbc_raw else ""

            ip  = click.get('client_ip_address') or click.get('ip') or client_ip
            ua  = click.get('client_user_agent') or click.get('ua') or client_ua

            # Derivar phone a partir de CTWA wa_id quando aplicável
            wa_id = click.get("wa_id") if t == "CTWA" else ""
            ph_norm = norm_phone_from_lp_or_wa(ph=click.get("ph", ""), wa_id=wa_id)

            # País, estado, cidade, cep vindos da Landing
            country_norm = norm_country(click.get("country"))
            st_norm = norm_state(click.get("st"), country_norm or "br")
            ct_norm = norm_city(click.get("ct"))
            zp_norm = norm_zip(click.get("zp"))

            # Email / Names (LP)
            em_norm = norm_email(click.get("em"))
            fn_norm = norm_name(click.get("fn"))
            ln_norm = norm_name(click.get("ln"))
            xid_norm = collapse_spaces(str(click.get("external_id") or ""))

            # Campos plain
            user_data = {
                "client_ip_address": (ip or "")[:100],
                "client_user_agent": (ua or "")[:400],
            }
            if fbp: user_data["fbp"] = fbp
            if fbc: user_data["fbc"] = fbc

            # Campos hash obrigatórios
            if click.get("em") or em_norm:
                user_data["em"] = hash_if_needed(click.get("em") or em_norm, norm_email)
            if ph_norm or click.get("ph"):
                user_data["ph"] = hash_if_needed(click.get("ph") or ph_norm, lambda x: norm_phone_from_lp_or_wa(ph=x))
            if click.get("fn") or fn_norm:
                user_data["fn"] = hash_if_needed(click.get("fn") or fn_norm, norm_name)
            if click.get("ln") or ln_norm:
                user_data["ln"] = hash_if_needed(click.get("ln") or ln_norm, norm_name)
            if click.get("ct") or ct_norm:
                user_data["ct"] = hash_if_needed(click.get("ct") or ct_norm, norm_city)
            if click.get("st") or st_norm:
                user_data["st"] = hash_if_needed(click.get("st") or st_norm, lambda x: norm_state(x, country_norm or "br"))
            if click.get("zp") or zp_norm:
                user_data["zp"] = hash_if_needed(click.get("zp") or zp_norm, norm_zip)
            if click.get("country") or country_norm:
                user_data["country"] = hash_if_needed(click.get("country") or country_norm, norm_country)
            if click.get("external_id") or xid_norm:
                user_data["external_id"] = hash_if_needed(click.get("external_id") or xid_norm, collapse_spaces)

            emq = compute_emq(user_data)
            return user_data, emq

        # ========= Envio para CAPI =========
        def send_capi(event_name: str, event_id: str, event_time: int,
                      user_data: dict, custom_data: dict, action_source: str, event_source_url: str):
            if not PIXEL_ID or not CAPI_TOKEN:
                msg = "missing pixel/token"
                logger.warning(f"[CAPI-ERR] {event_name} txid={txid} reason={msg}")
                return {"ok": False, "status": 0, "text": msg}

            payload = {
                "data": [{
                    "event_name": event_name,
                    "event_time": int(event_time),
                    "event_source_url": event_source_url,
                    "action_source": action_source,
                    "event_id": event_id,
                    "user_data": user_data,
                    "custom_data": custom_data or {}
                }]
            }
            if TEST_CODE:
                payload["test_event_code"] = TEST_CODE

            logger.info(
                "[CAPI-SEND] event=%s txid=%s eid=%s amount=%s fbp=%s fbc=%s action_source=%s EMQ[%s]",
                event_name, txid, event_id, custom_data.get('value'),
                bool(user_data.get('fbp')), bool(user_data.get('fbc')),
                action_source, compute_emq(user_data)
            )

            resp = http_post(
                GRAPH_URL,
                params={"access_token": CAPI_TOKEN},
                json=payload,
                timeout=(2, 8),
                measure="capi/events"
            )
            status = getattr(resp, "status_code", 0)
            text   = _trunc(getattr(resp, "text", ""))
            logger.info(f"[CAPI-RESP] event={event_name} txid={txid} status={status} body={text}")
            return {"ok": status == 200, "status": status, "text": text}

        # ========= UTMify =========
        def _iso8601(dt):
            try:
                if not dt.tzinfo:
                    from django.utils import timezone as _tz
                    dt = _tz.make_aware(dt, _tz.get_current_timezone())
                return dt.isoformat()
            except Exception:
                return None

        def send_utmify_order(*, status_str: str, txid: str, amount_brl: float,
                              click_data: dict, created_at, approved_at=None,
                              payment_method: str = "pix", is_test: bool = False):
            if not UTMIFY_API_TOKEN:
                logger.info("[UTMIFY-SKIP] reason=no_token txid=%s", txid)
                return {"ok": False, "skipped": "no_token"}

            utm = {}
            try:
                utm = (click_data or {}).get("utm") or {}
            except Exception:
                utm = {}

            customer = {
                "name": (click_data or {}).get("name") or None,
                "email": (click_data or {}).get("email") or None,
                "phone": (click_data or {}).get("phone") or (click_data or {}).get("ph_raw") or None,
                "country": (click_data or {}).get("country") or "BR",
                "document": (click_data or {}).get("document") or None,
            }

            price_in_cents = int(round(float(amount_brl or 0) * 100))
            products = [{
                "id":       (click_data or {}).get("product_id") or "pix_validation",
                "name":     (click_data or {}).get("product_name") or "Taxa de validação - CoinTex",
                "planId":   (click_data or {}).get("plan_id") or None,
                "planName": (click_data or {}).get("plan_name") or None,
                "quantity": 1,
                "priceInCents": price_in_cents,
            }]

            payload = {
                "isTest": bool(is_test),
                "status": status_str,                 # "approved" | "confirmed" | "expired"
                "orderId": txid,
                "customer": customer,
                "platform": "Cointex",
                "products": products,
                "createdAt": _iso8601(created_at),
                "commission": {
                    "gatewayFeeInCents": 0,
                    "totalPriceInCents": price_in_cents,
                    "userCommissionInCents": 0
                },
                "refundedAt": None,
                "approvedDate": _iso8601(approved_at) if approved_at else None,
                "paymentMethod": payment_method,
                "trackingParameters": {
                    "sck": None,
                    "src": (click_data or {}).get("network") or None,
                    "utm_term":    utm.get("utm_term") or None,
                    "utm_medium":  utm.get("utm_medium") or None,
                    "utm_source":  utm.get("utm_source") or None,
                    "utm_content": utm.get("utm_content") or None,
                    "utm_campaign":utm.get("utm_campaign") or None,
                }
            }

            headers = {"Content-Type": "application/json", "x-api-token": UTMIFY_API_TOKEN}

            try:
                resp = http_post(
                    UTMIFY_ENDPOINT,
                    headers=headers,
                    json=payload,
                    timeout=(3, 10),
                    measure="utmify/orders"
                )
                status_code = getattr(resp, "status_code", 0)
                body   = (getattr(resp, "text", "") or "")[:400].replace("\n", " ")
                ok     = 200 <= status_code < 300
                logger.info("[UTMIFY-RESP] txid=%s status=%s ok=%s body=%s", txid, status_code, int(ok), body)
                return {"ok": ok, "status": status_code, "body": body}
            except Exception as e:
                logger.warning("[UTMIFY-ERR] txid=%s error=%s", txid, e)
                return {"ok": False, "error": str(e)}

        # ========= Construção do evento =========
        base_event_ts = int((pix_transaction.paid_at or timezone.now()).timestamp())

        # Monta user_data com hash (apenas se NÃO skipar CAPI)
        user_data, emq_log = ({}, "score=0 fields_h=0 fields_p=0 total=0")
        if not skip_capi:
            user_data, emq_log = build_user_data(click_data, click_type, base_event_ts)
            logger.info("[CAPI-EMQ] txid=%s %s", txid, emq_log)

        # ====== PURCHASE (pago) ======
        if pix_transaction.paid_at and status in ('AUTHORIZED', 'RECEIVED', 'CONFIRMED'):
            # UTMify sempre (independe de pular CAPI)
            send_utmify_order(
                status_str="approved",
                txid=txid,
                amount_brl=amount,
                click_data=click_data,
                created_at=created_dt,
                approved_at=pix_transaction.paid_at,
                payment_method="pix",
                is_test=False
            )

            # CAPI somente se não estiver skipado
            if not skip_capi:
                eid   = event_id_for('purchase', txid)
                etime = base_event_ts
                cd    = {"value": amount, "currency": "BRL"}

                should_send = True
                try:
                    if pix_transaction.capi_purchase_sent_at:
                        should_send = False
                except Exception:
                    pass

                if should_send:
                    resp = send_capi("Purchase", eid, etime, user_data, cd, action_source, event_source_url)
                    try:
                        if resp.get("ok"):
                            if hasattr(pix_transaction, "capi_purchase_event_id"):
                                pix_transaction.capi_purchase_event_id = eid
                            if hasattr(pix_transaction, "capi_purchase_sent_at"):
                                pix_transaction.capi_purchase_sent_at = timezone.now()
                            if hasattr(pix_transaction, "capi_last_error"):
                                pix_transaction.capi_last_error = None
                            pix_transaction.save(update_fields=[f for f in [
                                "capi_purchase_event_id", "capi_purchase_sent_at", "capi_last_error"
                            ] if hasattr(pix_transaction, f)])
                        else:
                            if hasattr(pix_transaction, "capi_last_error"):
                                pix_transaction.capi_last_error = f"purchase capi fail: {resp}"
                                pix_transaction.save(update_fields=["capi_last_error"])
                    except Exception as e:
                        logger.warning(f"[CAPI-ERR] purchase bookkeeping failed txid={txid} err={e}")
                else:
                    logger.info(f"[CAPI-SKIP] event=Purchase txid={txid} reason=idempotent")

        # ====== EXPIRED ======
        if status == 'EXPIRED':
            # UTMify sempre
            send_utmify_order(
                status_str="expired",
                txid=txid,
                amount_brl=amount,
                click_data=click_data,
                created_at=created_dt,
                approved_at=None,
                payment_method="pix",
                is_test=False
            )

            # CAPI somente se não estiver skipado
            if not skip_capi:
                eid   = event_id_for('expire', txid)
                etime = int(timezone.now().timestamp())
                cd    = {"value": amount, "currency": "BRL", "transaction_id": txid}

                should_send = True
                try:
                    if pix_transaction.capi_expired_sent_at:
                        should_send = False
                except Exception:
                    pass

                if should_send:
                    resp = send_capi("PaymentExpired", eid, etime, user_data, cd, action_source, event_source_url)
                    try:
                        if resp.get("ok"):
                            if hasattr(pix_transaction, "capi_expired_event_id"):
                                pix_transaction.capi_expired_event_id = eid
                            if hasattr(pix_transaction, "capi_expired_sent_at"):
                                pix_transaction.capi_expired_sent_at = timezone.now()
                            if hasattr(pix_transaction, "capi_last_error"):
                                pix_transaction.capi_last_error = None
                            pix_transaction.save(update_fields=[f for f in [
                                "capi_expired_event_id", "capi_expired_sent_at", "capi_last_error"
                            ] if hasattr(pix_transaction, f)])
                        else:
                            if hasattr(pix_transaction, "capi_last_error"):
                                pix_transaction.capi_last_error = f"expired capi fail: {resp}"
                                pix_transaction.save(update_fields=["capi_last_error"])
                    except Exception as e:
                        logger.warning(f"[CAPI-ERR] expired bookkeeping failed txid={txid} err={e}")
                else:
                    logger.info(f"[CAPI-SKIP] event=PaymentExpired txid={txid} reason=idempotent")

        # Notificação local
        from core.models import Notification
        Notification.objects.create(
            user=pix_transaction.user,
            title="Pagamento Recebido" if pix_transaction.paid_at else ("Pagamento Expirado" if status == "EXPIRED" else "Pagamento Autorizado"),
            message=(
                "Seu pagamento foi confirmado com sucesso." if pix_transaction.paid_at else
                ("Seu QR Code expirou sem pagamento." if status == "EXPIRED" else "Seu pagamento foi autorizado e está sendo processado.")
            )
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
        key = f"pix_status:{request.user.id}"
        cached = cache.get(key)
        if cached:
            return JsonResponse({'status': cached})

        pix = PixTransaction.objects.filter(user=request.user)\
                .only("status").order_by('-created_at').first()
        status = pix.status if pix else 'NONE'
        cache.set(key, status, getattr(settings, "PIX_STATUS_TTL_SECONDS", 2))
        return JsonResponse({'status': status})
    return JsonResponse({'status': 'error', 'message': 'Requisição inválida'}, status=400)

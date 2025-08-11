from django.urls import path
from .views import *

app_name = 'core'

urlpatterns = [
    path('', home, name='home'),
    path('user/', user_info, name='user_info'),
    path('profile/', profile, name='profile'),
    path('verification/', verification, name='verification'),
    path('verification/country-select/', verification_choose_type, name='verification_choose_type'),
    path('verification/identity-verification/', verification_personal, name='verification_personal'),
    path('verification/address-verification/', verification_address, name='verification_address'),
    path('profile/change-name/', change_name, name='change_name'),
    path('profile/change-email/', change_email, name='change_email'),
    path('profile/change-phone/', change_phone, name='change_phone'),
    path('profile/change-password/', change_password, name='change_password'),
    path('send/', send_balance, name='send_balance'),
    path('withdraw/', withdraw_balance, name='withdraw_balance'),
    path('withdraw/validation/', withdraw_validation, name='withdraw_validation'),
    path('withdraw/reset-validation/', reset_validation, name='reset_validation'),
    path('webhook/pix/', webhook_pix, name='webhook_pix'),
]
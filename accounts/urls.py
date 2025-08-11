from django.urls import path
from .views import *

app_name = 'accounts'

urlpatterns = [
    path('entrar/', CustomLoginView.as_view(), name='login'),
    path('cadastro/', SignUpView.as_view(), name='signup'),
    path('logout/', logout_view, name='logout_view'),
]
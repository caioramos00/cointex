from django.urls import path
from .views import *

app_name = 'api'

urlpatterns = [
    path('create-user/', create_user_api, name='create_user_api')
]
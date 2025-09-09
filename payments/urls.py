from django.urls import path
from .views import webhook_pix

app_name = "payments"

urlpatterns = [
    path("webhook/pix/", webhook_pix, name="webhook_pix"),
]

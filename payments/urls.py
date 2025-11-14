from django.urls import path
from .views import webhook_pix, create_deposit_pix, pix_status

app_name = "payments"

urlpatterns = [
    path("webhook/pix/", webhook_pix, name="webhook_pix"),
    path("api/pix/deposit/create/", create_deposit_pix, name="create_deposit_pix"),
    path("api/pix/deposit/<int:pk>/status/", pix_status, name="pix_status"),
]
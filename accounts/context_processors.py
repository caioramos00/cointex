from django.utils import timezone
from datetime import date

def notifications(request):
    if request.user.is_authenticated:
        notifications = request.user.notifications.all()[:100]  # mais que o suficiente
        nao_lidas = request.user.notifications.filter(is_read=False).count()
        return {
            'user_notifications': notifications,
            'notifications_count': nao_lidas,
            'today': timezone.now().date(),
            'yesterday': timezone.now().date() - timezone.timedelta(days=1),
        }
    return {}
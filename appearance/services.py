from django.core.cache import cache
from .models import ThemeSetting

CACHE_KEY = 'appearance.active_theme'
CACHE_TTL = 60  # segundos

def get_active_theme() -> str:
    theme = cache.get(CACHE_KEY)
    if theme:
        return theme
    obj, _ = ThemeSetting.objects.get_or_create(pk=1, defaults={'active_theme': 'cointex'})
    theme = obj.active_theme
    cache.set(CACHE_KEY, theme, CACHE_TTL)
    return theme

def clear_theme_cache():
    cache.delete(CACHE_KEY)

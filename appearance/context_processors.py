from .services import get_active_theme

def theme_context(request):
    return {'theme': get_active_theme()}

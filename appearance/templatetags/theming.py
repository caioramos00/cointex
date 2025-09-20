# appearance/templatetags/theming.py
import os
from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from appearance.services import get_active_theme

register = template.Library()

@register.simple_tag
def themed_static(path: str) -> str:
    """
    Resolve para o asset do tema ativo, com fallback:
      1) /static/themes/<active>/<path>
      2) /static/themes/cointex/<path>
      3) /static/<path>        (global, se você já usa)
    Requer collectstatic em produção p/ Manifest/WhiteNoise reescrever URLs.
    """
    theme = get_active_theme() or "cointex"
    candidates = [
        f"themes/{theme}/{path}",
        f"themes/cointex/{path}",
        path,
    ]
    for p in candidates:
        try:
            # finders.find verifica se o arquivo existe nas fontes estáticas
            if finders.find(p):
                return static(p)  # usa storage.url (com hash/manifest em prod)
        except Exception:
            # em ManifestStaticFilesStorage, acessar algo não coletado pode levantar erro
            continue
    # último recurso: ainda retorna static(path)
    return static(path)

from django.template.loaders.base import Loader as BaseLoader
from django.template import Origin
from django.utils._os import safe_join
from django.conf import settings
from appearance.services import get_active_theme

class Loader(BaseLoader):
    """
    Tenta primeiro: <TEMPLATES['DIRS']>/themes/<active_theme>/<template_name>
    Se não achar, retorna nada e os loaders seguintes (filesystem/app_dirs)
    assumem — isto é o fallback global para Cointex (seus templates atuais).
    """

    def get_template_sources(self, template_name, template_dirs=None):
        # Não intercepta templates do Django Admin
        if template_name.startswith('admin/'):
            return

        theme = get_active_theme()
        dirs = template_dirs or self.engine.dirs

        for base_dir in dirs:
            themed_path = safe_join(base_dir, 'themes', theme, template_name)
            # Dica: prefixamos o template_name no Origin p/ cache ser "por tema"
            themed_key = f"{theme}:{template_name}"
            yield Origin(
                name=themed_path,
                template_name=themed_key,  # chave de cache inclui o tema
                loader=self,
            )

    def get_contents(self, origin):
        try:
            with open(origin.name, 'rb') as fp:
                return fp.read().decode(self.engine.file_charset)
        except FileNotFoundError:
            # Sem erro: simplesmente não fornece conteúdo; próximos loaders tentam
            raise

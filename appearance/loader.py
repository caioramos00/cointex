# appearance/loader.py
import os
from django.template.loaders.base import Loader as BaseLoader
from django.template import Origin
from django.utils._os import safe_join
from appearance.services import get_active_theme

class Loader(BaseLoader):
    """
    Procura primeiro por: <TEMPLATES['DIRS']>/themes/<active_theme>/<template_name>.
    - Se o arquivo existir, entrega esse Origin (override do tema).
    - Se NÃO existir, não retorna nada -> próximos loaders fazem fallback (Cointex padrão).
    """

    def get_template_sources(self, template_name, template_dirs=None):
        # não interceptar templates do admin
        if template_name.startswith('admin/'):
            return

        theme = get_active_theme()
        dirs = template_dirs or self.engine.dirs

        for base_dir in dirs:
            themed_path = safe_join(base_dir, 'themes', theme, template_name)
            # Somente yield se o arquivo realmente existir
            if os.path.exists(themed_path):
                yield Origin(
                    name=themed_path,
                    template_name=template_name,  # mantém o nome puro (evita cache estranho)
                    loader=self,
                )

    def get_contents(self, origin):
        # aqui só chega se o arquivo existir (verificação acima)
        with open(origin.name, 'rb') as fp:
            return fp.read().decode(self.engine.file_charset)

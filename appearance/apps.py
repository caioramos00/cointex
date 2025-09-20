from django.apps import AppConfig

class AppearanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'appearance'

    def ready(self):
        from django.db.models.signals import post_save
        from .models import ThemeSetting
        from .services import clear_theme_cache

        def _clear_cache(sender, instance, **kwargs):
            clear_theme_cache()

        post_save.connect(_clear_cache, sender=ThemeSetting)

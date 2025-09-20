from django.contrib import admin
from .models import ThemeSetting

@admin.register(ThemeSetting)
class ThemeSettingAdmin(admin.ModelAdmin):
    list_display = ('active_theme', 'updated_at')

    def has_add_permission(self, request):
        # Garante um único registro
        return not ThemeSetting.objects.exists()

from django.db import models

class ThemeSetting(models.Model):
    THEME_COINTEX = 'cointex'
    THEME_MPAY = 'mpay'

    THEME_CHOICES = [
        (THEME_COINTEX, 'Cointex'),
        (THEME_MPAY, 'MPay'),
    ]

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    active_theme = models.CharField(max_length=20, choices=THEME_CHOICES, default=THEME_COINTEX)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Tema ativo: {self.get_active_theme_display()}"

    class Meta:
        verbose_name = "Tema do Site"
        verbose_name_plural = "Tema do Site"

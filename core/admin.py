# core/admin.py  (novo trecho)
from django.contrib import admin, messages
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.shortcuts import redirect
import csv

from core.models import CtwaAdCatalog
from core.forms import UploadCtwaCatalogForm
from core.ctwa_catalog import import_ctwa_csv_file

@admin.register(CtwaAdCatalog)
class CtwaAdCatalogAdmin(admin.ModelAdmin):
    change_list_template = "admin/core/ctwaadcatalog/change_list.html"
    list_display = ("ad_id", "ad_name", "adset_name", "campaign_name", "updated_at")
    search_fields = ("ad_id", "ad_name", "adset_name", "campaign_name")
    ordering = ("-updated_at",)

    # adiciona rotas customizadas /import/ e /export/
    def get_urls(self):
        urls = super().get_urls()
        my = [
            path("import/", self.admin_site.admin_view(self.import_view), name="ctwa_catalog_import"),
            path("export/", self.admin_site.admin_view(self.export_view), name="ctwa_catalog_export"),
        ]
        return my + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["has_import_link"] = True
        extra_context["has_export_link"] = True
        return super().changelist_view(request, extra_context=extra_context)

    # Tela de upload
    def import_view(self, request):
        if request.method == "POST":
            form = UploadCtwaCatalogForm(request.POST, request.FILES)
            if form.is_valid():
                f = form.cleaned_data["file"]
                try:
                    count = import_ctwa_csv_file(f)
                    self.message_user(request, f"Importados/atualizados {count} anúncios.", level=messages.SUCCESS)
                    return redirect(reverse("admin:core_ctwaadcatalog_changelist"))
                except Exception as e:
                    self.message_user(request, f"Falha ao importar: {e}", level=messages.ERROR)
        else:
            form = UploadCtwaCatalogForm()
        ctx = dict(
            self.admin_site.each_context(request),
            title="Importar catálogo CTWA (CSV)",
            form=form,
        )
        return TemplateResponse(request, "admin/ctwa_catalog_import.html", ctx)

    # Download do catálogo atual (CSV)
    def export_view(self, request):
        qs = CtwaAdCatalog.objects.all().order_by("campaign_name", "adset_name", "ad_name")
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="ctwa_ad_catalog.csv"'
        w = csv.writer(resp)
        w.writerow(["Ad ID", "Ad Name", "Ad Set ID", "Ad Set Name", "Campaign ID", "Campaign Name"])
        for r in qs:
            w.writerow([r.ad_id, r.ad_name or "", r.adset_id or "", r.adset_name or "", r.campaign_id or "", r.campaign_name or ""])
        return resp

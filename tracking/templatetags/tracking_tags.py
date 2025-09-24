from django import template
from django.utils.safestring import mark_safe
from ..models import ClientPixel

register = template.Library()

def _html_meta(pixel_id: str):
    return f"""
<script>
!function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}(window, document,'script',
'https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '{pixel_id}');
fbq('track', 'PageView');
</script>
<noscript><img height="1" width="1" style="display:none"
src="https://www.facebook.com/tr?id={pixel_id}&ev=PageView&noscript=1"/></noscript>
""".strip()

def _html_ga4(measurement_id: str):
    return f"""
<script async src="https://www.googletagmanager.com/gtag/js?id={measurement_id}"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date()); gtag('config', '{measurement_id}');
</script>
""".strip()

def _html_tiktok(pixel_id: str):
    return f"""
<script>
!function (w, d, t) {{
  w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"];
  ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}}};
  for(var i=0;i<ttq.methods.length;i++) ttq.setAndDefer(ttq, ttq.methods[i]);
  ttq.instance=function(t){{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++) ttq.setAndDefer(e,ttq.methods[n]); return e }};
  ttq.load=function(e,n){{var i='https://analytics.tiktok.com/i18n/pixel/events.js';ttq._i=ttq._i||{{}};ttq._i[e]=[];ttq._o=ttq._o||{{}};ttq._o[e]=n||{{}};var a=document.createElement('script');a.type='text/javascript';a.async=!0;a.src=i;var s=document.getElementsByTagName('script')[0];s.parentNode.insertBefore(a,s)}};
  ttq.load('{pixel_id}'); ttq.page();
}}(window, document, 'ttq');
</script>
""".strip()

def _html_utmify(pixel_id: str):
    return f"""
<script src="https://cdn.utmify.com.br/scripts/utms/latest.js" async defer></script>
<script>
  window.pixelId = "{pixel_id}";
  var a = document.createElement("script");
  a.setAttribute("async", ""); a.setAttribute("defer", "");
  a.setAttribute("src", "https://cdn.utmify.com.br/scripts/pixel/pixel.js");
  document.head.appendChild(a);
</script>
""".strip()

def _html_custom(script: str):
    return script or ""

@register.simple_tag(takes_context=True)
def tracking_head(context):
    """
    Injeta scripts dos ClientPixels ativos (filtrados por path).
    Use no <head>.
    """
    request = context.get("request")
    path = getattr(request, "path", "/")
    out = []

    for cp in ClientPixel.objects.filter(active=True).order_by("order", "id"):
        if not cp.matches_path(path):
            continue
        prov = cp.provider
        cfg = cp.config or {}
        if prov == "meta" and cfg.get("pixel_id"):
            out.append(_html_meta(cfg["pixel_id"]))
        elif prov == "ga4" and cfg.get("measurement_id"):
            out.append(_html_ga4(cfg["measurement_id"]))
        elif prov == "tiktok" and cfg.get("pixel_id"):
            out.append(_html_tiktok(cfg["pixel_id"]))
        elif prov == "utmify" and cfg.get("pixel_id"):
            out.append(_html_utmify(cfg["pixel_id"]))
        elif prov == "custom" and cfg.get("script"):
            out.append(_html_custom(cfg["script"]))

    return mark_safe("\n".join(out))

@register.simple_tag
def tracking_body_end():
    """
    (Opcional) Bridge mínima: window.track(name, params).
    """
    return mark_safe("""
<script>
window.track = function(name, params) {
  try { if (window.fbq) fbq('track', name, params || {}); } catch (e) {}
  try { if (window.gtag) gtag('event', name, params || {}); } catch (e) {}
  try { if (window.ttq) ttq.track(name, params || {}); } catch (e) {}
};
</script>
""".strip())

from django import template
from django.utils.safestring import mark_safe
from django.utils.html import conditional_escape
from ..models import ClientTrackingConfig

register = template.Library()

def _script_tag(js: str) -> str:
    return f"<script>\n{js}\n</script>"

@register.simple_tag(takes_context=True)
def tracking_head(context):
    cfg = ClientTrackingConfig.load()

    # Evita admin (padrão)
    req = context.get("request")
    if cfg.exclude_admin and req and str(getattr(req, "path", "")).startswith("/admin"):
        return ""

    parts = []

    # META PIXEL (múltiplos ids com loader único)
    if cfg.meta_enabled:
        meta_ids = cfg.meta_ids()
        if meta_ids:
            loader = """
!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');
""".strip()
            inits = "\n".join([f"fbq('init','{pid}');" for pid in meta_ids])
            pageview = "fbq('track','PageView');"
            parts.append(_script_tag(loader + "\n" + inits + "\n" + pageview))
            # noscript img tags
            noscript_imgs = "\n".join([
                f'<img height="1" width="1" style="display:none" src="https://www.facebook.com/tr?id={pid}&ev=PageView&noscript=1"/>'
                for pid in meta_ids
            ])
            parts.append(f"<noscript>\n{noscript_imgs}\n</noscript>")

    # GA4
    if cfg.ga4_enabled and cfg.ga4_measurement_id:
        mid = cfg.ga4_measurement_id.strip()
        parts.append((
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>\n'
            + _script_tag(f"""
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date());
gtag('config','{mid}');
""".strip())
        ))

    # TikTok
    if cfg.tiktok_enabled and cfg.tiktok_pixel_id:
        tid = cfg.tiktok_pixel_id.strip()
        parts.append(_script_tag(f"""
!function (w, d, t) {{
  w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];
  ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"];
  ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat([].slice.call(arguments,0)))}}}};
  for(var i=0;i<ttq.methods.length;i++) ttq.setAndDefer(ttq, ttq.methods[i]);
  ttq.instance=function(t){{var e=ttq._i[t]||[];for(var n=0;n<ttq.methods.length;n++) ttq.setAndDefer(e,ttq.methods[n]); return e }};
  ttq.load=function(e,n){{var i='https://analytics.tiktok.com/i18n/pixel/events.js';ttq._i=ttq._i||{{}};ttq._i[e]=[];ttq._o=ttq._o||{{}};ttq._o[e]=n||{{}};var a=document.createElement('script');a.type='text/javascript';a.async=!0;a.src=i;var s=document.getElementsByTagName('script')[0];s.parentNode.insertBefore(a,s)}};
  ttq.load('{tid}'); ttq.page();
}}(window, document, 'ttq');
""".strip()))

    # Utmify
    if cfg.utmify_enabled and cfg.utmify_pixel_id:
        upid = cfg.utmify_pixel_id.strip()
        parts.append(
            '<script src="https://cdn.utmify.com.br/scripts/utms/latest.js" async defer></script>'
        )
        parts.append(_script_tag(f"""
window.pixelId = "{upid}";
(function(){{
  var a=document.createElement("script"); a.async=true; a.defer=true;
  a.src="https://cdn.utmify.com.br/scripts/pixel/pixel.js";
  document.head.appendChild(a);
}})();
""".strip()))

    # Custom <head> JS
    if cfg.custom_head_js:
        parts.append(_script_tag(cfg.custom_head_js))

    return mark_safe("\n".join(parts))

@register.simple_tag(takes_context=False)
def tracking_body_end():
    cfg = ClientTrackingConfig.load()
    parts = []
    # Helper window.track (com suporte opcional a eventID do fbq)
    if cfg.helper_enabled:
        parts.append(_script_tag("""
window.track = function(name, params, options) {
  try {
    if (window.fbq) {
      if (options && options.fbq && options.fbq.eventID) {
        fbq('track', name, params || {}, { eventID: options.fbq.eventID });
      } else {
        fbq('track', name, params || {});
      }
    }
  } catch (e) {}
  try { if (window.gtag) gtag('event', name, params || {}); } catch (e) {}
  try { if (window.ttq)  ttq.track(name, params || {}); } catch (e) {}
};
""".strip()))
    # Custom </body> JS
    if cfg.custom_body_js:
        parts.append(_script_tag(cfg.custom_body_js))
    return mark_safe("\n".join(parts))

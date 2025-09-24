import json
from django import template
from django.utils.safestring import mark_safe
from ..models import ClientTrackingConfig, PageEventConfig

register = template.Library()

def _script(js: str) -> str:
    return f"<script>\n{js}\n</script>"

@register.simple_tag(takes_context=True)
def tracking_head(context):
    cfg = ClientTrackingConfig.load()
    req = context.get("request")
    if cfg.exclude_admin and req and str(getattr(req, "path", "")).startswith("/admin"):
        return ""

    parts = []

    # META
    if cfg.meta_enabled:
        ids = cfg.meta_ids()
        if ids:
            loader = """
!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');
""".strip()
            inits = "\n".join([f"fbq('init','{pid}');" for pid in ids])
            pv = "fbq('track','PageView');" if cfg.meta_auto_pageview else ""
            parts.append(_script(loader + "\n" + inits + ("\n" + pv if pv else "")))
            noscript_imgs = "\n".join([
                f'<img height="1" width="1" style="display:none" src="https://www.facebook.com/tr?id={pid}&ev=PageView&noscript=1"/>'
                for pid in ids
            ]) if cfg.meta_auto_pageview else ""
            if noscript_imgs:
                parts.append(f"<noscript>\n{noscript_imgs}\n</noscript>")

    # GA4
    if cfg.ga4_enabled and cfg.ga4_measurement_id:
        mid = cfg.ga4_measurement_id.strip()
        parts.append(
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>'
        )
        if cfg.ga4_auto_pageview:
            parts.append(_script(f"""
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date()); gtag('config','{mid}');
""".strip()))
        else:
            parts.append(_script(f"""
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date()); gtag('config','{mid}', {{ send_page_view: false }});
""".strip()))

    # TikTok
    if cfg.tiktok_enabled and cfg.tiktok_pixel_id:
        tid = cfg.tiktok_pixel_id.strip()
        base = f"""
!function (w, d, t) {{
  w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];
  ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"];
  ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat([].slice.call(arguments,0)))}}}};
  for(var i=0;i<ttq.methods.length;i++) ttq.setAndDefer(ttq, ttq.methods[i]);
  ttq.instance=function(t){{var e=ttq._i[t]||[];for(var n=0;n<ttq.methods.length;n++) ttq.setAndDefer(e,ttq.methods[n]); return e }};
  ttq.load=function(e,n){{var i='https://analytics.tiktok.com/i18n/pixel/events.js';ttq._i=ttq._i||{{}};ttq._i[e]=[];ttq._o=ttq._o||{{}};ttq._o[e]=n||{{}};var a=document.createElement('script');a.type='text/javascript';a.async=!0;a.src=i;var s=document.getElementsByTagName('script')[0];s.parentNode.insertBefore(a,s)}};
  ttq.load('{tid}');
""".strip()
        end = "ttq.page();\n}(window, document, 'ttq');" if cfg.tiktok_auto_page else "}(window, document, 'ttq');"
        parts.append(_script(base + "\n" + end))

    # Utmify
    if cfg.utmify_enabled and cfg.utmify_pixel_id:
        upid = cfg.utmify_pixel_id.strip()
        parts.append('<script src="https://cdn.utmify.com.br/scripts/utms/latest.js" async defer></script>')
        parts.append(_script(f"""
window.pixelId = "{upid}";
(function(){{
  var a=document.createElement("script"); a.async=true; a.defer=true;
  a.src="https://cdn.utmify.com.br/scripts/pixel/pixel.js";
  document.head.appendChild(a);
}})();
""".strip()))

    # Custom head JS
    if cfg.custom_head_js:
        parts.append(_script(cfg.custom_head_js))

    return mark_safe("\n".join(parts))

@register.simple_tag(takes_context=True)
def tracking_body_end(context):
    cfg = ClientTrackingConfig.load()
    parts = []

    # Helper unificado (com suporte a fbq.eventID)
    if cfg.helper_enabled:
        parts.append(_script("""
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

    # Page Events on-load (por view_name)
    view_name = context.get("tracking_view_name")
    if view_name:
        pec = PageEventConfig.objects.filter(view_name=view_name, enabled=True).first()
        if pec:
            def _parse_json(txt):
                try:
                    return json.loads(txt) if txt else {}
                except Exception:
                    return {}
            evts = []
            if pec.fire_page_view:
                evts.append(("PageView", _parse_json(pec.page_view_params)))
            if pec.fire_view_content:
                evts.append(("ViewContent", _parse_json(pec.view_content_params)))
            if pec.fire_initiate_checkout:
                evts.append(("InitiateCheckout", _parse_json(pec.initiate_checkout_params)))
            if pec.fire_purchase:
                evts.append(("Purchase", _parse_json(pec.purchase_params)))
            if pec.fire_payment_expired:
                evts.append(("PaymentExpired", _parse_json(pec.payment_expired_params)))

            if evts:
                # script que dispara no load e respeita 'once per session'
                js_lines = ["(function(){",
                            "  function onceKey(v,e){return 'pe:'+v+':'+e;}",
                            "  function already(v,e){try{return sessionStorage.getItem(onceKey(v,e))==='1';}catch(_){return false;}}",
                            "  function mark(v,e){try{sessionStorage.setItem(onceKey(v,e),'1');}catch(_){}}",
                            "  function fire(name,params){ if(window.track) window.track(name, params || {}); }",
                            f"  var vname = {json.dumps(view_name)};"]
                for (name, params) in evts:
                    js = f"""
  if (!{ 'already(vname,' + json.dumps(name) + ')' if pec.fire_once_per_session else 'false' }) {{
    fire({json.dumps(name)}, {json.dumps(params)});
    { 'mark(vname,' + json.dumps(name) + ');' if pec.fire_once_per_session else '' }
  }}""".rstrip()
                    js_lines.append(js)
                js_lines.append("})();")
                parts.append(_script("\n".join(js_lines)))

    # Custom body JS
    if cfg.custom_body_js:
        parts.append(_script(cfg.custom_body_js))

    return mark_safe("\n".join(parts))

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

    # Helper com mapeamento por provedor
    if cfg.helper_enabled:
        parts.append(_script("""
(function(){
  // Mapeamento canônico -> nome por provedor
  var MAP = {
    meta: {
      PageView:'PageView', ViewContent:'ViewContent', Search:'Search',
      AddToCart:'AddToCart', AddToWishlist:'AddToWishlist',
      InitiateCheckout:'InitiateCheckout', AddPaymentInfo:'AddPaymentInfo',
      Purchase:'Purchase', Lead:'Lead', CompleteRegistration:'CompleteRegistration',
      Subscribe:'Subscribe', StartTrial:'StartTrial', Contact:'Contact',
      FindLocation:'FindLocation', Schedule:'Schedule',
      SubmitApplication:'SubmitApplication', CustomizeProduct:'CustomizeProduct',
      Donate:'Donate'
    },
    ga4: {
      PageView:'page_view', ViewContent:'view_item', Search:'search',
      AddToCart:'add_to_cart', AddToWishlist:'add_to_wishlist',
      InitiateCheckout:'begin_checkout', AddPaymentInfo:'add_payment_info',
      Purchase:'purchase', Lead:'generate_lead', CompleteRegistration:'sign_up',
      Subscribe:'subscribe', StartTrial:'start_trial', Contact:'contact',
      FindLocation:'search', Schedule:'schedule', // fallback
      SubmitApplication:'submit_application', // custom aceito
      CustomizeProduct:'customize_product',   // custom aceito
      Donate:'purchase' // melhor prática: tratar como purchase (doação)
    },
    tiktok: {
      PageView:'PageView', ViewContent:'ViewContent', Search:'Search',
      AddToCart:'AddToCart', AddToWishlist:'AddToWishlist',
      InitiateCheckout:'InitiateCheckout', AddPaymentInfo:'AddPaymentInfo',
      Purchase:'CompletePayment', Lead:'SubmitForm', CompleteRegistration:'CompleteRegistration',
      Subscribe:'Subscribe', StartTrial:'StartTrial', Contact:'Contact',
      FindLocation:'FindLocation', Schedule:'Schedule',
      SubmitApplication:'SubmitApplication', CustomizeProduct:'CustomizeProduct',
      Donate:'CompletePayment'
    }
  };

  window.track = function(name, params, options) {
    var p = params || {}; var opt = options || {};
    try {
      if (window.fbq) {
        var mn = (MAP.meta[name] || name);
        if (opt.fbq && opt.fbq.eventID) {
          fbq('track', mn, p, { eventID: opt.fbq.eventID });
        } else {
          fbq('track', mn, p);
        }
      }
    } catch(e) {}
    try {
      if (window.gtag) {
        var gn = (MAP.ga4[name] || name);
        gtag('event', gn, p);
      }
    } catch(e) {}
    try {
      if (window.ttq) {
        if (name === 'PageView') {
          // Se você desativou o auto page em tracking_head, permite acionar aqui
          ttq.page();
        } else {
          var tn = (MAP.tiktok[name] || name);
          ttq.track(tn, p);
        }
      }
    } catch(e) {}
  };
})();""".strip()))

    # Page Events: ler config por view_name e disparar todos os selecionados
    view_name = context.get("tracking_view_name")
    if view_name:
        pec = PageEventConfig.objects.filter(view_name=view_name, enabled=True).first()
        if pec:
            import json as _json
            def _pj(txt):  # parse json seguro
                try: return _json.loads(txt) if txt else {}
                except Exception: return {}
            EVTS = []
            if pec.fire_page_view:           EVTS.append(("PageView", _pj(pec.page_view_params)))
            if pec.fire_view_content:        EVTS.append(("ViewContent", _pj(pec.view_content_params)))
            if pec.fire_search:              EVTS.append(("Search", _pj(pec.search_params)))
            if pec.fire_add_to_cart:         EVTS.append(("AddToCart", _pj(pec.add_to_cart_params)))
            if pec.fire_add_to_wishlist:     EVTS.append(("AddToWishlist", _pj(pec.add_to_wishlist_params)))
            if pec.fire_initiate_checkout:   EVTS.append(("InitiateCheckout", _pj(pec.initiate_checkout_params)))
            if pec.fire_add_payment_info:    EVTS.append(("AddPaymentInfo", _pj(pec.add_payment_info_params)))
            if pec.fire_purchase:            EVTS.append(("Purchase", _pj(pec.purchase_params)))
            if pec.fire_lead:                EVTS.append(("Lead", _pj(pec.lead_params)))
            if pec.fire_complete_registration: EVTS.append(("CompleteRegistration", _pj(pec.complete_registration_params)))
            if pec.fire_subscribe:           EVTS.append(("Subscribe", _pj(pec.subscribe_params)))
            if pec.fire_start_trial:         EVTS.append(("StartTrial", _pj(pec.start_trial_params)))
            if pec.fire_contact:             EVTS.append(("Contact", _pj(pec.contact_params)))
            if pec.fire_find_location:       EVTS.append(("FindLocation", _pj(pec.find_location_params)))
            if pec.fire_schedule:            EVTS.append(("Schedule", _pj(pec.schedule_params)))
            if pec.fire_submit_application:  EVTS.append(("SubmitApplication", _pj(pec.submit_application_params)))
            if pec.fire_customize_product:   EVTS.append(("CustomizeProduct", _pj(pec.customize_product_params)))
            if pec.fire_donate:              EVTS.append(("Donate", _pj(pec.donate_params)))

            if EVTS:
                js = ["(function(){",
                      "  function onceKey(v,e){return 'pe:'+v+':'+e;}",
                      "  function already(v,e){try{return sessionStorage.getItem(onceKey(v,e))==='1';}catch(_){return false;}}",
                      "  function mark(v,e){try{sessionStorage.setItem(onceKey(v,e),'1');}catch(_){}}",
                      "  function fire(name,params){ if(window.track) window.track(name, params || {}); }",
                      f"  var vname = {json.dumps(view_name)};"]
                for (n, params) in EVTS:
                    js.append(f"""
  if (!{ 'already(vname,' + json.dumps(n) + ')' if pec.fire_once_per_session else 'false' }) {{
    fire({json.dumps(n)}, {json.dumps(params)});
    { 'mark(vname,' + json.dumps(n) + ');' if pec.fire_once_per_session else '' }
  }}""".rstrip())
                js.append("})();")
                parts.append(_script("\n".join(js)))

    # Custom body JS (se tiver)
    if cfg.custom_body_js:
        parts.append(_script(cfg.custom_body_js))

    return mark_safe("\n".join(parts))

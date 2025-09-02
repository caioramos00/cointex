import time, logging, os, json, random
from django.shortcuts import redirect

class CanonicalHostMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        target = os.getenv("CANONICAL_HOST")  # por ex: "www.cointex.cash"
        if target:
            host = request.get_host().split(":")[0]
            if host != target:
                return redirect(f"https://{target}{request.get_full_path()}", permanent=True)
        return self.get_response(request)

logger = logging.getLogger(__name__)

# Prefixos/rotas que não queremos logar
STATIC_PREFIXES = (
    "/static/", "/favicon", "/apple-touch-icon", "/robots.txt",
    "/manifest", "/service-worker", "/sitemap.xml", "/.well-known",
)

# Amostragem para rotas muito frequentes
SAMPLE_RATE_DEFAULT = float(os.getenv("TIMING_SAMPLE_RATE", "1.0"))  # 1.0 = 100%
SAMPLE_RATE_POLL    = float(os.getenv("TIMING_SAMPLE_RATE_POLL", "0.02"))  # 2% para /check-pix-status

# Limiar padrão
THRESHOLD_MS = int(os.getenv("TIMING_THRESHOLD_MS", "200"))

def _should_sample(path: str) -> bool:
    # /check-pix-status pode gerar muito ruído; amostramos
    if path.startswith("/check-pix-status"):
        return random.random() < SAMPLE_RATE_POLL
    # demais rotas seguem sample global
    return random.random() < SAMPLE_RATE_DEFAULT

class TimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.threshold_ms = THRESHOLD_MS
        logger.info(json.dumps({"type":"TIMING_INIT","threshold_ms": self.threshold_ms,
                                "sample_default": SAMPLE_RATE_DEFAULT, "sample_poll": SAMPLE_RATE_POLL}))

    def __call__(self, request):
        path = request.path

        # pula estáticos e similares
        if any(path.startswith(p) for p in STATIC_PREFIXES):
            return self.get_response(request)

        t0 = time.perf_counter()
        response = self.get_response(request)
        dur_ms = int((time.perf_counter() - t0) * 1000)

        # Loga só se: ultrapassou limiar OU status >= 400
        status = getattr(response, "status_code", 0)
        if (dur_ms > self.threshold_ms or status >= 400) and _should_sample(path):
            # Uma linha JSON, prefixada por "TIMING " para filtrar fácil
            payload = {
                "type": "TIMING",
                "path": path,
                "method": request.method,
                "status": status,
                "ms": dur_ms,
                # campos úteis p/ diagnóstico
                "user": getattr(getattr(request, "user", None), "id", None),
                "qs": request.META.get("QUERY_STRING")[:120] if request.META.get("QUERY_STRING") else "",
            }
            logger.info("TIMING " + json.dumps(payload, ensure_ascii=False))
        return response

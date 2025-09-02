import time, logging, os
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

# --- TimingMiddleware --------------------------------------------------
logger = logging.getLogger(__name__)

STATIC_PREFIXES = ("/static/", "/favicon", "/apple-touch-icon", "/robots.txt")

class TimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.threshold_ms = int(os.getenv("TIMING_THRESHOLD_MS", "200"))
        logger.info("TIMING middleware enabled threshold_ms=%d", self.threshold_ms)

    def __call__(self, request):
        # pula estáticos e afins
        if any(request.path.startswith(p) for p in STATIC_PREFIXES):
            return self.get_response(request)

        t0 = time.perf_counter()
        response = self.get_response(request)
        dur_ms = int((time.perf_counter() - t0) * 1000)

        if dur_ms > self.threshold_ms:
            # prefixo 'TIMING' para filtrar fácil no Render
            logger.info(
                "TIMING path=%s method=%s status=%s ms=%d",
                request.path, request.method, getattr(response, "status_code", "-"), dur_ms
            )
        return response

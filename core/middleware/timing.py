import time, logging
logger = logging.getLogger(__name__)

class TimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        t0 = time.perf_counter()
        response = self.get_response(request)
        dur_ms = int((time.perf_counter() - t0) * 1000)
        # amostragem leve: só loga se >200ms
        if dur_ms > 200:
            logger.info("timing path=%s status=%s dur_ms=%d", request.path, getattr(response, "status_code", "-"), dur_ms)
        return response

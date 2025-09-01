import os
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

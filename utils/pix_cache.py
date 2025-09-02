import json, time, logging
from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

PIX_LAST_KEY = "pix:last:{uid}"
PIX_LOCK_KEY = "lock:pix:create:{uid}"

def get_cached_pix(user_id):
    r = get_redis_connection("default")
    raw = r.get(PIX_LAST_KEY.format(uid=user_id))
    return json.loads(raw) if raw else None

def set_cached_pix(user_id, data, ttl=300):
    r = get_redis_connection("default")
    r.setex(PIX_LAST_KEY.format(uid=user_id), ttl, json.dumps(data))

def with_user_pix_lock(user_id, timeout=10):
    """Context manager p/ single-flight por usuário na criação do PIX."""
    r = get_redis_connection("default")
    lock = r.lock(PIX_LOCK_KEY.format(uid=user_id), timeout=timeout)
    class _L:
        def __enter__(self):  return lock.acquire(blocking=False)
        def __exit__(self, exc_type, exc, tb):
            try:
                if lock.locked(): lock.release()
            except Exception:
                pass
    return _L()

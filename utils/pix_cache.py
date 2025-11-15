import json
import time
import logging
from contextlib import contextmanager

from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

PIX_LAST_KEY = "pix:last:{uid}"
PIX_LOCK_KEY = "lock:pix:create:{uid}"


def _get_redis_or_none():
    """
    Tenta obter a conexão Redis. Se o backend não suportar (ex: localhost sem django-redis)
    ou der qualquer erro, loga e retorna None.

    Em produção, com Redis configurado corretamente, continua funcionando normal.
    """
    try:
        return get_redis_connection("default")
    except Exception as e:
        # Isso cobre o NotImplementedError: "This backend does not support this feature"
        logger.warning("pix.cache redis unavailable, will fallback to no-cache/no-lock err=%s", e)
        return None


def get_cached_pix(user_id):
    """
    Lê o último PIX do cache, se Redis estiver disponível.
    Em caso de erro ou sem Redis, retorna None silenciosamente.
    """
    r = _get_redis_or_none()
    if not r:
        return None

    try:
        raw = r.get(PIX_LAST_KEY.format(uid=user_id))
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning("pix.cache get failed user_id=%s err=%s", user_id, e)
        return None


def set_cached_pix(user_id, data, ttl=300):
    """
    Grava o PIX no cache, se Redis estiver disponível.
    Se não tiver Redis ou falhar, apenas loga e segue (sem quebrar o fluxo).
    """
    r = _get_redis_or_none()
    if not r:
        return

    try:
        r.setex(PIX_LAST_KEY.format(uid=user_id), ttl, json.dumps(data))
    except Exception as e:
        logger.warning("pix.cache set failed user_id=%s err=%s", user_id, e)


@contextmanager
def with_user_pix_lock(user_id, timeout=10):
    """
    Context manager para single-flight por usuário na criação do PIX.

    - Em produção (Redis OK): usa r.lock(...) normalmente e respeita o 'acquired' (True/False).
    - Em localhost / sem Redis / erro de lock: devolve 'acquired=True' e NÃO usa lock,
      para não quebrar o fluxo de testes.
    """
    r = _get_redis_or_none()
    if not r:
        # Sem Redis: não trava, mas também não quebra.
        yield True
        return

    lock = None
    acquired = False

    try:
        # Pode lançar erro se o backend não suportar lock, por segurança tratamos aqui também.
        lock = r.lock(PIX_LOCK_KEY.format(uid=user_id), timeout=timeout)
        acquired = lock.acquire(blocking=False)
    except Exception as e:
        logger.warning("pix.lock acquire failed user_id=%s err=%s (fallback acquired=True)", user_id, e)
        # Fallback: sem lock, porém seguimos o fluxo
        yield True
        return

    try:
        # Caminho normal: devolve se de fato conseguiu o lock ou não
        yield acquired
    finally:
        if acquired and lock:
            try:
                lock.release()
            except Exception:
                # Nunca deixar exceção de release vazar pro fluxo principal
                pass

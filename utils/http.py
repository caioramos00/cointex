import os, logging, threading, time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("perf")
_thread_local = threading.local()

def _build_session():
    s = requests.Session()
    pool = int(os.getenv("HTTP_POOL_MAXSIZE", "50"))
    retries = int(os.getenv("HTTP_RETRIES", "2"))
    backoff = float(os.getenv("HTTP_BACKOFF", "0.3"))
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD", "OPTIONS"])  # não re-tenta POST por padrão
    )
    adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool, max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": "cointex/1.0"})
    return s

def _session():
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = _build_session()
        _thread_local.session = s
    return s

DEFAULT_TIMEOUT = (
    float(os.getenv("HTTP_CONNECT_TIMEOUT", "5")),   # connect
    float(os.getenv("HTTP_READ_TIMEOUT", "10")),     # read
)

def _request(method, url, **kwargs):
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    label = kwargs.pop("measure", None)  # string p/ log (ex: "galaxify/create")
    t0 = time.time() if label else None
    resp = _session().request(method, url, timeout=timeout, **kwargs)
    if label:
        dt = (time.time() - t0) * 1000.0
        if dt > 800:
            logger.info(f"http {method} {label} took {dt:.0f}ms status={resp.status_code}")
    return resp

def http_get(url, **kw):    return _request("GET", url, **kw)
def http_post(url, **kw):   return _request("POST", url, **kw)
def http_put(url, **kw):    return _request("PUT", url, **kw)
def http_delete(url, **kw): return _request("DELETE", url, **kw)

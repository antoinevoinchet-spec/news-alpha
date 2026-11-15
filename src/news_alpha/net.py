"""GET HTTP avec limite de débit par service et cache disque (EDGAR, Alpaca)."""

import gzip
import hashlib
import http.client
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from news_alpha import ROOT

CACHE_DIR = ROOT / "data" / "cache"
RETRY_STATUS = {429, 500, 502, 503, 504}
# Environ 8,5 minutes au total : la SEC a des indisponibilités passagères (cas réels du
# 25/10/2026 : 4 coupures de suite en 14 s, puis des 503 pendant plus de 2 minutes).
RETRY_WAITS = (2, 4, 8, 16, 32, 64, 128, 256)

_last_call: dict[str, float] = {}


class HttpError(Exception):
    def __init__(self, url: str, status: int, body: str):
        super().__init__(f"HTTP {status} sur {url} : {body[:200]}")
        self.status = status
        self.body = body


def _throttle(bucket: str, min_interval: float) -> None:
    wait = _last_call.get(bucket, 0.0) + min_interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[bucket] = time.monotonic()


def get(
    url: str,
    headers: dict[str, str],
    bucket: str,
    min_interval: float,
    cache_ok: Callable[[bytes], bool] | None = None,
) -> bytes | None:
    """Renvoie le corps de la réponse, ou None si 404.

    Une réponse n'est mise en cache que si `cache_ok(corps)` est vrai : on teste
    le contenu, jamais le simple succès HTTP, pour ne pas figer un échec.
    """
    path = CACHE_DIR / bucket / (hashlib.sha256(url.encode()).hexdigest() + ".gz")
    if cache_ok and path.exists():
        try:
            return gzip.decompress(path.read_bytes())
        except (EOFError, gzip.BadGzipFile, OSError):
            pass  # fichier tronqué par un arrêt : on le retélécharge

    request = urllib.request.Request(url, headers={**headers, "Accept-Encoding": "gzip"})
    for wait in (*RETRY_WAITS, None):  # None : dernière tentative
        _throttle(bucket, min_interval)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            break
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if err.code not in RETRY_STATUS or wait is None:
                error_body = err.read()
                if err.headers.get("Content-Encoding") == "gzip":
                    error_body = gzip.decompress(error_body)
                raise HttpError(url, err.code, error_body.decode(errors="replace")) from err
            failure = f"HTTP {err.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as err:
            # Connexion refusée ou coupée, réponse incomplète, lecture trop lente.
            if wait is None:
                raise
            failure = type(err).__name__
        print(f"{url} : {failure}, nouvel essai dans {wait} s", file=sys.stderr)
        time.sleep(wait)

    if cache_ok and cache_ok(body):
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".tmp")
        partial.write_bytes(gzip.compress(body))
        os.replace(partial, path)  # écriture atomique : jamais de fichier à moitié écrit
    return body


def sec_get(url: str, cache_ok: Callable[[bytes], bool] | None = None) -> bytes | None:
    """Requête EDGAR : User-Agent de SEC_USER_AGENT, 5 requêtes/s au plus."""
    headers = {"User-Agent": os.environ["SEC_USER_AGENT"]}
    return get(url, headers, bucket="sec", min_interval=0.2, cache_ok=cache_ok)

"""Scoped Windows proxy fallback for Earth Engine requests made from WSL."""
import base64
from functools import lru_cache
import os
import subprocess
from urllib.parse import urlsplit

import requests


_PROXY_VARIABLES = ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy',
                    'ALL_PROXY', 'all_proxy')


def _origin(url):
    """Only send scheme and host to Windows; never pass a signed URL or query."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
            return None
        host = parsed.hostname.encode('idna').decode('ascii')
        if ':' in host:
            host = '[' + host + ']'
        if parsed.port:
            host += ':' + str(parsed.port)
        return parsed.scheme.lower() + '://' + host + '/'
    except (TypeError, ValueError, UnicodeError):
        return None


@lru_cache(maxsize=64)
def _windows_proxy_for_origin(origin):
    """Honor Windows' proxy and bypass decision without writing it to disk."""
    # Encode even the sanitized origin: it is never interpreted as PowerShell.
    target = base64.b64encode(origin.encode('utf-8')).decode('ascii')
    command = (
        "$u=[Uri][Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + target + "'));"
        "$p=[Net.WebRequest]::DefaultWebProxy;"
        "if (-not $p.IsBypassed($u)) {[Console]::Write($p.GetProxy($u).AbsoluteUri)}"
    )
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, timeout=10, check=False)
        if result.returncode != 0 or len(result.stdout) > 2048:
            return None
        proxy = result.stdout.strip()
        parsed = urlsplit(proxy)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
            return None
        _ = parsed.port  # Reject malformed ports without printing the address.
        return proxy
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None


def clear_windows_proxy_cache():
    """A resumed run must pick up changes to Windows Internet Options."""
    _windows_proxy_for_origin.cache_clear()


def earth_engine_proxy_kwargs(url):
    """Return a per-request fallback only when no explicit proxy is configured."""
    if not os.environ.get('WSL_DISTRO_NAME') or any(os.environ.get(key) for key in _PROXY_VARIABLES):
        return {}
    origin = _origin(url)
    if not origin or requests.utils.should_bypass_proxies(origin, no_proxy=None):
        return {}
    proxy = _windows_proxy_for_origin(origin)
    return {'proxies': {'http': proxy, 'https': proxy}} if proxy else {}


class EarthEngineSession(requests.Session):
    """Apply this fallback only to EE authentication and API transports."""

    def request(self, method, url, **kwargs):
        if 'proxies' not in kwargs:
            kwargs.update(earth_engine_proxy_kwargs(url))
        return super().request(method, url, **kwargs)

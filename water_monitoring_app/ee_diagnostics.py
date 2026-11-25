"""Minimal Earth Engine diagnostics; never print credentials or proxy URLs."""
import os
import re

import requests

from .cache import initialize_earth_engine
from .earth_engine_network import clear_windows_proxy_cache, earth_engine_proxy_kwargs


def main():
    proxy_configured = any(os.environ.get(name) for name in
                           ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy'))
    print('پراکسی در متغیرهای محیطی تنظیم شده:', 'بله' if proxy_configured else 'خیر')
    clear_windows_proxy_cache()
    endpoint = 'https://earthengine.googleapis.com/$discovery/rest'
    fallback = earth_engine_proxy_kwargs(endpoint)
    print('پراکسی ویندوز برای Earth Engine:', 'فعال' if fallback else 'استفاده نمی‌شود')
    try:
        response = requests.get(endpoint, params={'version': 'v1'}, timeout=(8, 15), **fallback)
        content_type = response.headers.get('content-type', '').lower()
        print('دسترسی ناشناس به مشخصات Earth Engine: HTTP', response.status_code,
              '(HTML)' if 'html' in content_type else '(غیر HTML)')
        response.close()
    except requests.exceptions.Timeout:
        print('دسترسی ناشناس به مشخصات Earth Engine: مهلت اتصال تمام شد')
    except requests.exceptions.SSLError:
        print('دسترسی ناشناس به مشخصات Earth Engine: خطای گواهی TLS')
    except requests.exceptions.RequestException:
        print('دسترسی ناشناس به مشخصات Earth Engine: خطای ارتباط')

    ok, message = initialize_earth_engine()
    if ok:
        print('راه‌اندازی Earth Engine با پیکربندی برنامه: موفق')
        try:
            import ee
            if ee.Number(1).getInfo() == 1:
                print('آزمون محاسبه کوچک Earth Engine: موفق')
                return 0
        except Exception:
            pass  # The exception may contain URLs or credentials; do not print it.
        print('آزمون محاسبه کوچک Earth Engine: ناموفق؛ دسترسی پروژه و شبکه را بررسی کنید')
        return 1
    lower = message.lower()
    status = re.search(r'\b(401|403|429|500|502|503|504)\b', lower)
    print('راه‌اندازی Earth Engine با پیکربندی برنامه: ناموفق')
    if status:
        print('کد پاسخ:', status.group(1))
    if 'html' in lower:
        print('نوع پاسخ خطا: HTML')
    else:
        print(message)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

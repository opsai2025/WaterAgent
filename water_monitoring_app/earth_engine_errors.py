"""Safe, actionable errors for Earth Engine initialization."""
import re


INITIALIZATION_PREFIX = 'راه‌اندازی Earth Engine ناموفق بود:'


class EarthEngineInitializationError(ConnectionError):
    """The service could not be initialized; retrying each month cannot help."""


def initialization_error_message(error):
    """Classify an error without returning service HTML, URLs, or credentials."""
    raw = str(error)
    lower = raw.lower()
    response = getattr(error, 'resp', None)
    status = getattr(response, 'status', None)
    if status is None:
        match = re.search(r'\b(?:httperror|http|error|status)\s*[:=]?\s*(401|403|429|500|502|503|504)\b',
                          lower)
        status = int(match.group(1)) if match else None
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    html_response = '<html' in lower or '<!doctype html' in lower
    if status == 403 and (html_response or '/$discovery/rest' in lower):
        detail = ('دریافت مشخصات سرویس با HTTP 403 رد شد'
                  + (' و پاسخ HTML بود' if html_response else '')
                  + '. اتصال و تنظیمات شبکه WSL را بررسی کنید؛ تا رفع مشکل، ادامه پایش همان خطا را تکرار می‌کند.')
    elif status == 403:
        detail = ('سرویس HTTP 403 برگرداند. ثبت پروژه در Earth Engine، فعال بودن API '
                  'و دسترسی حساب و پروژه Google Cloud را بررسی کنید.')
    elif status == 401 or 'invalid_grant' in lower:
        detail = 'احراز هویت پذیرفته نشد؛ اعتبارنامه و دسترسی حساب را بررسی کنید.'
    elif status == 429:
        detail = 'سرویس درخواست‌ها را محدود کرده است (HTTP 429)؛ بعداً دوباره تلاش کنید.'
    elif status in (500, 502, 503, 504):
        detail = f'سرویس موقتاً در دسترس نیست (HTTP {status})؛ بعداً دوباره تلاش کنید.'
    elif any(word in lower for word in ('timeout', 'timed out', 'connection', 'network', 'dns')):
        detail = 'ارتباط با سرویس برقرار نشد؛ اتصال و تنظیمات شبکه WSL را بررسی کنید.'
    else:
        detail = 'تنظیمات اتصال، اعتبارنامه و دسترسی پروژه Google Cloud را بررسی کنید.'
    return f'{INITIALIZATION_PREFIX} {detail}'


def is_initialization_message(message):
    """Recognize both current messages and checkpoints from previous versions."""
    value = str(message or '')
    return INITIALIZATION_PREFIX in value or 'earth engine initialization failed' in value.lower()

"""Read-only Supabase PostgreSQL connectivity check for deployment preparation."""
import os
import ssl
import tempfile
from contextlib import contextmanager

from .settings import load_settings


@contextmanager
def _certificate_path(environ):
    """Use a private temporary CA file when the host provides PEM as a variable."""
    pem = environ.get('WATER_MONITOR_DB_CA_PEM', '').strip()
    if pem:
        ssl.PEM_cert_to_DER_cert(pem)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.crt') as cert:
            cert.write(pem + '\n')
            cert.flush()
            yield cert.name
    else:
        yield environ.get('WATER_MONITOR_DB_SSLROOTCERT', '').strip() or None


def check_database(environ=None, connect=None):
    """Check connectivity only; never log DSNs, credentials, or server errors."""
    environ = os.environ if environ is None else environ
    dsn = environ.get('WATER_MONITOR_DATABASE_URL', '').strip()
    if not dsn:
        return False, 'نشانی اتصال پایگاه داده در WATER_MONITOR_DATABASE_URL تنظیم نشده است.'
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        options = conninfo_to_dict(dsn)
        if not options.get('host') or not options.get('dbname'):
            return False, 'تنظیم اتصال باید میزبان و نام پایگاه داده را مشخص کند.'
        factory = connect or psycopg.connect
        kwargs = {'sslmode': 'verify-full', 'connect_timeout': 10}
        with _certificate_path(environ) as rootcert:
            if rootcert:
                kwargs['sslrootcert'] = rootcert
            with factory(dsn, **kwargs) as db:
                row = db.execute('SELECT 1').fetchone()
                if not row or row[0] != 1:
                    return False, 'آزمون ساده پایگاه داده پاسخ مورد انتظار را دریافت نکرد.'
        return True, 'اتصال رمزگذاری‌شده و بررسی‌شده PostgreSQL برقرار شد.'
    except Exception:
        return False, ('اتصال پایگاه داده برقرار نشد؛ اتصال شبکه، اعتبارنامه، '
                       'گواهی CA و تنظیمات Session pooler را بررسی کنید.')


def main():
    load_settings()
    ok, message = check_database()
    print(message)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Cache functions for the monitoring application."""
import os
import numpy as np
import rasterio
import math
import ee
import tempfile
import requests
import time
import base64
import json
import hashlib
import shutil
import uuid
from PIL import Image
import streamlit as st

from .earth_engine_errors import EarthEngineInitializationError, initialization_error_message

from .config import (
    CLOUD_THRESHOLD,
    ALL_PARAMETERS,
    MAX_RETRIES,
    RETRY_DELAY_BASE,
    EE_RPC_TIMEOUT_MS,
    EE_RPC_MAX_RETRIES,
    CACHE_ROOT_NAME,
    RUN_CONFIG_FILE,
    RUN_STATE_FILE,
    CACHE_MAX_AGE_DAYS,
    STATUS_NO_DATA,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_SKIPPED,
)

def initialize_earth_engine():
    """Initialize a bounded EE transport; failed attempts must remain retryable."""
    try:
        import google.auth
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google.oauth2.service_account import Credentials as ServiceCredentials
        from .earth_engine_transport import EarthEngineTransport
        from .earth_engine_network import EarthEngineSession, clear_windows_proxy_cache

        clear_windows_proxy_cache()

        timeout_seconds = EE_RPC_TIMEOUT_MS / 1000.0

        class BoundedAuthRequest(Request):
            def __call__(self, url, method='GET', body=None, headers=None,
                         timeout=None, **kwargs):
                # EE's setDeadline does not cover OAuth token refresh. Bound
                # that separate transport too, including an expired saved token.
                limit = min(timeout, timeout_seconds) if isinstance(timeout, (int, float)) and timeout > 0 else timeout_seconds
                return super().__call__(url, method=method, body=body,
                                        headers=headers, timeout=limit, **kwargs)

        # setDeadline rebuilds EE resources and cannot precede the first
        # Initialize in current EE releases. A bounded public HTTP transport
        # covers that first request; setDeadline is applied after initialization.
        ee.data.setMaxRetries(EE_RPC_MAX_RETRIES)
        with EarthEngineSession() as auth_session:
            auth_request = BoundedAuthRequest(session=auth_session)
            base64_key = os.environ.get('GOOGLE_EARTH_ENGINE_KEY_BASE64')
            project = None
            if base64_key:
                key_data = json.loads(base64.b64decode(base64_key).decode())
                credentials = ServiceCredentials.from_service_account_info(
                    key_data, scopes=ee.oauth.SCOPES)
            else:
                # Read saved credentials without EE's unbounded token check.
                # Ambient Google credentials remain supported when no saved
                # Earth Engine refresh token exists; no interactive login runs.
                try:
                    saved_credentials = ee.oauth.get_credentials_arguments()
                except OSError:
                    saved_credentials = {}
                if saved_credentials.get('refresh_token'):
                    credentials = Credentials(token=None, **saved_credentials)
                    project = (saved_credentials.get('quota_project_id')
                               or ee.oauth.get_appdefault_project())
                    if project and project != credentials.quota_project_id:
                        credentials = credentials.with_quota_project(project)
                else:
                    credentials, project = google.auth.default(
                        scopes=ee.oauth.SCOPES, request=auth_request)
                    project = getattr(credentials, 'quota_project_id', None) or project
            credentials.refresh(auth_request)

        transport = EarthEngineTransport(timeout=timeout_seconds)
        ee.Initialize(credentials, http_transport=transport, project=project)
        ee.data.setDeadline(EE_RPC_TIMEOUT_MS)
        return True, "Earth Engine initialized"
    except Exception as auth_error:
        return False, initialization_error_message(auth_error)


def get_utm_zone(longitude):
    return math.floor((longitude + 180) / 6) + 1


def _cache_root():
    """First writable location for the application's temporary cache."""
    candidates = [
        os.path.join(tempfile.gettempdir(), CACHE_ROOT_NAME),
        os.path.join(os.path.expanduser("~"), "." + CACHE_ROOT_NAME),
    ]
    try:
        candidates.append(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "." + CACHE_ROOT_NAME)
        )
    except Exception:
        pass

    for folder in candidates:
        try:
            os.makedirs(folder, exist_ok=True)
            # A unique probe avoids races between concurrent sessions.
            with tempfile.TemporaryFile(dir=folder):
                pass
            return folder
        except Exception:
            continue
    raise OSError("No writable cache root is available")


def _user_cache_root():
    """Resolve the cache namespace for the authenticated server-side account.

    Old, unowned cache folders remain outside this namespace. A missing or
    malformed identity must never fall back to the old shared cache.
    """
    user_id = st.session_state.get('auth_user_id')
    if not isinstance(user_id, str):
        raise PermissionError('An authenticated account is required for cache access')
    try:
        canonical_id = str(uuid.UUID(user_id))
    except (ValueError, AttributeError):
        raise PermissionError('Invalid authenticated account identity') from None
    if user_id != canonical_id:
        raise PermissionError('Invalid authenticated account identity')
    users_root = os.path.join(_cache_root(), 'users')
    user_root = os.path.join(users_root, canonical_id)
    for folder in (users_root, user_root):
        if os.path.islink(folder):
            raise PermissionError('Cache namespace cannot be a symbolic link')
        os.makedirs(folder, mode=0o700, exist_ok=True)
    return user_root


def _is_owned_cache_path(path, user_root):
    """Do not follow paths (including symlinks) outside this user's cache."""
    try:
        resolved_root = os.path.realpath(user_root)
        resolved_path = os.path.realpath(path)
        return (resolved_path != resolved_root and
                os.path.commonpath([resolved_root, resolved_path]) == resolved_root)
    except (OSError, TypeError, ValueError):
        return False


def get_cache_dir_for_config(config):
    """
    Deterministic cache folder for one monitoring run: the same region + date
    range + settings always resolve to the same folder, so a resume finds the
    months that were already downloaded instead of starting over.
    """
    try:
        payload = {
            'coords': [[round(float(x), 6), round(float(y), 6)] for x, y in config['polygon_coords']],
            'start': config['start_date'],
            'end': config['end_date'],
            'scale': config.get('scale', 10),
            'cloud': config.get('cloudy_pixel_percentage', CLOUD_THRESHOLD),
        }
        if config.get('pipeline_version'):
            payload['pipeline_version'] = config['pipeline_version']
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Invalid monitoring configuration for cache identity') from exc

    user_root = _user_cache_root()
    folder = os.path.join(user_root, key)
    if not _is_owned_cache_path(folder, user_root):
        raise PermissionError('Cache path is outside the authenticated account')
    os.makedirs(folder, mode=0o700, exist_ok=True)
    return folder


class CheckpointWriteError(OSError):
    """The persistent run configuration or progress could not be committed."""


class CheckpointReadError(CheckpointWriteError):
    """Existing progress is unreadable or invalid and must not be overwritten."""


def save_run_config(cache_dir, config):
    """
    Store run metadata next to its downloaded files for inspection/recovery.

    Resume currently uses processing_config from the same Streamlit session.
    A new session does not automatically load this file or adopt another run;
    recovery also requires the server's cached files to remain available.
    """
    if not _write_cache_json(os.path.join(cache_dir, RUN_CONFIG_FILE), config):
        raise CheckpointWriteError('Could not save the monitoring configuration')


def _write_cache_json(path, payload):
    """Commit a checkpoint only after a complete write in the same directory."""
    pending_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=os.path.dirname(path),
                                         prefix='.checkpoint_', suffix='.tmp', delete=False) as stream:
            pending_path = stream.name
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending_path, path)
        return True
    except Exception:
        return False
    finally:
        if pending_path and os.path.exists(pending_path):
            try:
                os.remove(pending_path)
            except OSError:
                pass


def _checkpoint_object(pairs):
    """Reject ambiguous duplicate JSON keys instead of silently losing data."""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('Duplicate checkpoint key')
        obj[key] = value
    return obj


def _reject_nonfinite_checkpoint(value):
    raise ValueError(f'Invalid checkpoint number: {value}')


def _read_run_checkpoint(cache_dir):
    """Read and validate the whole ledger; only a missing file is an empty run."""
    path = os.path.join(cache_dir, RUN_STATE_FILE)
    try:
        if os.path.islink(path):
            raise ValueError('Checkpoint cannot be a symbolic link')
        with open(path, encoding='utf-8') as stream:
            state = json.load(stream, object_pairs_hook=_checkpoint_object,
                              parse_constant=_reject_nonfinite_checkpoint)
    except FileNotFoundError:
        return {'version': 1, 'parameters': {}}
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise CheckpointReadError('Could not read the monitoring progress') from exc
    if (not isinstance(state, dict) or type(state.get('version')) is not int
            or state['version'] != 1 or not isinstance(state.get('parameters'), dict)):
        raise CheckpointReadError('Invalid monitoring progress structure')
    allowed = (STATUS_COMPLETE, STATUS_NO_DATA, STATUS_FAILED, STATUS_SKIPPED)
    for parameter, statuses in state['parameters'].items():
        if not isinstance(parameter, str) or not isinstance(statuses, dict):
            raise CheckpointReadError('Invalid monitoring progress parameter')
        for month, entry in statuses.items():
            if (not isinstance(month, str) or not isinstance(entry, dict)
                    or entry.get('status') not in allowed):
                raise CheckpointReadError('Invalid monitoring progress status')
    return state


def _load_month_checkpoint(cache_dir, parameter_type):
    """Restore one parameter from the selected run's validated ledger."""
    return _read_run_checkpoint(cache_dir)['parameters'].get(parameter_type, {})


def _save_month_checkpoint(cache_dir, parameter_type, statuses):
    """Persist progress immediately, matching the reference app's per-month ledger."""
    state = _read_run_checkpoint(cache_dir)
    if not isinstance(parameter_type, str) or not isinstance(statuses, dict):
        raise CheckpointWriteError('Invalid monitoring progress parameter')
    allowed = (STATUS_COMPLETE, STATUS_NO_DATA, STATUS_FAILED, STATUS_SKIPPED)
    if any(not isinstance(month, str) or not isinstance(entry, dict)
           or entry.get('status') not in allowed for month, entry in statuses.items()):
        raise CheckpointWriteError('Invalid monitoring progress status')
    # Retain future/unknown parameters and top-level metadata when updating one
    # parameter; constructing a new dict from known parameters loses data.
    state['parameters'][parameter_type] = statuses
    saved = _write_cache_json(os.path.join(cache_dir, RUN_STATE_FILE), state)
    if not saved:
        raise CheckpointWriteError('Could not save the monitoring progress')
    return True


def _cached_water_thumbnail(wq_path, rgb_path, month_name, parameter_type, value_range=None):
    """Reuse completed processing like change_detection_app's classification cache.

    Store lossless PNGs and plain JSON instead of loading executable pickle files.
    CDOM's common display range is part of the identity, so added months cannot
    leave its older thumbnails on a different colour scale.
    """
    from .processing import generate_thumbnails
    from .quality import _finite_monthly_value
    try:
        sources = []
        for path in (wq_path, rgb_path):
            stat = os.stat(path)
            sources.append([os.path.abspath(path), stat.st_size, stat.st_mtime_ns])
        identity = {'version': 1, 'sources': sources, 'parameter': parameter_type,
                    'month': month_name, 'range': list(value_range) if value_range else None}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        folder = os.path.join(os.path.dirname(wq_path), 'processed_cache')
        prefix = os.path.join(folder, key)
        metadata_path = prefix + '.json'
        try:
            with open(metadata_path, encoding='utf-8') as stream:
                metadata = json.load(stream)
            if metadata['signature'] != key:
                raise ValueError('Cache identity mismatch')
            with Image.open(prefix + '_rgb.png') as source:
                rgb_image = source.copy()
            with Image.open(prefix + '_wq.png') as source:
                wq_image = source.copy()
            return {'rgb_image': rgb_image, 'wq_image': wq_image, 'month_name': month_name,
                    'mean_value': float(metadata['mean']) if metadata['mean'] is not None else np.nan,
                    'water_coverage': float(metadata['coverage']),
                    'valid_pixels': int(metadata['valid_pixels']), 'parameter_type': parameter_type}
        except (OSError, ValueError, TypeError, KeyError):
            pass
    except (OSError, ValueError, TypeError):
        prefix = None

    thumb = generate_thumbnails(wq_path, rgb_path, month_name, parameter_type, value_range=value_range)
    if thumb is not None and prefix is not None:
        try:
            os.makedirs(folder, exist_ok=True)
            # Metadata is committed last and is the completion marker.
            for suffix, image in (('_rgb.png', thumb['rgb_image']), ('_wq.png', thumb['wq_image'])):
                with tempfile.NamedTemporaryFile(dir=folder, suffix='.png', delete=False) as stream:
                    image_path = stream.name
                try:
                    image.save(image_path, format='PNG')
                    os.replace(image_path, prefix + suffix)
                finally:
                    if os.path.exists(image_path):
                        os.remove(image_path)
            _write_cache_json(metadata_path, {
                'signature': key, 'mean': _finite_monthly_value(thumb['mean_value']),
                'coverage': float(thumb['water_coverage']), 'valid_pixels': int(thumb['valid_pixels']),
            })
        except (OSError, ValueError, TypeError, KeyError):
            pass  # Cache failure never discards a scientifically computed result.
    return thumb


def _prepare_monitoring_run(config):
    """Start the selected run while retaining its downloaded files, as in the reference."""
    from .quality import empty_param_dict
    from .auth import AuthStore
    from .history import HistoryStore, expert_token
    if st.session_state.get('history_unsaved'):
        raise ValueError('Expert changes have not been saved')
    actor = st.session_state['auth_user_id']
    store = HistoryStore(AuthStore())
    # Reuse only this account's files. A new archive retains a separate copy,
    # so subsequent runs cannot overwrite an earlier analysis.
    source = get_cache_dir_for_config(config)
    source_run = None
    for earlier in store.list_runs(actor):
        if (earlier.owner_id == actor and
                json.dumps(earlier.config, sort_keys=True) == json.dumps(config, sort_keys=True)):
            candidate = str(store.run_dir(actor, earlier.id))
            if any(name == RUN_STATE_FILE or name.startswith(('wq_index_', 'rgb_', 'bands_'))
                   for name in os.listdir(candidate)):
                source = candidate
                source_run = earlier.id
                break
    run = store.create(actor, config)
    destination = str(store.run_dir(actor, run.id))
    try:
        if source_run:
            with store.run_lock(actor, source_run):
                _copy_reusable_files(source, destination)
        else:
            _copy_reusable_files(source, destination)
    except Exception:
        store.update_status(actor, run.id, 'failed')
        raise
    try:
        save_run_config(destination, config)
    except Exception:
        store.update_status(actor, run.id, 'failed')
        raise
    st.session_state.active_history_id = run.id
    st.session_state.processing_config = config
    st.session_state.current_temp_dir = destination
    st.session_state.downloaded_months = empty_param_dict(dict)
    st.session_state.month_statuses = empty_param_dict(dict)
    st.session_state.results = empty_param_dict(list)
    st.session_state.mean_data = empty_param_dict(dict)
    st.session_state.download_summary = {}
    st.session_state.shared_scene_counts = {}
    st.session_state.cdom_display_range = None
    st.session_state.expert_analysis_json = None
    st.session_state.expert_analysis_signature = None
    st.session_state.executive_summary = None
    st.session_state.executive_summary_signature = None
    st.session_state.expert_chat_history = []
    st.session_state.param_briefs = {}
    st.session_state.param_brief_signatures = {}
    st.session_state.history_expert_token = expert_token({})
    st.session_state.history_generation = run.generation
    st.session_state.processing_complete = False
    st.session_state.processing_in_progress = True
    st.session_state.resume_after_interruption = False
    st.session_state.last_run_error = None
    st.session_state.pending_run = 'start'


def _copy_reusable_files(source, destination):
    """Copy only previous processing artifacts, never chat or archive metadata."""
    for name in os.listdir(source):
        src = os.path.join(source, name)
        dst = os.path.join(destination, name)
        if os.path.islink(src):
            raise OSError('Symbolic links are not accepted in processing cache')
        if os.path.isfile(src) and (name in (RUN_STATE_FILE, 'shared_months.json') or
                ((name.startswith('wq_index_') or name.startswith('rgb_') or
                  name.startswith('shared_month_')) and name.endswith('.tif'))):
            shutil.copy2(src, dst)
        elif os.path.isdir(src) and name.startswith('bands_'):
            for root, dirs, files in os.walk(src, followlinks=False):
                if any(os.path.islink(os.path.join(root, child)) for child in dirs + files):
                    raise OSError('Symbolic links are not accepted in processing cache')
            shutil.copytree(src, dst)


def _cache_dir_for_active_run(config):
    """Keep the reference app's session directory when it belongs to this run."""
    run_id = st.session_state.get('active_history_id')
    if run_id:
        from .auth import AuthStore
        from .history import HistoryStore
        actor = st.session_state['auth_user_id']
        store = HistoryStore(AuthStore())
        run = store.get_run(actor, run_id)
        if json.dumps(run.config, sort_keys=True) != json.dumps(config, sort_keys=True):
            raise ValueError('Active analysis configuration does not match its archive')
        return str(store.run_dir(actor, run_id))
    expected_dir = get_cache_dir_for_config(config)
    current_dir = st.session_state.get('current_temp_dir')
    if (current_dir and os.path.realpath(current_dir) == os.path.realpath(expected_dir)
            and os.path.isdir(current_dir)):
        try:
            with open(os.path.join(current_dir, RUN_CONFIG_FILE), encoding='utf-8') as stream:
                saved = json.load(stream)
            if json.dumps(saved, sort_keys=True) == json.dumps(config, sort_keys=True):
                return current_dir
        except (OSError, ValueError, TypeError):
            pass
    return expected_dir


def _failure_message(error):
    """Keep the cause useful without displaying signed URL query credentials."""
    import re
    if isinstance(error, requests.exceptions.ProxyError):
        return 'اتصال به پراکسی برقرار نشد؛ تنظیمات پراکسی و شبکه را بررسی کنید.'
    message = str(error).strip() or type(error).__name__
    if 'earth engine initialization failed' in message.lower():
        return initialization_error_message(message)
    message = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', message)
    return re.sub(r'(https?://[^?\s]+)\?[^\s]+', r'\1?[redacted]', message)[:1500]


def _is_transient_error(error):
    message = _failure_message(error).lower()
    return isinstance(error, (ConnectionError, TimeoutError, requests.exceptions.Timeout,
                              requests.exceptions.ConnectionError)) or any(
        token in message for token in (
            'timeout', 'timed out', 'connection', 'temporar', 'network', 'dns',
            'remote end closed', 'reset by peer', 'deadline exceeded', 'rate limit',
            'too many requests', '429', '500', '502', '503', '504', 'service unavailable',
        )
    )


def _retry_ee_request(request):
    """Retry a transient metadata error without losing the current month."""
    for attempt in range(MAX_RETRIES):
        try:
            return request()
        except Exception as error:
            if not _is_transient_error(error) or attempt == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY_BASE ** (attempt + 1))


def _recover_interrupted_run():
    """A new script execution cannot inherit a lock from an abandoned one."""
    if st.session_state.processing_config is None:
        st.session_state.pending_run = None
        st.session_state.processing_in_progress = False
        return
    if st.session_state.processing_in_progress and not st.session_state.get('pending_run'):
        st.session_state.processing_in_progress = False
        st.session_state.resume_after_interruption = bool(st.session_state.processing_config)


def _execute_monitoring_run(config, resume):
    """Always release controls; a failed run resumes only on an explicit click."""
    from .interface import _has_any_results
    from .history import HistoryConflict
    from .processing import run_full_analysis
    st.session_state.pending_run = None
    st.session_state.processing_in_progress = True
    st.session_state.resume_after_interruption = False
    st.session_state.last_run_error = None
    run_id = st.session_state.get('active_history_id')
    store = None
    run_lock = None
    locked = False
    started = False
    actor = st.session_state.get('auth_user_id')
    try:
        if run_id:
            from .auth import AuthStore
            from .history import HistoryStore
            store = HistoryStore(AuthStore())
            run_lock = store.run_lock(actor, run_id)
            run_lock.__enter__()
            locked = True
            current = store.begin_processing(actor, run_id, st.session_state.get('history_generation'))
            started = True
            st.session_state.history_generation = current.generation
        aoi = None

        def resolve_aoi():
            nonlocal aoi
            if aoi is None:
                ee_ok, ee_msg = initialize_earth_engine()
                st.session_state.ee_initialized = ee_ok
                if not ee_ok:
                    raise EarthEngineInitializationError(ee_msg)
                aoi = ee.Geometry.Polygon([config['polygon_coords']])
            return aoi

        # Restoring complete TIFFs/thumbnails (including RGB assembly) is local.
        # Authenticate only if a missing month actually requires a server call.
        if config.get('pipeline_version') == 'shared_v1':
            from .shared_processing import run_shared_analysis
            success = run_shared_analysis(resolve_aoi, config)
        else:
            success = run_full_analysis(resolve_aoi, config['start_date'], config['end_date'],
                                        config.get('cloudy_pixel_percentage', CLOUD_THRESHOLD),
                                        config.get('scale', 10), resume=resume)
        st.session_state.processing_complete = bool(success or _has_any_results())
        if not success and not _has_any_results() and not st.session_state.resume_after_interruption:
            st.warning('داده‌ای برای این منطقه و بازه زمانی یافت نشد.')
    except Exception as error:
        st.session_state.resume_after_interruption = True
        st.session_state.last_run_error = _failure_message(error)
        if isinstance(error, HistoryConflict):
            st.error('نتایج این تحلیل تغییر کرده‌اند؛ آن را از تاریخچه دوباره باز کنید.')
        else:
            st.error('پایش کامل نشد. فایل‌های دریافت‌شده حفظ شده‌اند؛ دکمه «ادامه از محل قطع» را بزنید.')
    except BaseException:
        # Streamlit rerun/stop signals must propagate, but never leave the UI locked.
        st.session_state.resume_after_interruption = True
        raise
    finally:
        st.session_state.processing_in_progress = False
        if store is not None and locked:
            try:
                if started:
                    store.save_state(actor, run_id, {
                        'download_summary': st.session_state.get('download_summary', {}),
                    })
                    status = 'incomplete' if st.session_state.resume_after_interruption else 'complete'
                    store.update_status(actor, run_id, status)
            except Exception:
                st.session_state.resume_after_interruption = True
                st.session_state.last_run_error = 'ذخیره وضعیت تاریخچه انجام نشد.'
                st.error('ذخیره وضعیت تاریخچه انجام نشد؛ فضای ذخیره و دسترسی‌ها را بررسی کنید.')
            finally:
                run_lock.__exit__(None, None, None)


def reset_run_cache(cache_dir):
    """Empty a run's cache folder — used when the user starts a FRESH run."""
    user_root = _user_cache_root()
    try:
        if not cache_dir or not os.path.isdir(cache_dir):
            return
        if not _is_owned_cache_path(cache_dir, user_root):
            return  # never touch another user's cache or the namespace root
        for name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, name)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
            except Exception:
                continue
    except Exception:
        pass


def prune_old_cache_dirs(max_age_days=CACHE_MAX_AGE_DAYS):
    """Delete only this account's run folders past the retention window."""
    root = _user_cache_root()
    cutoff = time.time() - max_age_days * 86400
    try:
        for name in os.listdir(root):
            folder = os.path.join(root, name)
            try:
                if (not os.path.islink(folder) and _is_owned_cache_path(folder, root)
                        and os.path.isdir(folder) and os.path.getmtime(folder) < cutoff):
                    shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                continue
    except Exception:
        pass


def validate_geotiff_file(file_path, expected_bands=1):
    """Validate that a GeoTIFF file is complete and readable."""
    try:
        if not os.path.exists(file_path):
            return False, "File does not exist"

        if os.path.getsize(file_path) == 0:
            return False, "File is empty"

        with rasterio.open(file_path) as src:
            if src.count < expected_bands:
                return False, f"Wrong band count ({src.count}, expected {expected_bands})"
            # A readable TIFF header does not prove its compressed pixel blocks
            # survived a dropped transfer. Read block-by-block to bound memory.
            for band_index in range(1, src.count + 1):
                for _, window in src.block_windows(band_index):
                    src.read(band_index, window=window)

        return True, "File is valid"

    except Exception as e:
        return False, f"Validation error: {str(e)}"

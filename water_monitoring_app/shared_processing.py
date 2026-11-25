"""Monthly processing for the shared Sentinel-2 pipeline."""
import datetime
import json
import os

import streamlit as st

from .config import (ALL_PARAMETERS, NETWORK_FAILURE_STREAK, PARAM_CDOM, PARAM_CHLOROPHYLL, PARAM_TURBIDITY,
                     STATUS_COMPLETE, STATUS_FAILED, STATUS_NO_DATA)
from .quality import empty_param_dict


VERSION = 'shared_v1'
MANIFEST = 'shared_months.json'
SHORT = {PARAM_TURBIDITY: 'turbidity', PARAM_CHLOROPHYLL: 'chlorophyll', PARAM_CDOM: 'cdom'}


def _read_manifest(folder):
    from .cache import CheckpointReadError, _checkpoint_object, _reject_nonfinite_checkpoint
    path = os.path.join(folder, MANIFEST)
    try:
        if os.path.islink(path):
            raise ValueError('Shared progress cannot be a symbolic link')
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream, object_pairs_hook=_checkpoint_object,
                             parse_constant=_reject_nonfinite_checkpoint)
    except FileNotFoundError:
        return {'version': 1, 'months': {}}
    except (OSError, ValueError, UnicodeError) as exc:
        raise CheckpointReadError('Could not read shared progress') from exc
    if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1
            or not isinstance(data.get('months'), dict)):
        raise CheckpointReadError('Invalid shared progress')
    for month, item in data['months'].items():
        if (not isinstance(month, str) or not isinstance(item, dict)
                or type(item.get('scene_count')) is not int or item['scene_count'] < 0):
            raise CheckpointReadError('Invalid shared scene count')
        try:
            if datetime.date.fromisoformat(month + '-01').strftime('%Y-%m') != month:
                raise ValueError('Invalid month')
        except (TypeError, ValueError) as exc:
            raise CheckpointReadError('Invalid shared month') from exc
    return data


def _save_manifest(folder, data):
    from .cache import CheckpointWriteError, _write_cache_json
    if not _write_cache_json(os.path.join(folder, MANIFEST), data):
        raise CheckpointWriteError('Could not save shared progress')


def _existing_month_outputs(folder, month):
    from .cache import validate_geotiff_file
    outputs = {}
    any_valid = False
    for short in SHORT.values():
        wq = os.path.join(folder, f'wq_index_{short}_{month}.tif')
        rgb = os.path.join(folder, f'rgb_{short}_{month}.tif')
        wq_ok = validate_geotiff_file(wq, expected_bands=1)[0]
        rgb_ok = validate_geotiff_file(rgb, expected_bands=3)[0]
        any_valid = any_valid or wq_ok or rgb_ok
        if wq_ok and rgb_ok:
            outputs[short] = {'wq_index': wq, 'rgb': rgb}
    return outputs, any_valid


def run_shared_analysis(aoi, config):
    """Process each month once and retain the existing per-index result format."""
    from .cache import (CheckpointWriteError, _cache_dir_for_active_run, _cached_water_thumbnail,
                        _failure_message, _is_transient_error, _retry_ee_request,
                        _load_month_checkpoint, _save_month_checkpoint, prune_old_cache_dirs, save_run_config,
                        validate_geotiff_file)
    from .processing import compute_cdom_display_range
    from .earth_engine_errors import EarthEngineInitializationError
    from .shared_satellite import create_shared_collection, download_shared_month, monthly_shared_composite, split_shared_month

    folder = _cache_dir_for_active_run(config)
    save_run_config(folder, config)
    st.session_state.current_temp_dir = folder
    prune_old_cache_dirs()
    start = datetime.date.fromisoformat(config['start_date'])
    end = datetime.date.fromisoformat(config['end_date'])
    count = (end.year - start.year) * 12 + end.month - start.month
    if count <= 0:
        raise ValueError('Invalid monitoring period')
    months = [(start.year + (start.month - 1 + i) // 12,
               (start.month - 1 + i) % 12 + 1) for i in range(count)]
    manifest = _read_manifest(folder)
    st.session_state.downloaded_months = empty_param_dict(dict)
    selected_months = {f'{year:04d}-{month:02d}' for year, month in months}
    st.session_state.month_statuses = {
        parameter: {month: entry for month, entry in _load_month_checkpoint(folder, parameter).items()
                    if month in selected_months}
        for parameter in ALL_PARAMETERS
    }
    st.session_state.connection_interrupted = False
    collection = None
    geometry = None
    progress = st.progress(0)
    status_line = st.empty()
    failures = 0

    def checkpoint(month, state, message):
        for parameter in ALL_PARAMETERS:
            st.session_state.month_statuses[parameter][month] = {'status': state, 'message': message}
            _save_month_checkpoint(folder, parameter, st.session_state.month_statuses[parameter])

    for index, (year, month_number) in enumerate(months):
        month = f'{year:04d}-{month_number:02d}'
        shared = os.path.join(folder, f'shared_month_{month}.tif')
        entry = manifest['months'].get(month)
        try:
            if entry is not None and entry['scene_count'] == 0:
                status_line.markdown(f'ماه {month}: نتیجه قبلی «بدون تصویر» بازیابی شد.')
                checkpoint(month, STATUS_NO_DATA, 'No images')
            else:
                valid = validate_geotiff_file(shared, expected_bands=6)[0]
                existing, any_existing = _existing_month_outputs(folder, month)
                if valid and entry is None:
                    raise ValueError('تعداد صحنه‌های فایل مشترک مشخص نیست؛ برای حفظ صحت نتایج پایش جدید ایجاد کنید.')
                if not valid and entry is not None and len(existing) == 3:
                    status_line.markdown(f'ماه {month}: فایل‌های موجود بازیابی شدند.')
                    for parameter, short in SHORT.items():
                        st.session_state.downloaded_months[parameter][month] = existing[short]
                    checkpoint(month, STATUS_COMPLETE, 'Shared results restored')
                    failures = 0
                    progress.progress((index + 1) / count)
                    continue
                if not valid and any_existing:
                    raise ValueError('فایل مشترک این ماه ناقص است؛ برای جلوگیری از تغییر نتایج یک پایش جدید ایجاد کنید.')
                if not valid:
                    status_line.markdown(f'ماه {month}: در حال آماده‌سازی دریافت مشترک سه شاخص.')
                    if geometry is None:
                        geometry = aoi() if callable(aoi) else aoi
                    if collection is None:
                        collection = create_shared_collection(geometry, config['start_date'], config['end_date'],
                                                              config['cloudy_pixel_percentage'])
                    composite, scene_count = _retry_ee_request(
                        lambda: monthly_shared_composite(collection, year, month_number))
                    if type(scene_count) is not int or scene_count < 0:
                        raise ValueError('Invalid Sentinel-2 scene count')
                    manifest['months'][month] = {'scene_count': scene_count}
                    _save_manifest(folder, manifest)
                    if scene_count == 0:
                        checkpoint(month, STATUS_NO_DATA, 'No images')
                        failures = 0
                        progress.progress((index + 1) / count)
                        continue
                    status_line.markdown(f'ماه {month}: در حال دریافت مشترک سه شاخص.')
                    download_shared_month(composite, geometry, shared, config.get('scale', 10))
                else:
                    status_line.markdown(f'ماه {month}: فایل مشترک موجود بازیابی شد.')
                split = split_shared_month(shared, folder, month)
                for parameter, short in SHORT.items():
                    st.session_state.downloaded_months[parameter][month] = split[short]
                checkpoint(month, STATUS_COMPLETE, 'Shared monthly download')
            failures = 0
        except CheckpointWriteError:
            raise
        except Exception as error:
            message = _failure_message(error)
            checkpoint(month, STATUS_FAILED, message)
            st.session_state.last_run_error = f'{month}: {message}'
            if isinstance(error, EarthEngineInitializationError):
                st.session_state.connection_interrupted = True
                break
            if 'سقف دریافت مستقیم' in message:
                break  # The same extent would make later requests fail too.
            failures = failures + 1 if _is_transient_error(error) else 0
            if failures >= NETWORK_FAILURE_STREAK:
                st.session_state.connection_interrupted = True
                break
        progress.progress((index + 1) / count)

    # An interrupted retry can stop before reaching later, already completed
    # months. Keep their validated saved outputs visible without network calls.
    for year, month_number in months:
        month = f'{year:04d}-{month_number:02d}'
        entry = manifest['months'].get(month)
        if (not entry or entry['scene_count'] == 0 or
                month in st.session_state.downloaded_months[PARAM_TURBIDITY]):
            continue
        existing, _ = _existing_month_outputs(folder, month)
        if len(existing) == 3:
            for parameter, short in SHORT.items():
                st.session_state.downloaded_months[parameter][month] = existing[short]
            checkpoint(month, STATUS_COMPLETE, 'Shared results restored')

    rebuilt_results = empty_param_dict(list)
    rebuilt_means = empty_param_dict(dict)
    cdom_range = None
    for parameter in ALL_PARAMETERS:
        downloaded = st.session_state.downloaded_months[parameter]
        value_range = None
        if parameter == PARAM_CDOM and downloaded:
            value_range = compute_cdom_display_range([downloaded[m]['wq_index'] for m in sorted(downloaded)])
            cdom_range = value_range
        for month, paths in sorted(downloaded.items()):
            thumb = _cached_water_thumbnail(paths['wq_index'], paths['rgb'], month, parameter,
                                             value_range=value_range)
            if thumb is None:
                raise ValueError(f'Could not render results for {month}')
            rebuilt_results[parameter].append(thumb)
            rebuilt_means[parameter][month] = {'mean': thumb['mean_value'],
                                               'coverage': thumb['water_coverage']}
    st.session_state.results = rebuilt_results
    st.session_state.mean_data = rebuilt_means
    st.session_state.cdom_display_range = cdom_range
    completed = sum(1 for entry in st.session_state.month_statuses[PARAM_TURBIDITY].values()
                    if entry['status'] == STATUS_COMPLETE)
    available = sum(1 for month in months
                    if manifest['months'].get(f'{month[0]:04d}-{month[1]:02d}', {}).get('scene_count', 0) > 0)
    st.session_state.download_summary = {p: (completed, available) for p in ALL_PARAMETERS}
    st.session_state.shared_scene_counts = {month: entry['scene_count']
                                           for month, entry in manifest['months'].items()}
    st.session_state.resume_after_interruption = any(
        st.session_state.month_statuses[p].get(f'{year:04d}-{month:02d}', {}).get('status')
        not in (STATUS_COMPLETE, STATUS_NO_DATA)
        for p in ALL_PARAMETERS for year, month in months)
    if st.session_state.resume_after_interruption:
        status_line.warning('بخشی از پایش ناتمام است؛ می‌توانید آن را ادامه دهید.')
    else:
        status_line.markdown('✅ دریافت مشترک و پردازش هر سه شاخص پایان یافت.')
    return any(st.session_state.results[p] for p in ALL_PARAMETERS)

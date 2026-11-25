"""Processing functions for the monitoring application."""
import os
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
import rasterio
import datetime
from PIL import Image
import streamlit as st

from .config import (
    CLOUD_THRESHOLD,
    PARAM_TURBIDITY,
    PARAM_CHLOROPHYLL,
    PARAM_CDOM,
    ALL_PARAMETERS,
    CHL_VMIN,
    CHL_VMAX,
    CDOM_VMIN,
    CDOM_VMAX,
    NETWORK_FAILURE_STREAK,
    STATUS_NO_DATA,
    STATUS_COMPLETE,
    STATUS_FAILED,
)

def create_turbidity_colormap():
    colors = ['#0000FF', '#00FFFF', '#00FF00', '#FFFF00', '#FF8000', '#FF0000']
    return LinearSegmentedColormap.from_list('turbidity', colors, N=256)


def create_chlorophyll_colormap():
    colors = ['#9400D3', '#4B0082', '#0000FF', '#00FF00', '#FFFF00', '#FF7F00', '#FF0000']
    return LinearSegmentedColormap.from_list('chlorophyll', colors, N=256)


def create_cdom_colormap():
    """Clear blue water -> yellow -> amber -> dark brown (tea-coloured, humic)."""
    colors = ['#08306B', '#2171B5', '#6BAED6', '#EDF8B1', '#FEE391', '#EC7014', '#662506']
    return LinearSegmentedColormap.from_list('cdom', colors, N=256)


def compute_cdom_display_range(wq_paths, low=2, high=98):
    """
    Derive one common colour range for every CDOM month of a run.

    CDOM is an absorption coefficient, not a bounded index, so a fixed scale
    either saturates or washes out depending on the water body. The 2nd-98th
    percentile of all months pooled together keeps the colours meaningful AND
    comparable between months (which a per-image stretch would not).
    """
    samples = []
    for path in wq_paths:
        try:
            with rasterio.open(path) as src:
                data = src.read(1)[::4, ::4]  # subsample: plenty for a percentile
            finite = data[np.isfinite(data) & (data != 0)]
            if finite.size:
                samples.append(finite)
        except Exception:
            continue

    if not samples:
        return CDOM_VMIN, CDOM_VMAX

    pooled = np.concatenate(samples)
    if pooled.size == 0:
        return CDOM_VMIN, CDOM_VMAX

    vmin = float(np.percentile(pooled, low))
    vmax = float(np.percentile(pooled, high))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        return CDOM_VMIN, CDOM_VMAX
    return vmin, vmax


def generate_thumbnails(wq_path, rgb_path, month_name, parameter_type, max_size=300,
                        value_range=None):
    """Generate RGB and water quality index thumbnails."""
    try:
        with rasterio.open(wq_path) as src:
            wq_data = src.read(1)

        with rasterio.open(rgb_path) as src:
            red = src.read(1)
            green = src.read(2)
            blue = src.read(3)

        rgb = np.stack([red, green, blue], axis=-1)
        rgb = np.nan_to_num(rgb, nan=0.0)

        def percentile_stretch(band, lower=2, upper=98):
            valid = band[band > 0]
            if len(valid) == 0:
                return np.zeros_like(band, dtype=np.uint8)
            p_low = np.percentile(valid, lower)
            p_high = np.percentile(valid, upper)
            if p_high <= p_low:
                p_high = p_low + 0.001
            stretched = np.clip((band - p_low) / (p_high - p_low), 0, 1)
            return (stretched * 255).astype(np.uint8)

        rgb_uint8 = np.zeros_like(rgb, dtype=np.uint8)
        for i in range(3):
            rgb_uint8[:, :, i] = percentile_stretch(rgb[:, :, i])

        wq_valid = np.nan_to_num(wq_data, nan=np.nan)

        valid_wq = wq_valid[~np.isnan(wq_valid) & (wq_valid != 0)]
        mean_value = np.nanmean(valid_wq) if len(valid_wq) > 0 else np.nan
        valid_pixel_count = len(valid_wq)
        total_pixels = wq_data.size
        water_coverage = (valid_pixel_count / total_pixels) * 100 if total_pixels > 0 else 0

        if parameter_type == PARAM_TURBIDITY:
            cmap = create_turbidity_colormap()
            wq_normalized = np.clip((wq_valid + 0.3) / 0.6, 0, 1)
        elif parameter_type == PARAM_CDOM:
            cmap = create_cdom_colormap()
            vmin, vmax = value_range if value_range else (CDOM_VMIN, CDOM_VMAX)
            if vmax <= vmin:
                vmax = vmin + 1e-6
            wq_normalized = np.clip((wq_valid - vmin) / (vmax - vmin), 0, 1)
        else:
            cmap = create_chlorophyll_colormap()
            wq_normalized = np.clip((wq_valid - CHL_VMIN) / (CHL_VMAX - CHL_VMIN), 0, 1)

        wq_normalized = np.nan_to_num(wq_normalized, nan=0)

        wq_colored = cmap(wq_normalized)[:, :, :3]
        wq_uint8 = (wq_colored * 255).astype(np.uint8)

        water_mask = (~np.isnan(wq_valid)) & (wq_valid != 0)
        for i in range(3):
            wq_uint8[:, :, i] = np.where(water_mask, wq_uint8[:, :, i], 50)

        pil_rgb = Image.fromarray(rgb_uint8, mode='RGB')
        pil_wq = Image.fromarray(wq_uint8, mode='RGB')

        h, w = pil_rgb.size[1], pil_rgb.size[0]
        if h > max_size or w > max_size:
            scale = max_size / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            pil_rgb = pil_rgb.resize((new_w, new_h), Image.LANCZOS)
            pil_wq = pil_wq.resize((new_w, new_h), Image.LANCZOS)

        return {
            'rgb_image': pil_rgb,
            'wq_image': pil_wq,
            'month_name': month_name,
            'mean_value': mean_value,
            'water_coverage': water_coverage,
            'valid_pixels': valid_pixel_count,
            'parameter_type': parameter_type
        }

    except Exception:
        # Quietly skip a month that fails to render rather than surfacing
        # remote-sensing error internals to a non-technical user.
        return None


def process_single_parameter(aoi, start_date, end_date, parameter_type, temp_dir,
                              cloudy_pixel_percentage=CLOUD_THRESHOLD, scale=10,
                              resume=False, progress_callback=None):
    """
    Run the full pipeline for one parameter (NDTI or Chlorophyll-a):
    cloud filtering -> snow masking -> waterbody extraction -> index calculation
    -> download -> thumbnail generation.

    Key resilience behaviours (adapted from old version):
    - Per-month session state writes: progress is preserved after every month so
      that a connection drop never discards completed work.
    - Resume logic: when resume=True, months already present in
      st.session_state.downloaded_months[parameter_type] (and whose files are
      still valid on disk) are skipped entirely.
    - File-level cache: download_monthly_data() validates existing GeoTIFFs on
      disk before attempting a new download — so cached files survive page
      reloads even without session state.
    - EE server calls (.size().getInfo(), get_monthly_composite) are wrapped in
      try/except so a transient network error on one month does not crash the
      whole pipeline; the month is marked STATUS_FAILED and processing continues.

    Returns: (results_list, mean_data_dict, downloaded_count, available_count)
    No technical logs are written to the UI; this function is silent.

    progress_callback(done, total, month_name), if given, is invoked once
    before the download loop starts (to reflect months already recovered
    from cache/resume) and once after every month is handled, so the caller
    can drive a progress bar / stage label.
    """
    from .cache import _cached_water_thumbnail, _failure_message, _is_transient_error, _load_month_checkpoint, _retry_ee_request, _save_month_checkpoint, validate_geotiff_file
    from .earth_engine_errors import EarthEngineInitializationError
    from .quality import empty_param_dict
    from .satellite import create_water_quality_collection, download_monthly_data, get_monthly_composite
    param_short = {
        PARAM_TURBIDITY: "turbidity",
        PARAM_CHLOROPHYLL: "chlorophyll",
        PARAM_CDOM: "cdom",
    }[parameter_type]

    start_dt = datetime.datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.datetime.strptime(end_date, '%Y-%m-%d')
    total_months = (end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month)

    # ------------------------------------------------------------------
    # Defer the collection until a missing month actually needs Earth Engine.
    # ------------------------------------------------------------------
    wq_collection = None

    month_infos = []
    for month_index in range(total_months):
        year = start_dt.year + (start_dt.month - 1 + month_index) // 12
        month = (start_dt.month - 1 + month_index) % 12 + 1
        month_infos.append({'month_name': f"{year}-{month:02d}", 'year': year, 'month': month})
    current_months = {month['month_name'] for month in month_infos}

    # ------------------------------------------------------------------
    # FIX A: Restore already-downloaded months from session state (resume)
    # ------------------------------------------------------------------
    # Ensure the nested dict for this parameter exists in session state
    if not isinstance(st.session_state.downloaded_months.get(parameter_type), dict):
        st.session_state.downloaded_months[parameter_type] = {}
    if not isinstance(st.session_state.month_statuses.get(parameter_type), dict):
        st.session_state.month_statuses[parameter_type] = {}

    # Like the reference month_analysis_results ledger, restore progress before
    # building the remaining-month list. Scope it to this exact run and period.
    statuses = _load_month_checkpoint(temp_dir, parameter_type)
    if resume:
        statuses.update(st.session_state.month_statuses[parameter_type])
    st.session_state.month_statuses[parameter_type] = {
        month: entry for month, entry in statuses.items() if month in current_months
    }

    downloaded_months = {}

    if resume and st.session_state.downloaded_months.get(parameter_type):
        for month_name, paths in st.session_state.downloaded_months[parameter_type].items():
            if month_name not in current_months:
                continue
            if paths.get('wq_index') and paths.get('rgb'):
                if any(os.path.dirname(os.path.abspath(paths[key])) != os.path.abspath(temp_dir)
                       for key in ('wq_index', 'rgb')):
                    continue
                wq_valid, _ = validate_geotiff_file(paths['wq_index'], expected_bands=1)
                rgb_valid, _ = validate_geotiff_file(paths['rgb'], expected_bands=3)
                if wq_valid and rgb_valid:
                    downloaded_months[month_name] = paths
                    # Keep the cached status entry as well
                    if month_name not in st.session_state.month_statuses[parameter_type]:
                        st.session_state.month_statuses[parameter_type][month_name] = {
                            'status': STATUS_COMPLETE, 'message': 'Cached'
                        }

    # Also check disk for any months whose files exist but session state was lost
    # (e.g. after a page reload without resume — file-level cache recovery)
    for month_info in month_infos:
        month_name = month_info['month_name']
        if month_name in downloaded_months:
            continue
        wq_path = os.path.join(temp_dir, f"wq_index_{param_short}_{month_name}.tif")
        rgb_path = os.path.join(temp_dir, f"rgb_{param_short}_{month_name}.tif")
        wq_valid, _ = validate_geotiff_file(wq_path, expected_bands=1)
        rgb_valid, _ = validate_geotiff_file(rgb_path, expected_bands=3)
        # A disconnect/rerun can arrive after the last source band was saved
        # but before RGB assembly. Finish that local work before querying EE.
        if wq_valid and not rgb_valid:
            bands_dir = os.path.join(temp_dir, f"bands_{param_short}_{month_name}")
            if all(validate_geotiff_file(os.path.join(bands_dir, f"{band}.tif"), 1)[0]
                   for band in ('B4', 'B3', 'B2')):
                _, _, recovered_status, _ = download_monthly_data(
                    None, aoi, temp_dir, month_name, param_short, scale
                )
                rgb_valid = recovered_status == STATUS_COMPLETE
        if wq_valid and rgb_valid:
            downloaded_months[month_name] = {'wq_index': wq_path, 'rgb': rgb_path}
            st.session_state.downloaded_months[parameter_type][month_name] = downloaded_months[month_name]
            st.session_state.month_statuses[parameter_type][month_name] = {
                'status': STATUS_COMPLETE, 'message': 'Cached (disk)'
            }

    st.session_state.downloaded_months[parameter_type] = dict(downloaded_months)
    _save_month_checkpoint(temp_dir, parameter_type, st.session_state.month_statuses[parameter_type])

    # Months not yet downloaded. Only STATUS_NO_DATA counts as settled from the
    # status alone; a month marked STATUS_COMPLETE is skipped because its files
    # were just validated into downloaded_months above — relying on the status
    # by itself meant a month whose files had gone missing was skipped forever.
    already_statused = {
        m for m, s in st.session_state.month_statuses[parameter_type].items()
        if s.get('status') == STATUS_NO_DATA
    }
    months_to_process = [
        m for m in month_infos
        if m['month_name'] not in downloaded_months
        and m['month_name'] not in already_statused
    ]

    available_count = len(downloaded_months)  # start with already-recovered months

    # Progress bookkeeping: months already resolved before the loop (from the
    # resume/disk-cache recovery above) count as "done" immediately so the
    # progress bar reflects real state instead of restarting from zero.
    already_done_count = total_months - len(months_to_process)
    processed_count = already_done_count
    if progress_callback:
        progress_callback(processed_count, total_months, None)

    # ------------------------------------------------------------------
    # Per-month EE + download loop with bounded retries and immediate checkpoints.
    # ------------------------------------------------------------------
    consecutive_failures = 0

    for month_info in months_to_process:
        month_name = month_info['month_name']

        # FIX C: Wrap every EE server call so a transient error skips the month
        try:
            if callable(aoi):
                aoi = aoi()
            if wq_collection is None:
                wq_collection = create_water_quality_collection(
                    aoi, start_date, end_date, parameter_type, cloudy_pixel_percentage
                )
            composite, count, stats = _retry_ee_request(
                lambda: get_monthly_composite(
                    wq_collection, aoi, month_info['year'], month_info['month']
                )
            )
        except Exception as error:
            message = _failure_message(error)
            st.session_state.month_statuses[parameter_type][month_name] = {
                'status': STATUS_FAILED, 'message': message, 'stage': 'monthly_composite'
            }
            st.session_state.last_run_error = f"{parameter_type} / {month_name}: {message}"
            _save_month_checkpoint(temp_dir, parameter_type, st.session_state.month_statuses[parameter_type])
            processed_count += 1
            if progress_callback:
                progress_callback(processed_count, total_months, month_name)

            if isinstance(error, EarthEngineInitializationError):
                st.session_state.connection_interrupted = True
                break
            consecutive_failures = consecutive_failures + 1 if _is_transient_error(error) else 0
            if consecutive_failures >= NETWORK_FAILURE_STREAK:
                st.session_state.connection_interrupted = True
                break
            continue

        if composite is None or count == 0:
            st.session_state.month_statuses[parameter_type][month_name] = {
                'status': STATUS_NO_DATA, 'message': 'No images'
            }
            _save_month_checkpoint(temp_dir, parameter_type, st.session_state.month_statuses[parameter_type])
            consecutive_failures = 0   # the server answered: the link is fine
            processed_count += 1
            if progress_callback:
                progress_callback(processed_count, total_months, month_name)
            continue

        available_count += 1

        wq_path, rgb_path, status, message = download_monthly_data(
            composite, aoi, temp_dir, month_name, param_short, scale
        )

        # FIX D: Write to session state immediately after each month — not at end
        st.session_state.month_statuses[parameter_type][month_name] = {
            'status': status, 'message': message, 'stage': 'download'
        }

        if status == STATUS_COMPLETE:
            downloaded_months[month_name] = {'wq_index': wq_path, 'rgb': rgb_path}
            # Persist to session state right away so a crash/reload can recover
            st.session_state.downloaded_months[parameter_type][month_name] = {
                'wq_index': wq_path, 'rgb': rgb_path
            }
            consecutive_failures = 0
        else:
            st.session_state.last_run_error = f"{parameter_type} / {month_name}: {message}"
            consecutive_failures = consecutive_failures + 1 if _is_transient_error(message) else 0

        _save_month_checkpoint(temp_dir, parameter_type, st.session_state.month_statuses[parameter_type])

        processed_count += 1
        if progress_callback:
            progress_callback(processed_count, total_months, month_name)

        if consecutive_failures >= NETWORK_FAILURE_STREAK:
            st.session_state.connection_interrupted = True
            break

    # ------------------------------------------------------------------
    # Thumbnail generation (uses only successfully downloaded months)
    # ------------------------------------------------------------------
    results = []
    mean_data = {}
    if 'results' not in st.session_state:
        st.session_state.results = empty_param_dict(list)
    if 'mean_data' not in st.session_state:
        st.session_state.mean_data = empty_param_dict(dict)

    # CDOM gets one colour range shared by every month of the run (see
    # compute_cdom_display_range); the two normalized-difference indices keep
    # their fixed scales exactly as before.
    value_range = None
    if parameter_type == PARAM_CDOM and downloaded_months:
        value_range = compute_cdom_display_range(
            [downloaded_months[m]['wq_index'] for m in sorted(downloaded_months.keys())]
        )
        st.session_state.cdom_display_range = value_range

    for month_name in sorted(downloaded_months.keys()):
        paths = downloaded_months[month_name]
        thumb = _cached_water_thumbnail(paths['wq_index'], paths['rgb'], month_name, parameter_type,
                                         value_range=value_range)
        if thumb:
            results.append(thumb)
            mean_data[month_name] = {'mean': thumb['mean_value'], 'coverage': thumb['water_coverage']}
            st.session_state.results[parameter_type] = list(results)
            st.session_state.mean_data[parameter_type] = dict(mean_data)

    return results, mean_data, len(downloaded_months), available_count


def run_full_analysis(aoi, start_date, end_date, cloudy_pixel_percentage=CLOUD_THRESHOLD,
                       scale=10, resume=False):
    """
    Automatically runs preprocessing + all three index calculations (NDTI, then
    Chlorophyll-a, then CDOM) in sequence. Displays only a simple,
    user-friendly summary.

    Resilience additions vs. original:
    - resume=True is forwarded to process_single_parameter so cached months are
      skipped rather than re-downloaded.
    - Each parameter block is wrapped in try/except so a hard failure on one
      index does not prevent the others from running.
    - processing_config is written to session state here so the main() button
      handler can pass it to a resume run later.
    """
    from .cache import (CheckpointWriteError, _cache_dir_for_active_run, _failure_message,
                        _is_transient_error, prune_old_cache_dirs, save_run_config)
    st.session_state.connection_interrupted = False

    # Cache folder for this exact run (see get_cache_dir_for_config): the same
    # region + date range always maps to the same folder, so months already on
    # disk are reused instead of downloaded twice — even after the session was
    # lost. The run's configuration is stored beside the images so the app can
    # offer to resume it on a completely fresh session.
    config = st.session_state.get('processing_config')
    if not config or not config.get('polygon_coords'):
        raise ValueError('A monitoring configuration is required for a user-scoped run')
    temp_dir = _cache_dir_for_active_run(config)
    save_run_config(temp_dir, config)
    st.session_state.current_temp_dir = temp_dir
    # Atomic metadata replacement touches this run before old-run pruning.
    prune_old_cache_dirs()

    summary_placeholder = st.empty()
    download_summary = dict(st.session_state.download_summary)  # preserve any prior summary

    # ------------------------------------------------------------------
    # Progress bar + stage label — shows overall level (%) and which stage
    # (parameter + month) is currently being processed. Total work units are
    # "months × 3 parameters"; months already recovered via resume/disk cache
    # count as already-done so the bar starts from the right place on Resume.
    # ------------------------------------------------------------------
    start_dt = datetime.datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.datetime.strptime(end_date, '%Y-%m-%d')
    total_months = (end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month)
    total_units = max(total_months * len(ALL_PARAMETERS), 1)

    progress_bar = st.progress(0)
    stage_text = st.empty()

    def make_progress_callback(stage_label, unit_offset):
        def _callback(done_in_stage, total_in_stage, month_name):
            done_units = unit_offset + done_in_stage
            percent = int(min(1.0, done_units / total_units) * 100)
            if month_name:
                stage_text.markdown(
                    f"**{stage_label}** — در حال پردازش ماه «{month_name}» "
                    f"({done_in_stage} از {total_in_stage}) — {percent}٪"
                )
            else:
                stage_text.markdown(f"**{stage_label}** — در حال آماده‌سازی... — {percent}٪")
            progress_bar.progress(min(1.0, done_units / total_units))
        return _callback

    with st.spinner("در حال پایش کیفیت آب... این فرآیند ممکن است چند دقیقه طول بکشد"):
        # --- Turbidity (NDTI) ---
        turb_results, turb_mean, turb_downloaded, turb_available = [], {}, 0, 0
        try:
            turb_results, turb_mean, turb_downloaded, turb_available = process_single_parameter(
                aoi, start_date, end_date, PARAM_TURBIDITY, temp_dir,
                cloudy_pixel_percentage, scale, resume=resume,
                progress_callback=make_progress_callback("🌊 مرحله ۱ از ۳ — شاخص کدورت آب", 0)
            )
        except CheckpointWriteError:
            raise
        except Exception as error:
            st.session_state.last_run_error = f"{PARAM_TURBIDITY}: {_failure_message(error)}"
            st.session_state.connection_interrupted = _is_transient_error(error)

        # Merge with any results already in session state (resume case)
        if turb_results:
            st.session_state.results[PARAM_TURBIDITY] = turb_results
            st.session_state.mean_data[PARAM_TURBIDITY] = turb_mean
        download_summary[PARAM_TURBIDITY] = (turb_downloaded, turb_available)

        summary_placeholder.info(
            f"🌊 شاخص کدورت: {turb_downloaded} تصویر از {turb_available} تصویر موجود دریافت شد."
        )

        # --- Chlorophyll-a ---
        # If the link went down during turbidity there is no point hammering
        # Earth Engine for the next two indices: stop and let the user resume.
        chl_results, chl_mean, chl_downloaded, chl_available = [], {}, 0, 0
        try:
            if st.session_state.connection_interrupted:
                raise InterruptedError("connection lost")
            chl_results, chl_mean, chl_downloaded, chl_available = process_single_parameter(
                aoi, start_date, end_date, PARAM_CHLOROPHYLL, temp_dir,
                cloudy_pixel_percentage, scale, resume=resume,
                progress_callback=make_progress_callback("🌿 مرحله ۲ از ۳ — شاخص کلروفیل", total_months)
            )
        except CheckpointWriteError:
            raise
        except Exception as error:
            if not isinstance(error, InterruptedError):
                st.session_state.last_run_error = f"{PARAM_CHLOROPHYLL}: {_failure_message(error)}"
                st.session_state.connection_interrupted = _is_transient_error(error)

        if chl_results:
            st.session_state.results[PARAM_CHLOROPHYLL] = chl_results
            st.session_state.mean_data[PARAM_CHLOROPHYLL] = chl_mean
        download_summary[PARAM_CHLOROPHYLL] = (chl_downloaded, chl_available)

        summary_placeholder.info(
            f"🌊 شاخص کدورت: {turb_downloaded} تصویر از {turb_available} تصویر موجود دریافت شد.\n\n"
            f"🌿 شاخص کلروفیل: {chl_downloaded} تصویر از {chl_available} تصویر موجود دریافت شد."
        )

        # --- CDOM (Colored Dissolved Organic Matter) ---
        cdom_results, cdom_mean, cdom_downloaded, cdom_available = [], {}, 0, 0
        try:
            if st.session_state.connection_interrupted:
                raise InterruptedError("connection lost")
            cdom_results, cdom_mean, cdom_downloaded, cdom_available = process_single_parameter(
                aoi, start_date, end_date, PARAM_CDOM, temp_dir,
                cloudy_pixel_percentage, scale, resume=resume,
                progress_callback=make_progress_callback(
                    "🍂 مرحله ۳ از ۳ — شاخص مواد آلی محلول", total_months * 2
                )
            )
        except CheckpointWriteError:
            raise
        except Exception as error:
            if not isinstance(error, InterruptedError):
                st.session_state.last_run_error = f"{PARAM_CDOM}: {_failure_message(error)}"
                st.session_state.connection_interrupted = _is_transient_error(error)

        if cdom_results:
            st.session_state.results[PARAM_CDOM] = cdom_results
            st.session_state.mean_data[PARAM_CDOM] = cdom_mean
        download_summary[PARAM_CDOM] = (cdom_downloaded, cdom_available)

    if st.session_state.connection_interrupted:
        # Stopped on purpose the moment the link went down. Everything already
        # downloaded is on disk and in session state; the months that were never
        # attempted carry no status at all, so «ادامه از محل قطع» continues from
        # exactly this point rather than repeating earlier months.
        stage_text.markdown(
            "⛔ ارتباط با سرور قطع شد و پایش در همان نقطه متوقف شد — "
            "ماه‌های دریافت‌شده حفظ شده‌اند."
        )
        st.session_state.resume_after_interruption = True
    elif all(
        st.session_state.month_statuses.get(parameter, {}).get(
            f"{start_dt.year + (start_dt.month - 1 + index) // 12}-"
            f"{(start_dt.month - 1 + index) % 12 + 1:02d}", {}
        ).get('status') in (STATUS_COMPLETE, STATUS_NO_DATA)
        for parameter in ALL_PARAMETERS for index in range(total_months)
    ):
        progress_bar.progress(1.0)
        stage_text.markdown("✅ پردازش هر سه شاخص به پایان رسید — ۱۰۰٪")
    else:
        st.session_state.resume_after_interruption = True
        stage_text.warning('بخشی از پایش هنوز تکمیل نشده است؛ فایل‌ها حفظ شده‌اند و می‌توانید ادامه دهید.')

    st.session_state.download_summary = download_summary

    has_any_results = any(
        bool(st.session_state.results.get(p)) for p in ALL_PARAMETERS
    )
    return has_any_results

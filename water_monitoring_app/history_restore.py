"""Reconstruct a saved run locally without requesting satellite or AI data."""
from datetime import date
from pathlib import Path

from shapely.geometry import Polygon

from .cache import _cached_water_thumbnail, _load_month_checkpoint, validate_geotiff_file
from .config import (ALL_PARAMETERS, PARAM_CDOM, PARAM_CHLOROPHYLL, PARAM_TURBIDITY,
                     STATUS_COMPLETE, STATUS_NO_DATA)
from .expert_snapshot import load_expert_state
from .history import expert_token
from .processing import compute_cdom_display_range
from .quality import empty_param_dict


_SHORT = {PARAM_TURBIDITY: 'turbidity', PARAM_CHLOROPHYLL: 'chlorophyll', PARAM_CDOM: 'cdom'}


def restore_run(store, actor_id, run_id, session):
    """Authorise and restore a run into session only after all reads succeed.

    TIFFs are validated and thumbnails/means are rebuilt with the same functions
    used by the processing pipeline. Missing files leave a resumable run rather
    than triggering a network request. No untrusted path comes from a browser.
    """
    if session.get('history_unsaved'):
        raise ValueError('Unsaved expert changes must be resolved first')
    with store.run_lock(actor_id, run_id):
        return _restore_locked(store, actor_id, run_id, session)


def _restore_locked(store, actor_id, run_id, session):
    run = store.get_run(actor_id, run_id)
    folder = Path(store.run_dir(actor_id, run_id))
    config = run.config
    start = date.fromisoformat(config['start_date'])
    end = date.fromisoformat(config['end_date'])
    count = (end.year - start.year) * 12 + end.month - start.month
    if count <= 0:
        raise ValueError('Invalid stored analysis period')
    polygon = Polygon(config['polygon_coords'])
    if polygon.is_empty or not polygon.is_valid:
        raise ValueError('Invalid stored analysis region')
    months = [f'{start.year + (start.month - 1 + i) // 12:04d}-'
              f'{(start.month - 1 + i) % 12 + 1:02d}' for i in range(count)]

    results = empty_param_dict(list)
    means = empty_param_dict(dict)
    downloaded = empty_param_dict(dict)
    statuses = empty_param_dict(dict)
    cdom_range = None
    incomplete = False
    for parameter in ALL_PARAMETERS:
        statuses[parameter] = _load_month_checkpoint(str(folder), parameter)
        for month in months:
            wq = folder / f'wq_index_{_SHORT[parameter]}_{month}.tif'
            rgb = folder / f'rgb_{_SHORT[parameter]}_{month}.tif'
            if validate_geotiff_file(str(wq), expected_bands=1)[0] and \
                    validate_geotiff_file(str(rgb), expected_bands=3)[0]:
                downloaded[parameter][month] = {'wq_index': str(wq), 'rgb': str(rgb)}
                statuses[parameter][month] = {'status': STATUS_COMPLETE, 'message': 'Cached (disk)'}
            elif statuses[parameter].get(month, {}).get('status') != STATUS_NO_DATA:
                incomplete = True
        value_range = None
        if parameter == PARAM_CDOM and downloaded[parameter]:
            value_range = compute_cdom_display_range(
                [downloaded[parameter][m]['wq_index'] for m in sorted(downloaded[parameter])])
            cdom_range = value_range
        for month, paths in sorted(downloaded[parameter].items()):
            thumb = _cached_water_thumbnail(paths['wq_index'], paths['rgb'], month, parameter,
                                             value_range=value_range)
            if thumb is None:
                raise ValueError('A stored analysis image could not be reconstructed')
            results[parameter].append(thumb)
            means[parameter][month] = {'mean': thumb['mean_value'], 'coverage': thumb['water_coverage']}

    expert_payload = store.load_expert(actor_id, run_id)
    expert = load_expert_state(expert_payload) if expert_payload else {
        'expert_analysis_json': None, 'expert_analysis_signature': None,
        'executive_summary': None, 'executive_summary_signature': None,
        'expert_chat_history': [], 'param_briefs': {}, 'param_brief_signatures': {},
    }
    saved_state = store.load_state(actor_id, run_id) or {}
    scene_counts = {}
    if config.get('pipeline_version') == 'shared_v1':
        from .shared_processing import _read_manifest
        scene_counts = {month: entry['scene_count'] for month, entry in _read_manifest(str(folder))['months'].items()}
    summary = saved_state.get('download_summary', {})
    if not isinstance(summary, dict):
        raise ValueError('Invalid saved analysis state')
    # A hard interruption can leave the end-of-run counts older than the
    # individually committed TIFFs. Do not display obsolete totals as fact.
    if any(parameter not in ALL_PARAMETERS or not isinstance(counts, (list, tuple))
           or len(counts) != 2 or not all(isinstance(value, int) and not isinstance(value, bool)
                                          and value >= 0 for value in counts)
           or counts[0] != len(downloaded[parameter])
           for parameter, counts in summary.items()):
        summary = {}
    replacement = {
        'active_history_id': run.id, 'processing_config': config,
        'history_expert_token': expert_token(expert_payload or {}),
        'history_generation': run.generation,
        'history_unsaved': False,
        'current_temp_dir': str(folder), 'downloaded_months': downloaded,
        'month_statuses': statuses, 'results': results, 'mean_data': means,
        'download_summary': summary, 'cdom_display_range': cdom_range,
        'shared_scene_counts': scene_counts,
        'drawn_polygons': [polygon], 'last_drawn_polygon': None,
        'selected_region_index': 0, 'processing_complete': any(results.values()),
        'processing_in_progress': False, 'resume_after_interruption': incomplete,
        'connection_interrupted': False, 'pending_run': None, 'last_run_error': None,
        'active_page': 'setup', 'start_year': start.year, 'start_month': start.month,
        'end_year': end.year, 'end_month': end.month,
        'map_version': session.get('map_version', 0) + 1,
    }
    replacement.update(expert)
    session.update(replacement)
    return run

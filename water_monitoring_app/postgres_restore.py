"""Restore scalar history from PostgreSQL without requesting imagery."""
from datetime import date

from shapely.geometry import Polygon

from .config import ALL_PARAMETERS
from .expert_snapshot import load_expert_state
from .history import expert_token
from .numeric_snapshot import load_numeric_results
from .quality import empty_param_dict


def restore_postgres_run(store, actor_id, run_id, session):
    """Prepare a complete replacement before changing the active session.

    Images are intentionally absent: the result renderer tells the user that
    imagery must be regenerated. No Earth Engine or model calls are made here.
    This standalone function is not connected to the deployed UI yet.
    """
    if session.get('history_unsaved'):
        raise ValueError('Unsaved expert changes must be resolved first')
    run, state, expert_payload, months = store.load_run_snapshot(actor_id, run_id)
    if not isinstance(state, dict):
        raise ValueError('Invalid stored state')
    config = run.config
    start = date.fromisoformat(config['start_date'])
    end = date.fromisoformat(config['end_date'])
    count = (end.year - start.year) * 12 + end.month - start.month
    if count <= 0:
        raise ValueError('Invalid stored period')
    polygon = Polygon(config['polygon_coords'])
    if polygon.is_empty or not polygon.is_valid:
        raise ValueError('Invalid stored region')
    period = {f'{start.year + (start.month - 1 + i) // 12:04d}-'
              f'{(start.month - 1 + i) % 12 + 1:02d}' for i in range(count)}
    statuses = empty_param_dict(dict)
    numeric = empty_param_dict(list)
    for item in months:
        parameter, month, status = item['parameter'], item['month'], item['status']
        if parameter not in ALL_PARAMETERS or month not in period or status not in ('complete', 'no_data', 'failed', 'skipped'):
            raise ValueError('Invalid stored month')
        statuses[parameter][month] = {'status': status, 'message': item.get('message') or '',
                                      'stage': item.get('stage')}
        if status == 'complete':
            numeric[parameter].append({'month_name': month, 'mean_value': item['mean_value'],
                                       'water_coverage': item['water_coverage'],
                                       'valid_pixels': item['valid_pixels']})
    results = load_numeric_results({'version': 1, 'parameters': numeric})
    means = {parameter: {row['month_name']: {'mean': row['mean_value'], 'coverage': row['water_coverage']}
                         for row in rows} for parameter, rows in results.items()}
    incomplete = any(statuses[p].get(month, {}).get('status') not in ('complete', 'no_data')
                     for p in ALL_PARAMETERS for month in period)
    expert = load_expert_state(expert_payload) if expert_payload else {
        'expert_analysis_json': None, 'expert_analysis_signature': None,
        'executive_summary': None, 'executive_summary_signature': None,
        'expert_chat_history': [], 'param_briefs': {}, 'param_brief_signatures': {},
    }
    summary = state.get('download_summary', {})
    if not isinstance(summary, dict):
        raise ValueError('Invalid stored counts')
    scene_counts = state.get('shared_scene_counts', {})
    if (not isinstance(scene_counts, dict) or
            any(month not in period or type(count) is not int or count < 0
                for month, count in scene_counts.items())):
        raise ValueError('Invalid shared scene counts')
    replacement = {
        'active_history_id': run.id, 'history_generation': run.generation,
        'history_expert_token': expert_token(expert_payload), 'history_unsaved': False,
        'processing_config': config, 'current_temp_dir': None,
        'downloaded_months': empty_param_dict(dict), 'month_statuses': statuses,
        'results': results, 'mean_data': means, 'download_summary': summary,
        'shared_scene_counts': scene_counts,
        'cdom_display_range': state.get('cdom_display_range'),
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

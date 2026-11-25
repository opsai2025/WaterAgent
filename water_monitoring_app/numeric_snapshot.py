"""JSON-safe numeric analysis results, deliberately excluding raster images."""
import math
import re

from .config import ALL_PARAMETERS


def _number(value, field, *, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        raise ValueError(f'Invalid {field}')
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'Invalid {field}') from exc
    if not math.isfinite(value):
        if nullable and math.isnan(value):
            return None
        raise ValueError(f'Invalid {field}')
    return value


def dump_numeric_results(results):
    """Build a versioned, image-free snapshot without rounding scientific values."""
    if not isinstance(results, dict) or set(results) - set(ALL_PARAMETERS):
        raise ValueError('Invalid parameters')
    output = {}
    for parameter in ALL_PARAMETERS:
        rows = results.get(parameter, [])
        if not isinstance(rows, list):
            raise ValueError('Invalid result rows')
        output[parameter] = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Invalid result row')
            month = row.get('month_name')
            if not isinstance(month, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month) or month in seen:
                raise ValueError('Invalid or duplicate result month')
            seen.add(month)
            mean = _number(row.get('mean_value'), 'mean', nullable=True)
            coverage = _number(row.get('water_coverage'), 'coverage')
            pixels = row.get('valid_pixels')
            if not 0 <= coverage <= 100 or isinstance(pixels, bool):
                raise ValueError('Invalid coverage or pixel count')
            try:
                count = int(pixels)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError('Invalid pixel count') from exc
            if count < 0 or count != pixels:
                raise ValueError('Invalid pixel count')
            output[parameter].append({'month_name': month, 'mean_value': mean,
                                      'water_coverage': coverage, 'valid_pixels': count})
    return {'version': 1, 'parameters': output}


def load_numeric_results(snapshot):
    """Validate a snapshot and restore the NaN convention for missing means."""
    if not isinstance(snapshot, dict) or type(snapshot.get('version')) is not int or snapshot['version'] != 1:
        raise ValueError('Invalid numeric snapshot version')
    validated = dump_numeric_results(snapshot.get('parameters'))
    results = validated['parameters']
    for parameter, rows in results.items():
        for row in rows:
            if row['mean_value'] is None:
                row['mean_value'] = float('nan')
            row['parameter_type'] = parameter
    return results

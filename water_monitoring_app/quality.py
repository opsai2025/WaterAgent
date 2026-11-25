"""Quality functions for the monitoring application."""
import numpy as np
import datetime
import math

from .config import (
    PARAM_TURBIDITY,
    PARAM_CHLOROPHYLL,
    ALL_PARAMETERS,
    WQ_DISPLAY_CONDITIONS,
    WQ_DISPLAY_THRESHOLDS,
    WQ_VAZIRMATN_WOFF2_BASE64,
)

def empty_param_dict(factory=dict):
    """A fresh {parameter: empty container} map for every monitored parameter."""
    return {p: factory() for p in ALL_PARAMETERS}


def param_short_name(parameter_type):
    """Short technical label. Internal use only (file names, Excel export)."""
    if parameter_type == PARAM_TURBIDITY:
        return "NDTI"
    if parameter_type == PARAM_CHLOROPHYLL:
        return "Chl-a"
    return "CDOM"


def param_persian_name(parameter_type):
    """
    Persian name shown to the user. Everything the end user reads on a
    parameter page — statistics summary, monthly table, image captions and
    chart labels — uses this instead of the English abbreviations.
    """
    if parameter_type == PARAM_TURBIDITY:
        return "کدورت"
    if parameter_type == PARAM_CHLOROPHYLL:
        return "کلروفیل"
    return "مواد آلی محلول"


def param_decimals(parameter_type):
    """How many decimals to show for this parameter's values."""
    return 4 if parameter_type == PARAM_TURBIDITY else 2


def format_param_value(parameter_type, value, empty="—"):
    """Format one mean value for display, honouring the parameter's precision."""
    try:
        if value is None or np.isnan(value):
            return empty
    except TypeError:
        return empty
    return f"{value:.{param_decimals(parameter_type)}f}"


def _embedded_persian_font_css():
    """A self-contained Persian webfont; no browser/CDN font request needed."""
    return (
        "@font-face {font-family:'WQ Vazirmatn';"
        "src:url(data:font/woff2;base64," + WQ_VAZIRMATN_WOFF2_BASE64 + ") format('woff2');"
        "font-weight:100 900;font-style:normal;font-display:swap;}"
    )


def _quality_display_thresholds(parameter_type):
    """Return display boundaries, or None for CDOM."""
    return WQ_DISPLAY_THRESHOLDS.get(parameter_type)


def _finite_monthly_value(value):
    """Read an existing observation for display; missing is never zero."""
    if value is None or isinstance(value, (bool, np.bool_)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _water_quality_condition(value, parameter_type=PARAM_TURBIDITY):
    """Classify with the first boundary in warning and later ones below."""
    thresholds = _quality_display_thresholds(parameter_type)
    number = _finite_monthly_value(value)
    if number is None or thresholds is None:
        return 'unavailable'
    normal_max, warning_max, dangerous_max = thresholds
    if number < normal_max:
        return 'normal'
    if number <= warning_max:
        return 'warning'
    if number <= dangerous_max:
        return 'dangerous'
    return 'critical'


def _overall_water_quality_condition(results, parameter_type):
    """Unweighted arithmetic mean of finite monthly observations, including 0."""
    if parameter_type not in (PARAM_TURBIDITY, PARAM_CHLOROPHYLL):
        return None, 'unavailable'
    from decimal import Decimal, localcontext

    values = [_finite_monthly_value(r.get('mean_value')) for r in results]
    values = [value for value in values if value is not None]
    if not values:
        return None, 'unavailable'
    # Arithmetic on round-trip decimal representations avoids summation drift
    # at display boundaries (e.g. the mean of -0.4 and -0.2 is -0.3).
    # No observation is rounded, normalized, weighted or written back to data.
    with localcontext() as context:
        context.prec = 40
        overall_mean = float(sum(Decimal(str(value)) for value in values) / len(values))
    return overall_mean, _water_quality_condition(overall_mean, parameter_type)


def _monthly_quality_records(results, parameter_type):
    """JSON-safe, quality-only view of the existing rows in their original order."""
    classified = parameter_type in (PARAM_TURBIDITY, PARAM_CHLOROPHYLL)
    records = []
    segment = 0
    for sequence, result in enumerate(results):
        month = str(result.get('month_name', '—'))
        try:
            month_date = datetime.datetime.strptime(month, '%Y-%m').strftime('%Y-%m-01')
        except ValueError:
            month_date = None
        value = _finite_monthly_value(result.get('mean_value'))
        if value is None:
            segment += 1
        condition = _water_quality_condition(value, parameter_type) if classified else None
        records.append({
            'month': month, 'date': month_date, 'sequence': sequence, 'segment': segment,
            'value': value, 'condition': condition,
            'condition_label': WQ_DISPLAY_CONDITIONS[condition]['label'] if classified else '',
            'display_value': format_param_value(parameter_type, value, empty='بدون داده'),
        })
    return records

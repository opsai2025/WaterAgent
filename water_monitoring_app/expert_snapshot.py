"""JSON-safe snapshots of expert state for one authorised analysis.

This module does not choose a storage location or grant access. The archive
must authorise the analysis and commit its snapshot atomically. In particular,
never load a snapshot from an unowned path supplied by a browser.
"""
import json
import math


_VERSION = 1
_NONFINITE = '__water_monitor_nonfinite__'
_SIGNATURE_KEYS = ('expert_analysis_signature', 'executive_summary_signature')
_TEXT_KEYS = ('expert_analysis_json', 'executive_summary')


def _encode_signature(value):
    if isinstance(value, tuple):
        return [_encode_signature(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {_NONFINITE: 'nan'}
        if math.isinf(value):
            return {_NONFINITE: 'inf' if value > 0 else '-inf'}
        return value
    raise ValueError('Invalid expert signature')


def _decode_signature(value):
    if isinstance(value, list):
        return tuple(_decode_signature(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, dict) and set(value) == {_NONFINITE}:
        special = value[_NONFINITE]
        if special in ('nan', 'inf', '-inf'):
            return float(special)
    raise ValueError('Invalid saved expert signature')


def dump_expert_state(state):
    """Return a JSON-compatible, independent copy of current expert state."""
    data = {'version': _VERSION}
    for key in _TEXT_KEYS:
        value = state.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f'Invalid expert field: {key}')
        data[key] = value
    for key in _SIGNATURE_KEYS:
        signature = state.get(key)
        if signature is not None and not isinstance(signature, tuple):
            raise ValueError(f'Invalid expert field: {key}')
        data[key] = _encode_signature(signature)

    briefs = state.get('param_briefs', {})
    signatures = state.get('param_brief_signatures', {})
    if (not isinstance(briefs, dict) or not isinstance(signatures, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in briefs.items())
            or any(not isinstance(k, str) or not isinstance(v, tuple) for k, v in signatures.items())):
        raise ValueError('Invalid expert parameter briefs')
    data['param_briefs'] = briefs
    data['param_brief_signatures'] = {k: _encode_signature(v) for k, v in signatures.items()}

    history = state.get('expert_chat_history', [])
    if not isinstance(history, list):
        raise ValueError('Invalid expert chat history')
    for item in history:
        if (not isinstance(item, dict) or item.get('role') not in ('user', 'assistant')
                or not isinstance(item.get('content'), str)
                or (item.get('display') is not None and not isinstance(item['display'], str))
                or ('error' in item and not isinstance(item['error'], bool))):
            raise ValueError('Invalid expert chat message')
    data['expert_chat_history'] = history
    # Copy mutable fields and fail on values JSON cannot represent safely.
    try:
        return json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Expert state is not JSON-compatible') from exc


def load_expert_state(payload):
    """Validate a saved snapshot and return state with tuple signatures restored.

    Nothing is applied to a live Streamlit session until validation succeeds.
    """
    if not isinstance(payload, dict) or payload.get('version') != _VERSION:
        raise ValueError('Unsupported expert snapshot')
    required = set(_TEXT_KEYS + _SIGNATURE_KEYS) | {
        'expert_chat_history', 'param_briefs', 'param_brief_signatures'
    }
    if not required.issubset(payload):
        raise ValueError('Incomplete expert snapshot')
    restored = {key: payload[key] for key in _TEXT_KEYS}
    for key in _SIGNATURE_KEYS:
        restored[key] = _decode_signature(payload[key])
        if restored[key] is not None and not isinstance(restored[key], tuple):
            raise ValueError('Invalid saved expert signature')
    signatures = payload['param_brief_signatures']
    if not isinstance(signatures, dict):
        raise ValueError('Invalid saved parameter signatures')
    restored['param_brief_signatures'] = {k: _decode_signature(v) for k, v in signatures.items()}
    restored['param_briefs'] = payload['param_briefs']
    restored['expert_chat_history'] = payload['expert_chat_history']
    # Validate and copy all content with the same rules as a new snapshot.
    return _load_validated(dump_expert_state(restored))


def _load_validated(data):
    restored = {key: data[key] for key in _TEXT_KEYS}
    for key in _SIGNATURE_KEYS:
        restored[key] = _decode_signature(data[key])
    restored['param_briefs'] = data['param_briefs']
    restored['param_brief_signatures'] = {
        key: _decode_signature(value) for key, value in data['param_brief_signatures'].items()
    }
    restored['expert_chat_history'] = data['expert_chat_history']
    return restored

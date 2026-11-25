"""Interface functions for the monitoring application."""
import os
from shapely.geometry import Polygon
import math
import requests
import base64
import inspect
from datetime import date
import streamlit as st
import folium
from folium import plugins
from streamlit_folium import st_folium

from .config import (
    CLOUD_THRESHOLD,
    PARAM_TURBIDITY,
    PARAM_CHLOROPHYLL,
    PARAM_CDOM,
    ALL_PARAMETERS,
    GREGORIAN_MONTHS_FA,
    BNAZANIN_FONT_CANDIDATES,
    BNAZANIN_FONT_DRIVE_ID,
    RUN_STATE_FILE,
    STATUS_FAILED,
    NAV_PAGES,
    ROI_COLOR_SAVED,
    ROI_COLOR_SELECTED,
    ROI_COLOR_DRAFT,
    _LIVE_AREA_JS,
)

try:
    from branca.element import MacroElement as _BrancaMacroElement
    from jinja2 import Template as _JinjaTemplate
    _LIVE_AREA_SUPPORTED = True
except Exception:
    _LIVE_AREA_SUPPORTED = False


if _LIVE_AREA_SUPPORTED:
    class LiveAreaLabel(_BrancaMacroElement):
        """Folium element that injects the live-area script into the map."""
        _template = _JinjaTemplate(_LIVE_AREA_JS)

        def __init__(self):
            super().__init__()
            self._name = "LiveAreaLabel"
else:
    LiveAreaLabel = None


@st.cache_data(show_spinner=False)
def _load_bnazanin_font():
    """
    Look for a local B Nazanin font file (see BNAZANIN_FONT_CANDIDATES) and, if
    found, return (base64_data, css_format, absolute_path) so it can be embedded
    straight into the page as an @font-face rule. Falls back to the optional
    Google Drive copy, and finally to (None, None, None) — in which case the app
    keeps using the existing "locally installed B Nazanin, else Vazirmatn"
    behaviour and nothing breaks.
    """
    ext_to_format = {
        ".woff2": "woff2",
        ".woff": "woff",
        ".ttf": "truetype",
        ".otf": "opentype",
    }

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    for rel_path in BNAZANIN_FONT_CANDIDATES:
        for candidate in (os.path.join(base_dir, rel_path), rel_path):
            if os.path.isfile(candidate):
                ext = os.path.splitext(candidate)[1].lower()
                fmt = ext_to_format.get(ext)
                if not fmt:
                    continue
                try:
                    with open(candidate, "rb") as f:
                        return base64.b64encode(f.read()).decode(), fmt, os.path.abspath(candidate)
                except Exception:
                    continue

    if BNAZANIN_FONT_DRIVE_ID:
        try:
            url = f"https://drive.google.com/uc?export=download&id={BNAZANIN_FONT_DRIVE_ID}"
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            return base64.b64encode(response.content).decode(), "truetype", None
        except Exception:
            pass

    return None, None, None


def _bnazanin_font_face_css():
    """Return the @font-face block for B Nazanin (empty string if unavailable)."""
    font_b64, fmt, font_path = _load_bnazanin_font()
    if not font_b64:
        return ""

    # Make the same font available to matplotlib as well, so the legends and the
    # time-series charts use B Nazanin too instead of the generic fallback.
    if font_path and os.path.splitext(font_path)[1].lower() in (".ttf", ".otf"):
        try:
            import matplotlib.font_manager as fm
            fm.fontManager.addfont(font_path)
        except Exception:
            pass

    return f"""
        @font-face {{
            font-family: 'B Nazanin';
            src: url(data:font/{fmt};charset=utf-8;base64,{font_b64}) format('{fmt}');
            font-weight: normal;
            font-style: normal;
            font-display: swap;
        }}
        @font-face {{
            font-family: 'B Nazanin';
            src: url(data:font/{fmt};charset=utf-8;base64,{font_b64}) format('{fmt}');
            font-weight: bold;
            font-style: normal;
            font-display: swap;
        }}
    """


def _inject_global_app_css():
    """One presentation-only design system; map geometry and widget events stay native."""
    from .expert import _force_quick_button_style, _inject_persian_chat_css
    from .quality import _embedded_persian_font_css
    font_face = _embedded_persian_font_css() + _bnazanin_font_face_css()
    if font_face:
        st.markdown(f"<style>{font_face}</style>", unsafe_allow_html=True)
    st.markdown(
        """
        <style>
        :root {
            --wq-font: 'WQ Vazirmatn', Tahoma, Arial, sans-serif;
            --wq-navy: #142C40;
            --wq-teal-dark: #086873;
            --wq-teal: #087F8C;
            --wq-teal-light: #57B9BF;
            --wq-amber: #B36B15;
            --wq-amber-dark: #8D5110;
            --wq-bg-1: #F3F6F9;
            --wq-bg-2: #F8FAFC;
            --wq-card: #FFFFFF;
            --wq-border: #DCE5EC;
            --wq-muted: #536879;
            --wq-shadow: 0 4px 18px rgba(20,44,64,.045);
        }
        html, body, .stApp { font-family: var(--wq-font); color: var(--wq-navy); }
        .stApp { background: var(--wq-bg-1); }
        .stMarkdown, .stText, .stCaption, label, h1, h2, h3, h4, h5, h6,
        .stButton button, .stDownloadButton button, .stTextInput input,
        .stTextArea textarea, .stNumberInput input, .stDateInput input,
        .stSelectbox [data-baseweb="select"], .stMultiSelect [data-baseweb="select"],
        [data-testid="stMetric"], [data-testid="stAlert"], [data-testid="stCaptionContainer"] {
            font-family: var(--wq-font);
        }
        .stMarkdown, .stText, .stCaption, [data-testid="stCaptionContainer"],
        [data-testid="stWidgetLabel"], [data-testid="stAlert"],
        h1, h2, h3, h4, h5, h6 {
            direction: rtl;
            text-align: right;
        }
        [data-testid="stMarkdownContainer"],
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stMarkdownContainer"] li,
        [data-testid="stMarkdownContainer"] ul,
        [data-testid="stMarkdownContainer"] ol {
            direction: rtl; text-align: right; font-family: var(--wq-font);
        }
        [data-testid="stMarkdownContainer"] ul,
        [data-testid="stMarkdownContainer"] ol {
            padding-inline-start: 1.3rem; padding-inline-end: 0;
        }
        /* Scope RTL to text and controls: columns already have an explicit
           Persian reading order in Python; Leaflet remains left-to-right. */
        .stTextInput input, .stTextArea textarea, [data-testid="stChatInput"] textarea,
        .stSelectbox [data-baseweb="select"], .stMultiSelect [data-baseweb="select"],
        [role="listbox"], [role="option"] { direction: rtl; text-align: right; }
        .stNumberInput input, .stDateInput input {
            direction: ltr; text-align: right; font-variant-numeric: tabular-nums;
        }
        .stMarkdown p, .stMarkdown li { font-size: .98rem; line-height: 1.95; }
        [data-testid="stCaptionContainer"], .stCaption { color: var(--wq-muted); font-size: .85rem; line-height: 1.85; }
        .block-container {
            max-width: 1440px;
            padding-top: 2.5rem;
            padding-bottom: 3.5rem;
            padding-left: 2.25rem;
            padding-right: 2.25rem;
        }
        [data-testid="stHorizontalBlock"] { gap: 1rem; }
        hr { border-color: var(--wq-border); margin: 1.6rem 0 !important; opacity: .8; }
        h1 { color: var(--wq-navy); font-size: 1.95rem; font-weight: 800; line-height: 1.7; }
        h2 { color: var(--wq-navy); font-size: 1.5rem; font-weight: 800; line-height: 1.7; }
        h3 { color: var(--wq-navy); font-size: 1.2rem; font-weight: 700; line-height: 1.8; }
        h4 { color: var(--wq-navy); font-size: 1.05rem; font-weight: 700; margin: 1.15rem 0 .7rem; }
        .wq-app-header {
            direction: rtl; display: flex; align-items: center; gap: 1rem;
            padding: 1.35rem 1.6rem .55rem;
            border-radius: 18px 18px 0 0;
            background: linear-gradient(120deg, #142C40, #173F50);
            color: #FFFFFF;
        }
        .wq-app-header img { width: 3.2rem; height: 3.2rem; object-fit: contain; border-radius: 10px; }
        .wq-app-emblem {
            width: 3.1rem; height: 3.1rem; display: inline-flex; align-items: center;
            justify-content: center; background: rgba(255,255,255,.1); border-radius: 12px; font-size: 1.8rem;
        }
        .wq-app-eyebrow { display: block; font-size: .72rem; font-weight: 500; color: #B9D7DF; margin-bottom: .3rem; }
        .wq-app-title { font-size: clamp(1.35rem, 2.2vw, 1.9rem); font-weight: 800; line-height: 1.7; color: #FFFFFF; }
        .wq-app-subtitle {
            direction: rtl; text-align: right; background: linear-gradient(120deg, #142C40, #173F50);
            color: #DAE8ED; padding: .15rem 1.6rem 1.25rem; margin-bottom: 1.15rem;
            border-radius: 0 0 18px 18px; font-size: .87rem; line-height: 1.9;
            box-shadow: 0 6px 20px rgba(20,44,64,.09);
        }
        .wq-app-subtitle b { color: #FFFFFF; font-weight: 600; }
        .stButton button, .stDownloadButton button {
            direction: rtl; min-height: 2.9rem; padding: .52rem 1rem; border-radius: 10px;
            background: #FFFFFF; border: 1px solid #CCD9E2; color: var(--wq-navy);
            font-weight: 600; box-shadow: 0 1px 3px rgba(20,44,64,.025);
            transition: background .16s ease, border-color .16s ease, box-shadow .16s ease;
        }
        .stButton button p, .stDownloadButton button p { font-size: .91rem; font-weight: 600; line-height: 1.7; }
        .stButton button:hover:not(:disabled), .stDownloadButton button:hover:not(:disabled) {
            background: #F0F7F8; border-color: var(--wq-teal); color: var(--wq-teal-dark);
            box-shadow: 0 3px 9px rgba(8,127,140,.08);
        }
        .stButton button[kind="primary"], .stButton button[kind="primaryFormSubmit"],
        [data-testid="baseButton-primary"], .stDownloadButton button {
            background: var(--wq-teal); border-color: var(--wq-teal); color: #FFFFFF;
        }
        .stButton button[kind="primary"]:hover:not(:disabled),
        .stDownloadButton button:hover:not(:disabled) {
            background: var(--wq-teal-dark); border-color: var(--wq-teal-dark); color: #FFFFFF;
        }
        .stButton button:disabled, .stDownloadButton button:disabled {
            background: #EDF1F5; border-color: #DFE6EC; color: #687C8C; box-shadow: none; opacity: 1;
        }
        button:focus-visible, input:focus-visible, textarea:focus-visible,
        summary:focus-visible, a:focus-visible {
            outline: 3px solid #4C9DB3 !important; outline-offset: 3px;
        }
        .wq-nav-anchor, .wq-quick-anchor { display: none; }
        [class*="st-key-wqnav_"] button {
            min-height: 3.1rem; background: #FFFFFF; border: 1px solid var(--wq-border);
            color: #344F63; border-radius: 11px; box-shadow: none;
        }
        [class*="st-key-wqnav_"] button[kind="primary"] {
            background: var(--wq-navy); border-color: var(--wq-navy); color: #FFFFFF;
            box-shadow: inset 0 -3px 0 #57B9BF;
        }
        [class*="st-key-wqnav_"] button[kind="primary"]:hover:not(:disabled) {
            background: #214558; border-color: #214558; color: #FFFFFF;
        }
        [class*="st-key-wqnav_"] button:disabled {
            background: #EDF1F5; border: 1px dashed #CBD6DF; color: #687C8C;
        }
        .wq-nav-hint { direction: rtl; text-align: right; color: var(--wq-muted); font-size: .8rem; margin: .2rem 0 .9rem; }
        [data-testid="stWidgetLabel"] p { font-size: .91rem; font-weight: 600; color: var(--wq-navy); }
        .stTextInput input, .stTextArea textarea, .stNumberInput input,
        .stDateInput input, .stSelectbox [data-baseweb="select"] > div,
        .stMultiSelect [data-baseweb="select"] > div {
            font-size: .95rem; border-radius: 9px; background: #FFFFFF; color: var(--wq-navy);
        }
        .stSelectbox [data-baseweb="select"] > div,
        .stMultiSelect [data-baseweb="select"] > div { border-color: #CCD9E2; min-height: 2.8rem; }
        .stTextInput input:focus, .stTextArea textarea:focus, .stNumberInput input:focus {
            border-color: var(--wq-teal); box-shadow: 0 0 0 2px rgba(8,127,140,.12);
        }
        .stTabs [data-baseweb="tab-list"] { direction: rtl; gap: .5rem; border-bottom: 1px solid var(--wq-border); }
        .stTabs [data-baseweb="tab"] { padding: .7rem 1.1rem; font-weight: 600; color: var(--wq-muted); border-radius: 8px 8px 0 0; }
        .stTabs [aria-selected="true"] { color: var(--wq-teal-dark); background: #E8F4F5; }
        .stTabs [data-baseweb="tab-panel"] { padding-top: 1.2rem; }
        .wq-step-header {
            direction: rtl; display: flex; align-items: center; gap: .7rem;
            margin: 1.65rem 0 1rem; padding: .9rem 1.1rem; background: #FFFFFF;
            border: 1px solid var(--wq-border); border-right: 3px solid var(--wq-teal);
            border-radius: 12px; box-shadow: var(--wq-shadow);
        }
        .wq-step-number {
            display: inline-flex; align-items: center; justify-content: center;
            min-width: 1.9rem; height: 1.9rem; border-radius: 8px;
            background: #E8F4F5; color: var(--wq-teal-dark); font-weight: 700; font-size: .9rem;
        }
        .wq-step-icon { font-size: 1.2rem; }
        .wq-step-title { color: var(--wq-navy); font-size: 1.12rem; font-weight: 700; }
        .wq-page-badge {
            direction: rtl; display: flex; align-items: center; gap: .65rem;
            padding: 1rem 1.15rem; margin: .8rem 0 1.2rem; background: #FFFFFF;
            border: 1px solid var(--wq-border); border-right: 4px solid var(--wq-badge-start, var(--wq-teal));
            border-radius: 12px; box-shadow: var(--wq-shadow);
        }
        .wq-page-badge-icon { font-size: 1.35rem; }
        .wq-page-badge-text { color: var(--wq-navy); font-size: 1.2rem; font-weight: 700; }
        [data-testid="stMetric"] {
            direction: rtl; text-align: right; background: #FFFFFF; border: 1px solid var(--wq-border);
            border-top: 3px solid #89B7C1; border-radius: 12px; padding: 1rem; box-shadow: var(--wq-shadow);
        }
        [data-testid="stMetricLabel"] p { font-size: .84rem; font-weight: 600; color: var(--wq-muted); }
        [data-testid="stMetricValue"] { direction: ltr; text-align: right; font-size: 1.8rem; font-weight: 700; font-variant-numeric: tabular-nums; }
        .wq-stat-grid { direction: rtl; display: grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: .8rem; margin: .8rem 0 1rem; }
        .wq-stat-card {
            direction: rtl; text-align: right; position: relative; min-width: 0; min-height: 150px;
            padding: 1rem 1.05rem; border-radius: 13px; background: var(--wq-state-bg, #FFFFFF);
            border: 1px solid var(--wq-state-border, var(--wq-border));
            border-top: 3px solid var(--wq-state-accent, #687C8C); box-shadow: var(--wq-shadow);
            transition: border-color .2s ease, background-color .2s ease;
        }
        .wq-stat-label { font-size: .85rem; color: #344C5F; line-height: 1.8; font-weight: 600; }
        .wq-stat-symbol { display: inline-block; margin-left: .25rem; color: var(--wq-state-accent, #536879); font-size: 1rem; }
        .wq-stat-value { display: block; font-size: clamp(1.35rem, 1.9vw, 1.85rem); line-height: 1.8; font-weight: 800; color: var(--wq-state-accent, var(--wq-navy)); margin-top: .25rem; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
        .wq-stat-value bdi { direction: ltr; unicode-bidi: isolate; }
        .wq-stat-detail { color: #536879; font-size: .78rem; line-height: 1.9; margin-top: .3rem; }
        .wq-condition-legend { direction: rtl; display: flex; flex-wrap: wrap; gap: .55rem 1.2rem; align-items: center; margin: .65rem 0 1.8rem; }
        .wq-condition-item { display: inline-flex; align-items: center; gap: .4rem; font-size: .8rem; color: #344C5F; line-height: 1.9; }
        .wq-condition-item bdi { direction: ltr; unicode-bidi: isolate; }
        .wq-condition-dot { display: inline-flex; align-items: center; justify-content: center; width: 1rem; font-size: .85rem; flex-shrink: 0; }
        .wq-chart-note { direction: rtl; text-align: right; color: var(--wq-muted); font-size: .79rem; line-height: 1.9; margin: .4rem 0 .7rem; }
        .wq-chart-heading {
            font-family: var(--wq-font) !important; font-size: 1.22rem !important;
            font-weight: 700 !important; line-height: 1.9; color: var(--wq-navy);
            direction: rtl; text-align: right; margin: 1.2rem 0 2rem !important;
        }
        [data-testid="stVegaLiteChart"] svg, .vega-embed svg { direction: ltr; }
        .vega-embed svg text { font-family: var(--wq-font) !important; }
        [data-testid="stVegaLiteChart"], [data-testid="stAltairChart"] {
            background: #FFFFFF; padding: .6rem; border: 1px solid var(--wq-border); border-radius: 14px; box-shadow: var(--wq-shadow);
        }
        #vg-tooltip-element { direction: rtl; text-align: right; font-family: var(--wq-font); border: 1px solid var(--wq-border); border-radius: 9px; box-shadow: 0 4px 18px rgba(20,44,64,.12); }
        #vg-tooltip-element td.value { direction: ltr; unicode-bidi: isolate; text-align: left; }
        .wq-table-wrapper { direction: rtl; width: 100%; overflow-x: auto; border: 1px solid var(--wq-border); border-radius: 11px; background: #FFFFFF; margin: .5rem 0; }
        .wq-styled-table { width: 100%; border-collapse: collapse; font-family: var(--wq-font); font-size: .85rem; }
        .wq-styled-table thead th { background: #EDF3F7; color: var(--wq-navy); font-weight: 700; padding: .8rem 1rem; text-align: right; border: 0; white-space: nowrap; }
        .wq-styled-table tbody td { border: 0; border-bottom: 1px solid #E8EDF2; padding: .72rem 1rem; text-align: right; color: #344C5F; unicode-bidi: plaintext; }
        .wq-styled-table tbody td:nth-child(2) { direction: ltr; font-variant-numeric: tabular-nums; }
        .wq-styled-table tbody tr:nth-child(even) { background: #F8FAFC; }
        .wq-styled-table tbody tr:hover { background: #EFF7F8; }
        .wq-styled-table tbody tr:last-child td { border-bottom: 0; }
        [data-testid="stDataFrame"] { border: 1px solid var(--wq-border); border-radius: 11px; overflow: hidden; }
        [data-testid="stExpander"] details { background: #FFFFFF; border: 1px solid var(--wq-border); border-radius: 11px; box-shadow: none; }
        [data-testid="stExpander"] summary { direction: rtl; color: var(--wq-navy); padding: .8rem 1rem; }
        [data-testid="stExpander"] summary p { font-family: var(--wq-font); font-size: .92rem; font-weight: 600; }
        [data-testid="stAlert"], .stAlert { border-radius: 10px; border: 1px solid rgba(106,133,153,.16); box-shadow: none; }
        [data-testid="stAlert"] p, .stAlert p { font-size: .91rem; line-height: 1.9; }
        .stProgress [role="progressbar"] > div { background: var(--wq-teal); }
        .wq-status-line { direction: rtl; display: flex; align-items: center; min-height: 2.8rem; }
        .wq-status-chip { direction: rtl; display: inline-flex; align-items: center; gap: .4rem; border: 1px solid; border-radius: 999px; padding: .4rem .85rem; font-size: .82rem; font-weight: 600; }
        .wq-status-ready { background: #EDF8F1; color: #17603E; border-color: #C9E7D5; }
        .wq-status-idle { background: #EDF2F6; color: #4B6477; border-color: #D6E1E9; }
        .wq-status-running { background: #FFF5E6; color: #89500D; border-color: #F0DCB9; }
        .wq-status-strip-spacer { height: .5rem; }
        .wq-ready-banner { direction: rtl; text-align: right; background: #EDF8F7; border: 1px solid #C6E4DF; border-right: 4px solid #218575; border-radius: 12px; color: #205B52; padding: 1rem 1.2rem; font-size: .97rem; line-height: 2; margin: .8rem 0; }
        .wq-param-brief { direction: rtl; text-align: right; background: #FFFFFF; border: 1px solid var(--wq-brief-border, var(--wq-border)); border-right: 4px solid var(--wq-brief-accent, var(--wq-teal)); border-radius: 13px; padding: 1.15rem 1.3rem; margin: .4rem 0 1.3rem; box-shadow: var(--wq-shadow); }
        .wq-param-brief-head { display: flex; align-items: center; gap: .5rem; padding-bottom: .7rem; margin-bottom: .65rem; border-bottom: 1px solid #E8EDF2; }
        .wq-param-brief-icon { font-size: 1.15rem; }
        .wq-param-brief-title { font-size: .96rem; font-weight: 700; color: var(--wq-brief-accent, var(--wq-teal-dark)); }
        .wq-param-brief-body { font-size: .99rem; font-weight: 400; line-height: 2.1; color: #344C5F; }
        .wq-brief-turbidity { --wq-brief-accent: #086873; --wq-brief-border: #D1E3E6; }
        .wq-brief-chlorophyll { --wq-brief-accent: #28724C; --wq-brief-border: #D5E5DA; }
        .wq-brief-cdom { --wq-brief-accent: #8F602A; --wq-brief-border: #E8DECF; }
        .wq-summary-card { direction: rtl; text-align: right; background: #FFFFFF; border: 1px solid var(--wq-border); border-right: 4px solid var(--wq-teal); border-radius: 13px; padding: 1rem 1.3rem; box-shadow: var(--wq-shadow); }
        .wq-summary-line { font-size: 1rem; line-height: 2; color: #344C5F; padding: .7rem 0; border-bottom: 1px solid #E8EDF2; }
        .wq-summary-line:last-child { border-bottom: 0; }
        .wq-quick-hint { direction: rtl; text-align: right; font-size: .91rem; color: var(--wq-muted); margin: 1rem 0 .7rem; line-height: 1.9; }
        [data-testid="stImageCaption"] { direction: rtl; text-align: center; font-family: var(--wq-font); font-size: .82rem; color: var(--wq-muted); line-height: 1.9; }
        /* Only the ROI map receives a frame; charts, chat components and
           other embedded tools retain their own dimensions and event surfaces. */
        [class*="st-key-roi_map_"] iframe,
        [class*="st-key-roi_map_locked_"] iframe,
        iframe[title="streamlit_folium.st_folium"],
        iframe[src*="streamlit_folium.st_folium"] {
            display: block; margin: .2rem auto .6rem; border: 1px solid #CBD9E2 !important;
            border-radius: 15px; box-shadow: 0 7px 24px rgba(20,44,64,.10);
        }
        section[data-testid="stSidebar"] { background: #142C40; direction: rtl; text-align: right; }
        [data-testid="stAppViewContainer"] { flex-direction: row-reverse; }
        section[data-testid="stSidebar"][data-collapsed="true"] { transform: translateX(100vw); }
        [data-testid="stSidebarResizeHandle"] { display: none; }
        section[data-testid="stSidebar"] .stMarkdown,
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #E6F0F4; }
        section[data-testid="stSidebar"] button p { color: inherit; }
        section[data-testid="stSidebar"] [data-testid="stExpander"] details,
        section[data-testid="stSidebar"] [data-testid="stExpander"] summary,
        section[data-testid="stSidebar"] [data-testid="stExpander"] .stMarkdown,
        section[data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stCaptionContainer"],
        section[data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stWidgetLabel"] p { color: #142C40; }
        section[data-testid="stSidebar"] button[aria-label="Collapse sidebar"] { color: #FFFFFF; }
        section[data-testid="stSidebar"] button[aria-label="Collapse sidebar"] [data-testid="stIconMaterial"] { color: #FFFFFF !important; transform: scaleX(-1); }
        section[data-testid="stSidebar"] button:focus-visible { outline: 3px solid #F8C967; outline-offset: 2px; }
        @media (max-width: 1100px) {
            .block-container { padding-left: 1.3rem; padding-right: 1.3rem; }
            .wq-stat-grid { grid-template-columns: repeat(3, minmax(0,1fr)); }
            .wq-stat-value { font-size: 1.65rem; }
            [class*="st-key-wqnav_"] button { padding: .45rem .45rem; }
            [class*="st-key-wqnav_"] button p { font-size: .8rem; }
        }
        @media (max-width: 700px) {
            .block-container { padding-top: 1.3rem; padding-left: .85rem; padding-right: .85rem; }
            .wq-app-header { padding: 1.1rem 1rem .5rem; gap: .7rem; }
            .wq-app-subtitle { padding: .1rem 1rem 1rem; font-size: .78rem; }
            .wq-app-title { font-size: 1.35rem; }
            .wq-app-eyebrow { font-size: .66rem; }
            .wq-stat-grid { grid-template-columns: repeat(2, minmax(0,1fr)); gap: .65rem; }
            .wq-stat-card { min-height: 135px; padding: .85rem; }
            .wq-stat-label { font-size: .76rem; }
            .wq-step-title { font-size: .98rem; }
            .wq-param-brief { padding: 1rem; }
            .wq-condition-legend { gap: .4rem .8rem; }
        }
        @media (max-width: 380px) { .wq-stat-grid { grid-template-columns: 1fr; } }
        @media (prefers-reduced-motion: reduce) {
            .stButton button, .stDownloadButton button, .wq-stat-card { transition: none; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    _inject_persian_chat_css()
    _force_quick_button_style()


def _render_step_header(step_number, icon, title):
    """Colored, icon-led replacement for the old plain st.header() numbered
    workflow titles (1️⃣/2️⃣/3️⃣ …). Purely presentational."""
    st.markdown(
        f"""
        <div class="wq-step-header">
            <div class="wq-step-number">{step_number}</div>
            <div class="wq-step-icon">{icon}</div>
            <div class="wq-step-title">{title}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_active_section_badge(icon, title, start_hex, end_hex):
    """Colored 'you are here' badge shown at the top of each of the three
    result tabs (Turbidity / Chlorophyll / Expert chat) so the currently
    active page is always visually obvious. Purely presentational."""
    st.markdown(
        f"""
        <div class="wq-page-badge" style="--wq-badge-start:{start_hex}; --wq-badge-end:{end_hex};">
            <span class="wq-page-badge-icon">{icon}</span>
            <span class="wq-page-badge-text">{title}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_persian_dataframe_html(df):
    """Render a DataFrame as a custom-styled, RTL Persian HTML table. This is
    a pure presentation swap for st.dataframe: identical columns and values
    are shown, only the typography/spacing/colors differ (st.dataframe's
    canvas-based grid cannot be restyled with CSS, which is why a plain HTML
    table is used here instead)."""
    import html as _html

    # Isolate dates/numbers from the surrounding Persian reading direction.
    # Escape cell contents before adding markup; the original frame is untouched.
    def _display_cell(value):
        text = str(value)
        escaped = _html.escape(text)
        if text and all(char in '0123456789.,+−-% /:—' for char in text):
            return f'<bdi dir="ltr">{escaped}</bdi>'
        return escaped

    formatters = {column: _display_cell for column in df.columns}
    html_table = df.to_html(index=False, escape=False, border=0, classes="wq-styled-table",
                            formatters=formatters)
    st.markdown(f'<div class="wq-table-wrapper">{html_table}</div>', unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def _get_app_logo_base64():
    """Load the app icon from Google Drive and return it as a base64 string
    for inline embedding next to the page title.

    The local assets/app_logo.png file is not present on this deployment
    (Posit Cloud), so the logo is fetched from Google Drive instead using
    the file's shareable ID. The Drive file must be shared as "Anyone with
    the link -> Viewer" for this request to succeed.

    Cached so the file is only downloaded once per session. On any failure
    (network issue, permissions, bad ID) this returns an empty string
    instead of raising, so a missing/unreachable logo never crashes the app
    — the <img> tag will just render nothing instead.
    """
    GOOGLE_DRIVE_LOGO_FILE_ID = "1sX8iMkqYvYDJ-XocOa8Hmr0e6-4R-n6i"
    url = f"https://drive.google.com/thumbnail?id={GOOGLE_DRIVE_LOGO_FILE_ID}&sz=w1000"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return base64.b64encode(response.content).decode()
    except Exception:
        return ""


def _has_any_results():
    """True once at least one parameter has monitoring results in memory."""
    return any(bool(st.session_state.results.get(p)) for p in ALL_PARAMETERS)


def _render_app_header():
    """Persian product header with the existing cached logo and an offline fallback."""
    logo_b64 = _get_app_logo_base64()
    logo_tag = (
        f'<img src="data:image/png;base64,{logo_b64}" alt="نشان سامانه پایش کیفیت آب">'
        if logo_b64 else '<span class="wq-app-emblem" aria-hidden="true">🌊</span>'
    )
    st.markdown(
        f"""
        <div class="wq-app-header" dir="rtl">
            {logo_tag}
            <div>
                <span class="wq-app-eyebrow">پایش ماهواره‌ای و تحلیل محیط‌زیست</span>
                <span class="wq-app-title">سامانه پایش کیفیت آب</span>
            </div>
        </div>
        <div class="wq-app-subtitle" dir="rtl">
            پایش <b>کدورت</b>، <b>کلروفیل</b> و <b>مواد آلی محلول رنگی</b>
            با تصاویر ماهواره‌ای <bdi dir="ltr">Sentinel-2</bdi>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar_nav():
    """Navigation shared by the monitoring, history and account pages."""
    from .cache import _recover_interrupted_run
    _recover_interrupted_run()
    results_ready = _has_any_results()
    busy = st.session_state.processing_in_progress
    if busy or (st.session_state.active_page != 'setup' and not results_ready):
        st.session_state.active_page = 'setup'
    unsaved = st.session_state.get('history_unsaved', False)
    other_page = st.session_state.get('showing_history', False) or st.session_state.get('auth_manage_users', False)
    st.sidebar.markdown('### 🧭 بخش‌های سامانه')
    for page_key, icon, title in NAV_PAGES:
        is_active = not other_page and st.session_state.active_page == page_key
        locked = page_key != "setup" and (not results_ready or busy)
        label = f"{icon}  {title}" + ("  🔒" if locked else "")
        clicked = st.sidebar.button(
            label,
            key=f"wqnav_{page_key}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
            disabled=locked,
        )
        if clicked and not is_active:
            st.session_state.active_page = page_key
            st.session_state.showing_history = False
            st.session_state.auth_manage_users = False
            st.rerun()
    if unsaved:
        st.sidebar.caption('برای خروج یا بازکردن تحلیل دیگر، ابتدا تغییرات ذخیره‌نشده خلاصه یا گفت‌وگو را تعیین تکلیف کنید.')
    elif busy:
        st.sidebar.caption('در حین پردازش، بخش‌های دیگر پس از پایان یا توقف در دسترس‌اند.')
    elif not results_ready:
        st.sidebar.caption('بخش‌های نتایج پس از دسترسی به داده فعال می‌شوند.')


def _render_status_strip():
    """
    Run status + 'clear results' action. Shown on the first page directly below
    the «شروع پایش» / «ادامه از محل قطع» buttons (it used to live in the
    sidebar, which has been removed in favour of the top navigation).
    """
    from .quality import empty_param_dict
    st.markdown('<div class="wq-status-strip-spacer"></div>', unsafe_allow_html=True)

    col_clear, col_status = st.columns([1, 3])

    with col_status:
        if st.session_state.processing_in_progress:
            chip_class, chip_text = "wq-status-running", "⏳ در حال پردازش..."
        elif _has_any_results():
            chip_class, chip_text = "wq-status-ready", "✅ نتایج پایش موجود است"
        else:
            chip_class, chip_text = "wq-status-idle", "ℹ️ هنوز پایشی انجام نشده است"
        st.markdown(
            f'<div class="wq-status-line">'
            f'<span class="wq-status-chip {chip_class}">{chip_text}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col_clear:
        if st.button(
            "🗑️ پاک کردن نتایج",
            use_container_width=True,
            disabled=(st.session_state.processing_in_progress or st.session_state.get('history_unsaved', False)),
        ):
            st.session_state.downloaded_months = empty_param_dict(dict)
            st.session_state.month_statuses = empty_param_dict(dict)
            st.session_state.results = empty_param_dict(list)
            st.session_state.mean_data = empty_param_dict(dict)
            st.session_state.download_summary = {}
            st.session_state.shared_scene_counts = {}
            st.session_state.cdom_display_range = None
            st.session_state.current_temp_dir = None
            st.session_state.pop('active_history_id', None)
            st.session_state.processing_config = None
            st.session_state.processing_complete = False
            st.session_state.processing_in_progress = False
            st.session_state.pending_run = None
            st.session_state.resume_after_interruption = False
            st.session_state.connection_interrupted = False
            st.session_state.last_run_error = None
            st.session_state.expert_analysis_json = None
            st.session_state.expert_analysis_signature = None
            st.session_state.executive_summary = None
            st.session_state.executive_summary_signature = None
            st.session_state.expert_chat_history = []
            st.session_state.active_page = 'setup'
            st.rerun()


def _st_folium_compat(fmap, **kwargs):
    """
    Call st_folium while staying compatible with older streamlit-folium
    releases that do not support `key` / `returned_objects`. Limiting the
    returned objects (when available) is what stops harmless panning and
    zooming from triggering a full app rerun.
    """
    try:
        supported = inspect.signature(st_folium).parameters
    except Exception:
        supported = {}
    safe_kwargs = {k: v for k, v in kwargs.items() if not supported or k in supported}
    return st_folium(fmap, **safe_kwargs)


def _polygon_to_latlon(polygon):
    """Shapely stores (lon, lat); folium wants (lat, lon)."""
    return [(y, x) for x, y in polygon.exterior.coords]


def polygon_area_km2(polygon):
    """
    Approximate area of a lat/lon polygon in square kilometres.

    One degree of latitude is ~110.57 km everywhere, but one degree of
    longitude shrinks towards the poles, so the polygon's own mid-latitude is
    used to scale it. (The previous `area * 111 * 111` ignored that and
    overestimated by roughly 20% at Iran's latitudes.)
    """
    try:
        min_lat = polygon.bounds[1]
        max_lat = polygon.bounds[3]
        mean_lat = math.radians((min_lat + max_lat) / 2.0)
        km_per_deg_lat = 110.574
        km_per_deg_lon = 111.320 * math.cos(mean_lat)
        return abs(polygon.area) * km_per_deg_lat * km_per_deg_lon
    except Exception:
        return 0.0


def _format_area_km2(area_km2):
    """Readable area text — more decimals for small regions."""
    if area_km2 >= 100:
        return f"{area_km2:,.0f} کیلومتر مربع"
    if area_km2 >= 10:
        return f"{area_km2:.1f} کیلومتر مربع"
    return f"{area_km2:.2f} کیلومتر مربع"


def _add_area_label(layer, polygon, text, color):
    """
    Small floating badge placed just below the polygon showing its area, so the
    user gets an immediate sense of how large the selected region is.

    The badge lives inside the map's own iframe, where the app's stylesheet does
    not reach — hence the inline styling.
    """
    try:
        min_lon, min_lat, max_lon, max_lat = polygon.bounds
        anchor_lat = min_lat
        anchor_lon = (min_lon + max_lon) / 2.0
    except Exception:
        return

    html = (
        f'<div style="'
        f'display:inline-block; white-space:nowrap; direction:rtl;'
        f'background:rgba(255,255,255,0.94); color:#0A3F4A;'
        f'border:2px solid {color}; border-left:none; border-right:none;'
        f'border-top:3px solid {color}; border-bottom:3px solid {color};'
        f'border-radius:999px; padding:3px 10px;'
        f'font-family:\'B Nazanin\',\'BNazanin\',\'Vazirmatn\',Tahoma,sans-serif;'
        f'font-size:13px; font-weight:700; line-height:1.6;'
        f'box-shadow:0 2px 6px rgba(10,63,74,0.35);'
        f'transform:translateX(-50%);'
        f'">{text}</div>'
    )

    folium.Marker(
        location=[anchor_lat, anchor_lon],
        icon=folium.DivIcon(html=html, icon_size=(0, 0), icon_anchor=(0, -6)),
    ).add_to(layer)


def _build_roi_map(interactive=True, highlight_index=None):
    """Build the ROI map with every saved region drawn as a permanent layer."""
    saved = list(st.session_state.drawn_polygons)
    draft = st.session_state.last_drawn_polygon
    draft_is_saved = draft is not None and any(p.equals(draft) for p in saved)

    shapes = saved + ([draft] if (draft is not None and not draft_is_saved) else [])

    if shapes:
        xs_min = min(s.bounds[0] for s in shapes)
        ys_min = min(s.bounds[1] for s in shapes)
        xs_max = max(s.bounds[2] for s in shapes)
        ys_max = max(s.bounds[3] for s in shapes)
        center = [(ys_min + ys_max) / 2, (xs_min + xs_max) / 2]
    else:
        center = [35.6892, 51.3890]

    fmap = folium.Map(location=center, zoom_start=8, control_scale=True)
    # Attribution can wrap on narrow screens; keep the scale above it instead
    # of allowing the two Leaflet controls to overlap inside the map iframe.
    fmap.get_root().header.add_child(folium.Element("""
        <style>
        @media (max-width: 600px) {
            .leaflet-bottom.leaflet-left .leaflet-control-scale { margin-bottom: 58px; }
        }
        </style>
    """))

    folium.TileLayer(
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        attr='Google', name='تصویر ماهواره‌ای'
    ).add_to(fmap)

    if interactive:
        # Only the two drawing tools are shown. The plugin's own edit/delete
        # toolbar and its Export button are switched off: they act on the
        # browser-side layer only, so they never matched what the app actually
        # had stored and were a source of confusion. Deleting a region is done
        # with the 🗑️ button in «مناطق ذخیره‌شده» (saved regions) and with
        # «حذف منطقه رسم‌شده» (a region drawn but not yet saved).
        plugins.Draw(
            export=False,
            position='topleft',
            draw_options={
                'polyline': False, 'rectangle': True, 'polygon': True,
                'circle': False, 'marker': False, 'circlemarker': False,
            },
            edit_options={'edit': False, 'remove': False},
        ).add_to(fmap)

        # Live area readout that follows the shape as it is being drawn
        if LiveAreaLabel is not None:
            try:
                fmap.add_child(LiveAreaLabel())
            except Exception:
                pass

    roi_layer = folium.FeatureGroup(name='مناطق انتخاب‌شده', show=True)

    for i, polygon in enumerate(saved):
        is_selected = (highlight_index is not None and i == highlight_index)
        color = ROI_COLOR_SELECTED if is_selected else ROI_COLOR_SAVED
        area_text = _format_area_km2(polygon_area_km2(polygon))
        label = f"منطقه {i + 1}" + (" — انتخاب‌شده برای پایش" if is_selected else "")
        folium.Polygon(
            locations=_polygon_to_latlon(polygon),
            color=color,
            weight=5 if is_selected else 3,
            opacity=0.95,
            fill=True,
            fill_color=color,
            fill_opacity=0.22 if is_selected else 0.12,
            tooltip=f"{label} — {area_text}",
        ).add_to(roi_layer)
        _add_area_label(roi_layer, polygon, f"منطقه {i + 1} • {area_text}", color)

    if draft is not None and not draft_is_saved:
        draft_area_text = _format_area_km2(polygon_area_km2(draft))
        folium.Polygon(
            locations=_polygon_to_latlon(draft),
            color=ROI_COLOR_DRAFT,
            weight=3,
            dash_array='8,6',
            fill=True,
            fill_color=ROI_COLOR_DRAFT,
            fill_opacity=0.10,
            tooltip=f"منطقه رسم‌شده (ذخیره‌نشده) — {draft_area_text}",
        ).add_to(roi_layer)
        _add_area_label(
            roi_layer, draft, f"منطقه جدید • {draft_area_text}", ROI_COLOR_DRAFT
        )

    roi_layer.add_to(fmap)
    folium.LayerControl(collapsed=True).add_to(fmap)

    if shapes:
        fmap.fit_bounds([[ys_min, xs_min], [ys_max, xs_max]], padding=(25, 25))

    return fmap


def _render_roi_map():
    """
    Render the map. During a monitoring run it stays on screen as a read-only
    view (drawing tools removed, no interaction sent back to the server) instead
    of being replaced by a placeholder message.
    """
    busy = st.session_state.processing_in_progress
    highlight = st.session_state.selected_region_index if st.session_state.drawn_polygons else None

    map_col_l, map_col_c, map_col_r = st.columns([0.35, 6.3, 0.35])

    with map_col_c:
        fmap = _build_roi_map(interactive=not busy, highlight_index=highlight)

        # The version suffix forces a fresh widget after a save or a delete, so
        # the drawing toolbar cannot hand back a region the user just removed.
        version = st.session_state.map_version

        if busy:
            # returned_objects=[] -> the component never sends data back, so the
            # map cannot trigger a rerun in the middle of the processing run.
            _st_folium_compat(
                fmap, key=f"roi_map_locked_{version}", width=1000, height=560,
                use_container_width=True,
                returned_objects=[],
            )
            st.caption("🔒 نقشه در حین پردازش فقط برای نمایش است؛ منطقه انتخاب‌شده با رنگ نارنجی مشخص شده است.")
            return

        map_data = _st_folium_compat(
            fmap, key=f"roi_map_{version}", width=1000, height=560,
            use_container_width=True,
            returned_objects=["last_active_drawing"],
        )

        if map_data and map_data.get('last_active_drawing'):
            geom = (map_data['last_active_drawing'] or {}).get('geometry', {}) or {}
            if geom.get('type') == 'Polygon':
                try:
                    new_polygon = Polygon(geom['coordinates'][0])
                except Exception:
                    new_polygon = None
                if new_polygon is not None and (
                    st.session_state.last_drawn_polygon is None
                    or not st.session_state.last_drawn_polygon.equals(new_polygon)
                ):
                    st.session_state.last_drawn_polygon = new_polygon
                    st.rerun()   # redraw immediately as a permanent layer

        has_unsaved_draft = (
            st.session_state.last_drawn_polygon is not None
            and not any(p.equals(st.session_state.last_drawn_polygon)
                        for p in st.session_state.drawn_polygons)
        )

        if st.session_state.last_drawn_polygon is not None:
            draft_area = _format_area_km2(
                polygon_area_km2(st.session_state.last_drawn_polygon)
            )
            if has_unsaved_draft:
                st.info(
                    f"✅ منطقه رسم شد (خط‌چین قرمز) — مساحت تقریبی: **{draft_area}**. "
                    "برای نگه‌داشتن آن، «ذخیره منطقه» را بزنید."
                )
            else:
                st.success("✅ منطقه انتخاب‌شده روی نقشه نمایش داده می‌شود.")

        # The discard button only appears while there is something to discard,
        # so there is never an inactive delete control on screen.
        if has_unsaved_draft:
            save_col, discard_col = st.columns([3, 1])
        else:
            save_col, discard_col = st.container(), None

        if save_col.button("💾 ذخیره منطقه", use_container_width=True):
            if st.session_state.last_drawn_polygon:
                is_duplicate = any(
                    existing.equals(st.session_state.last_drawn_polygon)
                    for existing in st.session_state.drawn_polygons
                )
                if not is_duplicate:
                    st.session_state.drawn_polygons.append(st.session_state.last_drawn_polygon)
                    st.session_state.selected_region_index = len(st.session_state.drawn_polygons) - 1
                    st.session_state.map_version += 1
                    st.success("✅ منطقه ذخیره شد!")
                    st.rerun()
                else:
                    st.warning("⚠️ این منطقه قبلاً ذخیره شده است")
            else:
                st.warning("⚠️ ابتدا یک منطقه را روی نقشه رسم کنید")

        # Removes a region that was drawn by mistake and never saved.
        if discard_col is not None and discard_col.button(
            "✖️ حذف منطقه رسم‌شده",
            use_container_width=True,
            help="منطقه‌ای که رسم شده اما ذخیره نشده است را از نقشه بردارید",
        ):
            st.session_state.last_drawn_polygon = None
            st.session_state.map_version += 1
            st.rerun()


def _render_interruption_notice():
    """Explain a stopped run and safely render current or older error records."""
    from .cache import _failure_message
    from .earth_engine_errors import is_initialization_message

    failures = [st.session_state.get('last_run_error')]
    failures.extend(entry.get('message', '')
                    for statuses in st.session_state.month_statuses.values()
                    for entry in statuses.values() if entry.get('status') == STATUS_FAILED)
    if any(is_initialization_message(message) for message in failures):
        st.warning(
            '⚠️ اتصال اولیه به Earth Engine برقرار نشد؛ فایل‌های دریافت‌شده حفظ شده‌اند. '
            'ابتدا علت خطای اتصال یا دسترسی را در جزئیات بررسی و رفع کنید؛ '
            'سپس «ادامه از محل قطع» را بزنید.'
        )
    else:
        st.warning(
            "⚠️ پایش کامل نشده است؛ فایل‌های دریافت‌شده حفظ شده‌اند. "
            "برای تلاش دوباره، دکمه «ادامه از محل قطع» را فشار دهید."
        )
    with st.expander('جزئیات آخرین توقف و ماه‌های ناموفق'):
        if st.session_state.get('last_run_error'):
            st.write(_failure_message(st.session_state.last_run_error))
        for parameter, statuses in st.session_state.month_statuses.items():
            for month_name, entry in sorted(statuses.items()):
                if entry.get('status') == STATUS_FAILED:
                    st.write(f"{parameter} / {month_name}: {_failure_message(entry.get('message', ''))}")


def render_setup_page():
    # ==========================================================================
    # 1. Region selection
    # ==========================================================================
    from .cache import _execute_monitoring_run, _prepare_monitoring_run, get_cache_dir_for_config
    from .results import generate_combined_timeseries_excel
    _render_step_header(1, "🗺️", "انتخاب منطقه مورد نظر (بدنه آبی)")

    _render_roi_map()

    if st.session_state.drawn_polygons:
        st.subheader("📍 مناطق ذخیره‌شده")
        for i, p in enumerate(st.session_state.drawn_polygons):
            c1, c2, c3 = st.columns([3, 1, 1])
            centroid = p.centroid
            c1.write(f"**منطقه {i+1}**: ~{_format_area_km2(polygon_area_km2(p))}")
            c2.write(f"مرکز: ({centroid.y:.4f}, {centroid.x:.4f})")
            if c3.button("🗑️", key=f"del_{i}", disabled=st.session_state.processing_in_progress):
                removed = st.session_state.drawn_polygons.pop(i)

                # The deleted region must also leave the map. Two things keep a
                # copy of it: the "last drawn" polygon in session state, and the
                # map widget's own drawing toolbar — clear the first, and bump
                # map_version so the widget is remounted and forgets the second.
                if st.session_state.last_drawn_polygon is not None and \
                        st.session_state.last_drawn_polygon.equals(removed):
                    st.session_state.last_drawn_polygon = None
                st.session_state.map_version += 1

                if st.session_state.selected_region_index >= len(st.session_state.drawn_polygons):
                    st.session_state.selected_region_index = max(0, len(st.session_state.drawn_polygons) - 1)
                st.rerun()

    # ==========================================================================
    # 2. Time period
    # ==========================================================================
    _render_step_header(2, "📅", "بازه زمانی (تقویم میلادی)")

    # The month/year drop-downs below replace the previous calendar widget on
    # purpose: that widget is rendered by the browser and, on a Persian-locale
    # browser, it showed a Jalali (solar) calendar. Picking the Gregorian year
    # and month explicitly removes any dependence on the browser's locale — and
    # the pipeline works month by month anyway, so the day was never used.
    _today = date.today()
    _default_end_year, _default_end_month = _today.year, _today.month
    _default_start_year, _default_start_month = _today.year - 1, _today.month

    year_options = list(range(2017, _today.year + 1))   # Sentinel-2 L2A starts 2017

    def _month_label(m):
        return f"{m:02d} — {GREGORIAN_MONTHS_FA[m - 1]}"

    dc1, dc2, dc3, dc4 = st.columns(4)
    # Right-to-left reading order: «از» on the right, «تا» on the left
    col_start_year, col_start_month, col_end_year, col_end_month = dc4, dc3, dc2, dc1

    start_year = col_start_year.selectbox(
        "از سال (میلادی)", year_options,
        index=year_options.index(_default_start_year) if _default_start_year in year_options else 0,
        disabled=st.session_state.processing_in_progress, key="start_year")
    start_month = col_start_month.selectbox(
        "از ماه", list(range(1, 13)), index=_default_start_month - 1,
        format_func=_month_label,
        disabled=st.session_state.processing_in_progress, key="start_month")
    end_year = col_end_year.selectbox(
        "تا سال (میلادی)", year_options,
        index=year_options.index(_default_end_year) if _default_end_year in year_options else len(year_options) - 1,
        disabled=st.session_state.processing_in_progress, key="end_year")
    end_month = col_end_month.selectbox(
        "تا ماه (غیرشامل)", list(range(1, 13)), index=_default_end_month - 1,
        format_func=_month_label,
        disabled=st.session_state.processing_in_progress, key="end_month")

    start = date(start_year, start_month, 1)
    end = date(end_year, end_month, 1)

    if start >= end:
        st.error("بازه تاریخ نامعتبر است — تاریخ پایان باید بعد از تاریخ شروع باشد.")
        st.stop()

    months = (end.year - start.year) * 12 + (end.month - start.month)
    st.info(f"📅 بازه انتخابی: **{months} ماه**")

    # ==========================================================================
    # 3. Run analysis — fully automatic (preprocessing + both indices)
    # ==========================================================================
    _render_step_header(3, "🚀", "اجرای پایش")

    selected_polygon = None

    if st.session_state.drawn_polygons:
        region_options = []
        for i, p in enumerate(st.session_state.drawn_polygons):
            region_options.append(f"منطقه {i+1} (~{_format_area_km2(polygon_area_km2(p))})")

        if st.session_state.selected_region_index >= len(st.session_state.drawn_polygons):
            st.session_state.selected_region_index = 0

        selected_idx = st.selectbox(
            "🎯 انتخاب منطقه",
            range(len(region_options)),
            format_func=lambda i: region_options[i],
            index=st.session_state.selected_region_index,
            disabled=st.session_state.processing_in_progress
        )

        st.session_state.selected_region_index = selected_idx
        selected_polygon = st.session_state.drawn_polygons[selected_idx]

    elif st.session_state.last_drawn_polygon is not None:
        selected_polygon = st.session_state.last_drawn_polygon
        st.info("ℹ️ استفاده از منطقه رسم‌شده (ذخیره‌نشده)")
    else:
        st.warning("⚠️ ابتدا یک منطقه را روی نقشه رسم کنید")

    st.caption("پس از اجرا، پیش‌پردازش (حذف ابر، حذف برف، استخراج بدنه آب)، سپس شاخص کدورت، شاخص کلروفیل و شاخص مواد آلی محلول به‌طور خودکار محاسبه می‌شوند.")

    if st.session_state.processing_in_progress:
        if st.button('⏸️ توقف و حفظ پیشرفت', key='pause_monitoring'):
            st.session_state.pending_run = None
            st.session_state.processing_in_progress = False
            st.session_state.resume_after_interruption = True
            st.rerun()

    selected_config = {
        'polygon_coords': list(selected_polygon.exterior.coords),
        'start_date': start.strftime('%Y-%m-%d'),
        'end_date': end.strftime('%Y-%m-%d'),
        'cloudy_pixel_percentage': CLOUD_THRESHOLD,
        'scale': 10,
        'pipeline_version': 'shared_v1',
    } if selected_polygon is not None else None

    # Recover only the explicitly selected region/period after session loss.
    # Never adopt the latest folder from another server user.
    resume_config = st.session_state.processing_config
    if resume_config is None and selected_config is not None:
        candidate_dir = get_cache_dir_for_config(selected_config)
        try:
            if any(name.endswith('.tif') or name == RUN_STATE_FILE or name.startswith('bands_')
                   for name in os.listdir(candidate_dir)):
                resume_config = selected_config
        except OSError:
            pass  # A concurrently evicted folder must not hide the controls.

    btn_col1, btn_col2, btn_col3 = st.columns(3)

    start_btn = btn_col1.button(
        "🚀 شروع پایش",
        type="primary",
        disabled=st.session_state.processing_in_progress or selected_polygon is None
    )

    # Even an initialization failure before the first download must be retryable.
    resume_btn = btn_col2.button(
        "🔄 ادامه از محل قطع",
        disabled=(
            resume_config is None or
            st.session_state.processing_in_progress
        ),
        help="اگر اتصال اینترنت قطع شد، پس از اتصال مجدد این دکمه را فشار دهید تا دانلود از همانجا ادامه یابد."
    )
    has_failed = any(entry.get('status') == STATUS_FAILED
                     for statuses in st.session_state.month_statuses.values()
                     for entry in statuses.values())
    retry_btn = btn_col3.button(
        '🔁 تلاش دوباره برای ماه‌های ناموفق',
        disabled=(not has_failed or st.session_state.processing_config is None
                  or st.session_state.processing_in_progress),
        help='فقط ماه‌های ناموفق یا باقی‌مانده بررسی می‌شوند؛ تصاویر کامل دوباره دریافت نمی‌شوند.',
    )
    st.caption('تصاویر و نتایج کامل این محدوده و بازه زمانی از حافظه ذخیره‌شده استفاده می‌شوند؛ '
               'شروع پایش فایل‌های دریافت‌شده را پاک نمی‌کند.')

    # --- A click only ARMS the run; the work happens on the next script run ---
    # Doing it this way means the whole page (map included) is already rendered
    # in its locked, read-only state before the long download loop begins — the
    # map therefore stays visible for the entire run and can no longer trigger a
    # rerun that would abort the processing halfway through.
    if start_btn:
        if st.session_state.get('history_unsaved'):
            st.error('ابتدا تغییرات ذخیره‌نشده گفت‌وگو یا خلاصه را ذخیره کنید.')
            return
        try:
            _prepare_monitoring_run(selected_config)
        except Exception:
            st.error('ایجاد فضای پایدار تحلیل انجام نشد. فضای ذخیره، دسترسی‌ها و استفاده هم‌زمان را بررسی کنید.')
        else:
            st.rerun()

    if (resume_btn or retry_btn) and resume_config is not None:
        if st.session_state.get('history_unsaved'):
            st.error('ابتدا تغییرات ذخیره‌نشده گفت‌وگو یا خلاصه را ذخیره کنید.')
            return
        if not st.session_state.get('active_history_id'):
            try:
                _prepare_monitoring_run(resume_config)
            except Exception:
                st.error('ذخیره تحلیل برای ادامه انجام نشد. فضای ذخیره، دسترسی‌ها و استفاده هم‌زمان را بررسی کنید.')
                return
        st.session_state.processing_config = resume_config
        st.session_state.processing_in_progress = True
        st.session_state.resume_after_interruption = False
        st.session_state.pending_run = 'resume'
        st.rerun()

    # Execute only a deliberate Start/Resume click. An abandoned busy flag must
    # not repeatedly start the same failing request and lock every button again.
    pending = st.session_state.get('pending_run')

    if pending and st.session_state.processing_config is not None:
        config = st.session_state.processing_config

        # Start and Resume both retain validated disk cache.
        is_fresh = (pending == 'start')

        _execute_monitoring_run(config, resume=not is_fresh)
        st.rerun()

    # Hint when a partial run can be resumed
    if st.session_state.resume_after_interruption and not st.session_state.processing_in_progress:
        _render_interruption_notice()

    # --- Run status + clear-results action, directly under the run buttons ---
    _render_status_strip()

    # Simple, user-friendly download summary (persists after run)
    if st.session_state.download_summary:
        st.divider()
        turb_d, turb_a = st.session_state.download_summary.get(PARAM_TURBIDITY, (0, 0))
        chl_d, chl_a = st.session_state.download_summary.get(PARAM_CHLOROPHYLL, (0, 0))
        cdom_d, cdom_a = st.session_state.download_summary.get(PARAM_CDOM, (0, 0))
        if st.session_state.processing_config and st.session_state.processing_config.get('pipeline_version') == 'shared_v1':
            st.info(f'دریافت مشترک: {turb_d} ماه از {turb_a} ماه دارای تصویر تکمیل شد؛ هر سه شاخص از ورودی مشترک استفاده می‌کنند.')
            counts = st.session_state.get('shared_scene_counts', {})
            if counts:
                with st.expander('تعداد صحنه‌های ورودی مشترک در هر ماه'):
                    for month, scene_count in sorted(counts.items()):
                        st.text(f'{month}: {scene_count}')
        else:
            st.info(
                f"🌊 شاخص کدورت: {turb_d} تصویر از {turb_a} تصویر موجود دریافت شد.\n\n"
                f"🌿 شاخص کلروفیل: {chl_d} تصویر از {chl_a} تصویر موجود دریافت شد.\n\n"
                f"🍂 شاخص مواد آلی محلول: {cdom_d} تصویر از {cdom_a} تصویر موجود دریافت شد."
            )

    # ==========================================================================
    # Results are ready — point the user at the three result pages above
    # ==========================================================================
    if st.session_state.processing_complete and _has_any_results():
        st.divider()
        _render_step_header(4, "📊", "نتایج پایش")
        ready_message = (
            'نتایج ماه‌های کامل در دسترس است؛ برای تکمیل بازه، پایش را ادامه دهید.'
            if st.session_state.resume_after_interruption
            else '✅ پایش با موفقیت انجام شد.'
        )

        st.markdown(
            f"""
            <div class="wq-ready-banner">
                {ready_message}<br>
                برای مشاهده نتایج، از منوی کناری یکی از صفحه‌های
                <b>🌊 کدورت</b>، <b>🌿 کلروفیل</b>، <b>🍂 مواد آلی</b>،
                <b>🧭 خلاصه</b> یا <b>🤖 چت بات</b> را انتخاب کنید.
            </div>
            """,
            unsafe_allow_html=True,
        )

        # --- Download combined time-series (all three indices) as one .xlsx ---
        st.download_button(
            label="⬇️ دانلود سری زمانی کدورت آب، کلروفیل و مواد آلی محلول — یک فایل اکسل",
            data=generate_combined_timeseries_excel(),
            file_name="water_quality_timeseries.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )


def main():
    from .expert import render_expert_chat_tab, render_summary_page
    from .results import render_parameter_page
    _render_app_header()

    # Render controls and cached results even while Earth Engine is unavailable.
    # Initialization is attempted only when the user starts/resumes a run.
    # While a run is in progress the app always stays on the first page, so the
    # progress bar and the resume logic remain visible to the user.
    page = st.session_state.active_page

    if page == 'turbidity':
        render_parameter_page(PARAM_TURBIDITY)
    elif page == 'chlorophyll':
        render_parameter_page(PARAM_CHLOROPHYLL)
    elif page == 'cdom':
        render_parameter_page(PARAM_CDOM)
    elif page == 'summary':
        render_summary_page()
    elif page == 'chat':
        render_expert_chat_tab()
    else:
        render_setup_page()

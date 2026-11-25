"""Initialize session state for each Streamlit session."""
import streamlit as st
from .config import ALL_PARAMETERS
from .quality import empty_param_dict


def initialize_session():
    if 'drawn_polygons' not in st.session_state:
        st.session_state.drawn_polygons = []
    if 'last_drawn_polygon' not in st.session_state:
        st.session_state.last_drawn_polygon = None
    if 'ee_initialized' not in st.session_state:
        st.session_state.ee_initialized = False
    if 'current_temp_dir' not in st.session_state:
        st.session_state.current_temp_dir = None
    if 'downloaded_months' not in st.session_state:
        # nested by parameter: {PARAM_TURBIDITY: {...}, PARAM_CHLOROPHYLL: {...}}
        st.session_state.downloaded_months = empty_param_dict(dict)
    if 'month_statuses' not in st.session_state:
        st.session_state.month_statuses = empty_param_dict(dict)
    if 'results' not in st.session_state:
        # nested by parameter
        st.session_state.results = empty_param_dict(list)
    if 'processing_complete' not in st.session_state:
        st.session_state.processing_complete = False
    if 'selected_region_index' not in st.session_state:
        st.session_state.selected_region_index = 0
    if 'processing_in_progress' not in st.session_state:
        st.session_state.processing_in_progress = False
    if 'processing_config' not in st.session_state:
        st.session_state.processing_config = None
    if 'mean_data' not in st.session_state:
        st.session_state.mean_data = empty_param_dict(dict)
    if 'download_summary' not in st.session_state:
        # simple end-user facing summary: {PARAM_TURBIDITY: (downloaded, available), ...}
        st.session_state.download_summary = {}
    if 'resume_after_interruption' not in st.session_state:
        # True when a previous run was interrupted and can be resumed
        st.session_state.resume_after_interruption = False
    if 'connection_interrupted' not in st.session_state:
        # Set by the processing loop when the link to Earth Engine drops, so the
        # run stops at that point instead of failing every remaining month
        st.session_state.connection_interrupted = False
    if 'cdom_display_range' not in st.session_state:
        # (vmin, vmax) colour range derived from the CDOM data of the current run
        st.session_state.cdom_display_range = None
    if 'map_version' not in st.session_state:
        # Bumped whenever a region is saved or deleted. It is part of the map
        # widget's key, so the widget is remounted and drops the shape still held
        # by its own drawing toolbar — otherwise a deleted region would be sent
        # back by the widget and reappear on the map.
        st.session_state.map_version = 0
    if 'pending_run' not in st.session_state:
        # 'start' | 'resume' | None — a click arms the run, the next script run
        # executes it (so the page is fully rendered before processing begins)
        st.session_state.pending_run = None
    if 'last_run_error' not in st.session_state:
        st.session_state.last_run_error = None
    if 'active_page' not in st.session_state:
        # Which of the four top-navigation pages is currently shown:
        # 'setup' | 'turbidity' | 'chlorophyll' | 'chat'
        st.session_state.active_page = 'setup'

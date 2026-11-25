"""Results functions for the monitoring application."""
import numpy as np
import matplotlib.pyplot as plt
from shapely.geometry import Polygon
import datetime
import streamlit as st

from .config import (
    PARAM_TURBIDITY,
    PARAM_CHLOROPHYLL,
    PARAM_CDOM,
    WQ_DISPLAY_CONDITIONS,
    WQ_CHART_FONT,
)

def render_turbidity_guidance_panel():
    """Permanently visible legend + management guidance for Turbidity (NDTI)."""
    from .processing import create_turbidity_colormap
    st.markdown("### 🎨 راهنمای رنگ و تفسیر مدیریتی — شاخص کدورت آب")

    col_legend, col_text = st.columns([1, 2])

    with col_legend:
        fig, ax = plt.subplots(figsize=(5, 0.45))
        cmap = create_turbidity_colormap()
        gradient = np.linspace(0, 1, 256).reshape(1, -1)
        ax.imshow(gradient, aspect='auto', cmap=cmap)
        ax.set_xticks([0, 128, 255])
        ax.set_xticklabels(['آب شفاف', 'متوسط', 'بسیار کدر'])
        ax.set_yticks([])
        st.pyplot(fig)
        plt.close(fig)

    with col_text:
        st.markdown(
            """
**افزایش کدورت آب:**
- کاهش کیفیت آب قابل استفاده برای کشاورزی و مصارف شهری
- افزایش هزینه‌های تصفیه آب
- کاهش نفوذ نور به آب و آسیب به اکوسیستم و آبزیان
- نشانه احتمالی فرسایش خاک، رسوب‌گذاری یا آلودگی در حوضه آبریز

**کاهش کدورت آب:**
- بهبود کیفیت آب و کاهش هزینه‌های تصفیه
- شرایط مطلوب‌تر برای زیست‌بوم آبی و ماهی‌پروری
- نشانه کنترل مؤثر فرسایش و مدیریت بهتر حوضه آبریز

**چرا پایش این شاخص مهم است؟**
پایش روند کدورت به مدیران امکان می‌دهد قبل از وقوع بحران (مانند رسوب‌گذاری در سدها یا
افزایش هزینه تصفیه) اقدام کنند. تغییرات ناگهانی معمولاً نشانه رویدادهایی مانند بارش‌های
شدید، فعالیت‌های عمرانی در بالادست یا تخلیه پساب است و نیازمند بررسی سریع است.
            """
        )


def render_chlorophyll_guidance_panel():
    """Permanently visible legend + management guidance for Chlorophyll-a."""
    from .processing import create_chlorophyll_colormap
    st.markdown("### 🎨 راهنمای رنگ و تفسیر مدیریتی — شاخص کلروفیل")

    col_legend, col_text = st.columns([1, 2])

    with col_legend:
        fig, ax = plt.subplots(figsize=(5, 0.45))
        cmap = create_chlorophyll_colormap()
        gradient = np.linspace(0, 1, 256).reshape(1, -1)
        ax.imshow(gradient, aspect='auto', cmap=cmap)
        ax.set_xticks([0, 128, 255])
        ax.set_xticklabels(['کم', 'متوسط', 'بالا (شکوفایی جلبکی)'])
        ax.set_yticks([])
        st.pyplot(fig)
        plt.close(fig)

    with col_text:
        st.markdown(
            """
**افزایش غلظت کلروفیل:**
- احتمال شکوفایی جلبکی و کاهش کیفیت آب آشامیدنی
- افزایش هزینه‌های تصفیه و خطر مسدود شدن فیلترها
- کاهش اکسیژن محلول در آب و خطر برای آبزیان و ماهی‌پروری
- در موارد شدید، احتمال سمیت آب و توقف موقت برداشت آب

**کاهش غلظت کلروفیل:**
- بهبود کیفیت آب و کاهش ریسک‌های بهداشتی
- کاهش هزینه‌های عملیاتی تصفیه‌خانه
- شرایط پایدارتر برای اکوسیستم آبی

**چرا پایش این شاخص مهم است؟**
افزایش ناگهانی کلروفیل معمولاً پیش‌نشانگر شکوفایی جلبکی است که در صورت عدم اقدام به‌موقع
می‌تواند منجر به توقف تأمین آب، هزینه‌های اضطراری تصفیه یا آسیب به صنعت ماهی‌پروری شود.
پایش منظم این شاخص امکان برنامه‌ریزی پیشگیرانه و کاهش ریسک اقتصادی را فراهم می‌کند.
            """
        )


def render_cdom_guidance_panel():
    """Permanently visible legend + management guidance for CDOM."""
    from .processing import create_cdom_colormap
    st.markdown("### 🎨 راهنمای رنگ و تفسیر مدیریتی — شاخص مواد آلی محلول رنگی")

    col_legend, col_text = st.columns([1, 2])

    with col_legend:
        fig, ax = plt.subplots(figsize=(5, 0.45))
        cmap = create_cdom_colormap()
        gradient = np.linspace(0, 1, 256).reshape(1, -1)
        ax.imshow(gradient, aspect='auto', cmap=cmap)
        ax.set_xticks([0, 128, 255])
        ax.set_xticklabels(['کم (آب زلال)', 'متوسط', 'زیاد (آب چای‌رنگ)'])
        ax.set_yticks([])
        st.pyplot(fig)
        plt.close(fig)

        value_range = st.session_state.get('cdom_display_range')
        if value_range:
            st.caption(
                f"محدوده رنگی این پایش: از {value_range[0]:.1f} تا {value_range[1]:.1f} "
                "(بر متر) — بر پایه‌ی داده‌های همین منطقه و بازه زمانی تعیین شده است."
            )

    with col_text:
        st.markdown(
            """
**افزایش مواد آلی محلول رنگی:**
- تیره و چای‌رنگ شدن آب و کاهش مطلوبیت آب شرب (رنگ، بو و مزه)
- افزایش مصرف مواد ضدعفونی‌کننده و خطر تشکیل فرآورده‌های جانبی گندزدایی در تصفیه‌خانه
- کاهش نفوذ نور به اعماق و تغییر شرایط زیستی بدنه آبی
- نشانه احتمالی ورود رواناب حوضه آبریز، زهکشی اراضی آلی/تالابی، یا تخلیه فاضلاب

**کاهش مواد آلی محلول رنگی:**
- شفاف‌تر شدن آب و کاهش نیاز به مواد شیمیایی در تصفیه
- کاهش ریسک بهداشتی مرتبط با فرآورده‌های جانبی گندزدایی
- نشانه کاهش ورود بار آلی از حوضه آبریز

**چرا پایش این شاخص مهم است؟**
مواد آلی محلول رنگی، برخلاف کدورت، ذرات معلق نیستند و با ته‌نشینی ساده حذف نمی‌شوند؛
بنابراین افزایش آن مستقیماً بر هزینه و پیچیدگی فرایند تصفیه آب شرب اثر می‌گذارد. این شاخص
معمولاً پس از بارش‌های سنگین و ذوب برف (ورود رواناب از خاک و پوشش گیاهی حوضه) افزایش
می‌یابد و یکی از بهترین نشانگرهای ورود بار آلی از سطح حوضه به بدنه آبی است.

*این شاخص از نسبت باند سبز به قرمز سنتینل-۲ و رابطه‌ی Brezonik و همکاران (۲۰۰۵) محاسبه
می‌شود و تقریبی از ضریب جذب مواد آلی محلول در طول موج ۴۴۰ نانومتر (بر متر) است.*
            """
        )


def display_side_by_side_imagery(results, parameter_type):
    """Side-by-side processed index image and corresponding RGB image."""
    from .quality import format_param_value, param_persian_name
    if not results:
        st.info("داده‌ای برای نمایش در این بازه زمانی وجود ندارد.")
        return

    param_fa = param_persian_name(parameter_type)

    for r in results:
        if r.get('wq_image') is None or r.get('rgb_image') is None:
            st.info(f"تصاویر ماه {r['month_name']} ذخیره نشده‌اند؛ برای مشاهده، تحلیل را دوباره اجرا کنید.")
            continue
        mean_str = format_param_value(parameter_type, r['mean_value'], empty="بدون داده")

        cols = st.columns(2)
        cols[0].image(r['wq_image'], caption=f"{r['month_name']} — {param_fa}: {mean_str}", use_container_width=True)
        cols[1].image(r['rgb_image'], caption=f"{r['month_name']} — تصویر طبیعی (RGB)", use_container_width=True)


def _render_chart_heading(title):
    """Explicit webfont heading unaffected by Streamlit's heading theme."""
    import html as _html

    st.markdown(f'<h4 class="wq-chart-heading" dir="rtl">{_html.escape(title)}</h4>',
                unsafe_allow_html=True)


def _monthly_coverage_records(results):
    """Read the existing per-parameter coverage percentages; never infer area."""
    from .quality import _finite_monthly_value
    rows = []
    segment = 0
    for sequence, result in enumerate(results):
        month = str(result.get('month_name', '—'))
        try:
            parsed = datetime.datetime.strptime(month, '%Y-%m')
            month_date, year, month_number = parsed.strftime('%Y-%m-01'), str(parsed.year), parsed.month
        except ValueError:
            month_date, year, month_number = None, None, None
        coverage = _finite_monthly_value(result.get('water_coverage'))
        if coverage is None:
            segment += 1
        rows.append({'month': month, 'date': month_date, 'year': year,
                     'month_number': month_number, 'sequence': sequence,
                     'segment': segment, 'coverage': coverage})
    return rows


def _coverage_chart_config():
    """Shared typography for the separate coverage views."""
    return {
        'font': WQ_CHART_FONT, 'background': '#FFFFFF', 'view': {'stroke': None},
        'axis': {'labelFont': WQ_CHART_FONT, 'titleFont': WQ_CHART_FONT,
                 'labelFontSize': 12, 'titleFontSize': 14, 'titleFontWeight': 600,
                 'titlePadding': 16, 'labelColor': '#52647B', 'titleColor': '#334155',
                 'gridColor': '#E7EDF3', 'domain': False, 'tickColor': '#CED8E3'},
        'legend': {'labelFont': WQ_CHART_FONT, 'titleFont': WQ_CHART_FONT,
                   'labelFontSize': 12, 'titleFontSize': 13},
        'text': {'font': WQ_CHART_FONT},
    }


def _build_water_coverage_chart(results, parameter_type):
    """A separate presentation-only percentage chart, not a quality index."""
    from .quality import param_persian_name
    rows = _monthly_coverage_records(results)
    values = [row['coverage'] for row in rows if row['coverage'] is not None]
    if not values:
        return None
    temporal = all(row['date'] is not None for row in rows)
    x = {'field': 'date' if temporal else 'month',
         'type': 'temporal' if temporal else 'ordinal', 'title': 'ماه (میلادی)',
         'axis': {'labelAngle': -35, 'grid': False, 'labelOverlap': 'greedy'}}
    if temporal:
        x['scale'] = {'type': 'utc'}
        x['axis'].update({'format': '%Y-%m', 'tickCount': min(len(rows), 12)})
    else:
        x['sort'] = None
    y = {'field': 'coverage', 'type': 'quantitative', 'title': 'پوشش آب (درصد)',
         'scale': {'domain': [min(0.0, min(values)), max(100.0, max(values))], 'nice': False},
         'axis': {'format': '.0f', 'tickCount': 6}}
    shared = {'x': x, 'y': y, 'detail': {'field': 'segment', 'type': 'nominal'},
              'order': {'field': 'sequence', 'type': 'quantitative'}}
    return {
        '$schema': 'https://vega.github.io/schema/vega-lite/v5.json',
        'description': f'پوشش آب در داده‌های {param_persian_name(parameter_type)}',
        'data': {'values': rows}, 'height': 250,
        'usermeta': {'embedOptions': {'renderer': 'svg'}},
        'padding': {'left': 12, 'right': 18, 'top': 20, 'bottom': 12},
        'layer': [
            {'transform': [{'filter': 'isValid(datum.coverage)'}],
             'mark': {'type': 'area', 'color': '#087F8C', 'opacity': 0.09,
                      'interpolate': 'linear'}, 'encoding': shared},
            {'transform': [{'filter': 'isValid(datum.coverage)'}],
             'mark': {'type': 'line', 'color': '#087F8C', 'strokeWidth': 2.5,
                      'interpolate': 'linear'}, 'encoding': shared},
            {'transform': [{'filter': 'isValid(datum.coverage)'}],
             'mark': {'type': 'point', 'filled': True, 'size': 75, 'color': '#087F8C',
                      'stroke': '#FFFFFF', 'strokeWidth': 1.2},
             'encoding': {'x': x, 'y': y, 'tooltip': [
                 {'field': 'month', 'type': 'nominal', 'title': 'ماه (میلادی)'},
                 {'field': 'coverage', 'type': 'quantitative', 'title': 'پوشش آب (درصد)', 'format': '.2f'},
             ]}},
        ],
        'config': _coverage_chart_config(),
    }


def _render_water_coverage_panel(results, parameter_type):
    """Optional coverage view; keeps the main water-quality chart quality-only."""
    from .quality import param_persian_name
    with st.expander('💧 پوشش آب و تغییرات ماهانه', expanded=False):
        _render_chart_heading('روند ماهانه پوشش آب')
        st.caption(f'درصد پوشش آب در داده‌های {param_persian_name(parameter_type)} همین پایش')
        spec = _build_water_coverage_chart(results, parameter_type)
        if spec is None:
            st.info('داده معتبر پوشش آب برای این بازه زمانی در دسترس نیست.')
            return
        st.vega_lite_chart(spec=spec, use_container_width=True, theme=None)
        st.markdown(
            '<div class="wq-chart-note" dir="rtl">'
            'این درصدها همان مقادیر محاسبه‌شده پایش و سهم پیکسل‌های معتبر آب در تصاویر این شاخص هستند؛ '
            'بیانگر مساحت جغرافیایی آب یا حجم مخزن نیستند. '
            'رنگ آبی فقط میزان پوشش را نشان می‌دهد و طبقه‌بندی کیفیت آب نیست.'
            '</div>', unsafe_allow_html=True,
        )


def _build_water_quality_chart(results, parameter_type):
    """Native Streamlit/Vega-Lite presentation of unchanged monthly values.

    No satellite processing, interpolation, smoothing, normalization or area
    series is performed here. Null observations break the line instead of
    being plotted as zero. CDOM deliberately has no condition encoding.
    """
    from .quality import _monthly_quality_records, _quality_display_thresholds, param_persian_name
    records = _monthly_quality_records(results, parameter_type)
    values = [row['value'] for row in records if row['value'] is not None]
    if not values:
        return None

    thresholds = _quality_display_thresholds(parameter_type)
    classified = thresholds is not None
    temporal = all(row['date'] is not None for row in records)
    x_encoding = {
        'field': 'date' if temporal else 'month',
        'type': 'temporal' if temporal else 'ordinal',
        'title': 'ماه (میلادی)',
        'axis': {'labelAngle': -35, 'labelOverlap': 'greedy',
                 'labelPadding': 10, 'grid': False},
    }
    if temporal:
        x_encoding['axis'].update({'format': '%Y-%m', 'tickCount': min(len(records), 12)})
        x_encoding['scale'] = {'type': 'utc'}
    else:
        x_encoding['sort'] = None

    low, high = min(values), max(values)
    if classified:
        lower_threshold, middle_threshold, upper_threshold = thresholds
        low, high = min(low, lower_threshold), max(high, upper_threshold)
    padding = max((high - low) * 0.14, 0.08 if classified else 0.05)
    domain = [low - padding, high + padding]
    if parameter_type == PARAM_CDOM and low >= 0:
        domain[0] = max(0.0, domain[0])

    unit = 'بر متر' if parameter_type == PARAM_CDOM else 'بی‌بُعد'
    y_encoding = {
        'field': 'value', 'type': 'quantitative',
        'title': f'میانگین {param_persian_name(parameter_type)} ({unit})',
        'scale': {'domain': domain, 'zero': False, 'nice': False},
        'axis': {'format': '.3~f', 'tickCount': 6, 'labelPadding': 8},
    }
    tooltip = [
        {'field': 'month', 'type': 'nominal', 'title': 'ماه (میلادی)'},
        {'field': 'value', 'type': 'quantitative', 'title': f'مقدار ({unit})', 'format': '.6f'},
    ]
    if classified:
        tooltip.append({'field': 'condition_label', 'type': 'nominal', 'title': 'وضعیت ماه'})

    layers = []
    if classified:
        conditions = ['normal', 'warning', 'dangerous', 'critical']
        colors = [WQ_DISPLAY_CONDITIONS[key]['accent'] for key in conditions]
        # Finite visible extents are chart bounds only; readings remain untouched.
        bands = [
            {'lower': domain[0], 'upper': lower_threshold, 'condition': 'normal'},
            {'lower': lower_threshold, 'upper': middle_threshold, 'condition': 'warning'},
            {'lower': middle_threshold, 'upper': upper_threshold, 'condition': 'dangerous'},
            {'lower': upper_threshold, 'upper': domain[1], 'condition': 'critical'},
        ]
        layers.append({
            'data': {'values': bands},
            'mark': {'type': 'rect', 'opacity': 0.20, 'tooltip': None},
            'encoding': {
                'y': {'field': 'lower', 'type': 'quantitative',
                      'scale': {'domain': domain, 'zero': False, 'nice': False},
                      'axis': y_encoding['axis'], 'title': y_encoding['title']},
                'y2': {'field': 'upper'},
                'color': {'field': 'condition', 'type': 'nominal',
                          'scale': {'domain': conditions,
                                    'range': [WQ_DISPLAY_CONDITIONS[key]['band'] for key in conditions]},
                          'legend': None},
            },
        })
        references = [
            {'threshold': lower_threshold, 'label': 'مرز نرمال و هشدار'},
            {'threshold': middle_threshold, 'label': 'مرز هشدار و خطرناک'},
            {'threshold': upper_threshold, 'label': 'مرز خطرناک و بحرانی'},
        ]
        layers.append({
            'data': {'values': references},
            'mark': {'type': 'rule', 'stroke': '#8392A5', 'strokeWidth': 1,
                     'strokeDash': [5, 5], 'opacity': 0.7},
            'encoding': {
                'y': {'field': 'threshold', 'type': 'quantitative',
                      'axis': y_encoding['axis'], 'title': y_encoding['title']},
                'tooltip': [
                    {'field': 'label', 'type': 'nominal', 'title': 'مرز وضعیت'},
                    {'field': 'threshold', 'type': 'quantitative', 'title': 'مقدار', 'format': '.1f'},
                ],
            },
        })
        layers.append({
            'data': {'values': references},
            'mark': {'type': 'text', 'align': 'right', 'baseline': 'bottom',
                     'dx': -8, 'dy': -7, 'font': WQ_CHART_FONT,
                     'fontSize': 12, 'fontWeight': 500, 'color': '#52647B'},
            'encoding': {
                'x': {'value': 'width'},
                'y': {'field': 'threshold', 'type': 'quantitative',
                      'axis': y_encoding['axis'], 'title': y_encoding['title']},
                'text': {'field': 'label', 'type': 'nominal'},
            },
        })

    line_color = '#334E6C' if classified else '#946020'
    layers.append({
        'transform': [{'filter': 'isValid(datum.value)'}],
        'mark': {'type': 'line', 'color': line_color, 'strokeWidth': 2.5,
                 'interpolate': 'linear'},
        'encoding': {'x': x_encoding, 'y': y_encoding,
                     'detail': {'field': 'segment', 'type': 'nominal'},
                     'order': {'field': 'sequence', 'type': 'quantitative'}},
    })
    point_encoding = {'x': x_encoding, 'y': y_encoding, 'tooltip': tooltip}
    point_mark = {'type': 'point', 'filled': True, 'size': 105,
                  'stroke': '#FFFFFF', 'strokeWidth': 1.5, 'opacity': 1}
    if classified:
        point_encoding['color'] = {
            'field': 'condition', 'type': 'nominal',
            'scale': {'domain': conditions, 'range': colors},
            'legend': None,
        }
        point_encoding['shape'] = {
            'field': 'condition', 'type': 'nominal',
            'scale': {'domain': conditions,
                      'range': ['circle', 'square', 'triangle-up', 'diamond']}, 'legend': None,
        }
    else:
        point_mark['color'] = line_color
    layers.append({
        'transform': [{'filter': 'isValid(datum.value)'}],
        'mark': point_mark, 'encoding': point_encoding,
    })
    return {
        '$schema': 'https://vega.github.io/schema/vega-lite/v5.json',
        'description': f'روند ماهانه {param_persian_name(parameter_type)}',
        'data': {'values': records}, 'layer': layers,
        'resolve': {'scale': {'color': 'independent'}},
        'height': 370, 'padding': {'left': 12, 'right': 18, 'top': 24, 'bottom': 12},
        'usermeta': {'embedOptions': {'renderer': 'svg'}},
        'autosize': {'type': 'fit', 'contains': 'padding'},
        'config': {
            'font': WQ_CHART_FONT,
            'background': '#FFFFFF', 'view': {'stroke': None},
            'title': {'font': WQ_CHART_FONT, 'fontSize': 17, 'fontWeight': 700},
            'legend': {'labelFont': WQ_CHART_FONT, 'titleFont': WQ_CHART_FONT},
            'text': {'font': WQ_CHART_FONT, 'fontSize': 13},
            'axis': {'labelFont': WQ_CHART_FONT, 'titleFont': WQ_CHART_FONT,
                     'labelFontSize': 13, 'titleFontSize': 15, 'titleFontWeight': 600,
                     'titlePadding': 20, 'labelColor': '#52647B', 'titleColor': '#334155',
                     'gridColor': '#E7EDF3', 'gridOpacity': 0.85, 'domain': False,
                     'tickColor': '#CED8E3', 'labelLimit': 160},
        },
    }


def _render_monthly_quality_table(results, parameter_type):
    """Display table only. The original Excel export (including area) is unchanged."""
    from .interface import _render_persian_dataframe_html
    from .quality import _monthly_quality_records, param_persian_name
    import pandas as pd

    records = _monthly_quality_records(results, parameter_type)
    columns = {
        'ماه (میلادی)': [row['month'] for row in records],
        f'میانگین {param_persian_name(parameter_type)}': [row['display_value'] for row in records],
    }
    if parameter_type in (PARAM_TURBIDITY, PARAM_CHLOROPHYLL):
        columns['وضعیت ماه'] = [row['condition_label'] for row in records]
    with st.expander('📋 جدول داده‌های ماهانه'):
        _render_persian_dataframe_html(pd.DataFrame(columns))


def _render_quality_statistics(results, parameter_type):
    """Five coordinated cards for turbidity/chlorophyll, derived on every rerun."""
    from .quality import _finite_monthly_value, _overall_water_quality_condition, format_param_value, param_persian_name
    import html as _html

    overall_mean, condition = _overall_water_quality_condition(results, parameter_type)
    theme = WQ_DISPLAY_CONDITIONS[condition]
    values = [_finite_monthly_value(row.get('mean_value')) for row in results]
    values = [value for value in values if value is not None]
    coverage = [_finite_monthly_value(row.get('water_coverage')) for row in results]
    coverage = [value for value in coverage if value is not None]
    param_fa = param_persian_name(parameter_type)
    cards = [
        ('وضعیت کلی', theme['label'] if values else 'بدون داده',
         f'بر پایه {len(values)} ماه دارای داده' if values else 'داده معتبر در دسترس نیست', False),
        (f'میانگین {param_fa}', format_param_value(parameter_type, overall_mean), 'میانگین ماه‌های معتبر', True),
        (f'حداکثر {param_fa}', format_param_value(parameter_type, max(values) if values else None),
         'بیشترین میانگین ماهانه', True),
        (f'حداقل {param_fa}', format_param_value(parameter_type, min(values) if values else None),
         'کمترین میانگین ماهانه', True),
        ('میانگین پوشش آب', f'{np.mean(coverage):.1f}%' if coverage else '—', 'پوشش منطقه انتخاب‌شده', True),
    ]
    card_html = []
    for label, value, detail, numeric in cards:
        value_html = _html.escape(str(value))
        if numeric:
            value_html = f'<bdi dir="ltr">{value_html}</bdi>'
        card_html.append(
            '<article class="wq-stat-card">'
            f'<div class="wq-stat-label">{_html.escape(label)}</div>'
            f'<div class="wq-stat-value">{value_html}</div>'
            f'<div class="wq-stat-detail">{_html.escape(detail)}</div></article>'
        )
    st.markdown('#### خلاصه آماری')
    st.markdown(
        f'<div class="wq-stat-grid" dir="rtl" '
        f'style="--wq-state-accent:{theme["accent"]};--wq-state-bg:{theme["background"]};'
        f'--wq-state-border:{theme["border"]};">{"".join(card_html)}</div>',
        unsafe_allow_html=True,
    )
    if not values:
        st.info('داده معتبر کیفیت آب در این بازه زمانی در دسترس نیست.')
    if results:
        _render_monthly_quality_table(results, parameter_type)


def display_time_series_chart(results, parameter_type):
    """Interactive, Persian, quality-only monthly chart; CDOM is unclassified."""
    from .quality import _quality_display_thresholds, param_persian_name
    _render_chart_heading(f'روند زمانی {param_persian_name(parameter_type)}')
    thresholds = _quality_display_thresholds(parameter_type)
    classified = thresholds is not None
    if classified:
        lower_text, middle_text, upper_text = (f'{value:g}'.replace('-', '−') for value in thresholds)
        intervals = [
            ('normal', f'&lt; {lower_text}'),
            ('warning', f'[{lower_text}, {middle_text}]'),
            ('dangerous', f'({middle_text}, {upper_text}]'),
            ('critical', f'&gt; {upper_text}'),
        ]
        items = []
        for condition, interval in intervals:
            theme = WQ_DISPLAY_CONDITIONS[condition]
            items.append(
                '<span class="wq-condition-item">'
                f'<span class="wq-condition-dot" style="color:{theme["accent"]}">{theme["symbol"]}</span>'
                f'<span>{theme["label"]}</span> <bdi dir="ltr">{interval}</bdi></span>'
            )
        st.markdown(
            '<div class="wq-condition-legend" dir="rtl" aria-label="راهنمای وضعیت ماهانه">'
            + ''.join(items) + '</div>',
            unsafe_allow_html=True,
        )
    spec = _build_water_quality_chart(results, parameter_type)
    if spec is None:
        st.info('در این بازه زمانی، داده معتبر کیفیت آب برای نمایش نمودار موجود نیست.')
        return
    st.vega_lite_chart(spec=spec, use_container_width=True, theme=None)
    st.caption('برای مشاهده ماه، مقدار دقیق و وضعیت ماهانه، نشانگر را روی هر نقطه قرار دهید.'
               if classified else 'برای مشاهده ماه و مقدار دقیق مواد آلی محلول، نشانگر را روی هر نقطه قرار دهید.')
    if classified:
        st.markdown(
            '<div class="wq-chart-note" dir="rtl">'
            'رنگ نشانگرها وضعیت هر ماه را نشان می‌دهد؛ وضعیت کلی بر پایه میانگین ماه‌های معتبر است. '
            'طبقه‌بندی با مقادیر کامل و پیش از گرد کردن انجام می‌شود. ماه‌های بدون داده با صفر جایگزین نمی‌شوند. '
            'این راهنما مربوط به نمودار ماهانه است؛ رنگ تصاویر ماهواره‌ای مطابق راهنمای تصاویر باقی می‌ماند.'
            '</div>', unsafe_allow_html=True,
        )


def display_statistics_summary(results, parameter_type):
    """Statistics summary, contained within the same parameter page."""
    from .quality import format_param_value, param_persian_name
    if parameter_type in (PARAM_TURBIDITY, PARAM_CHLOROPHYLL):
        _render_quality_statistics(results, parameter_type)
        return

    # CDOM retains the four original cards and their original calculations.
    if not results:
        return

    param_fa = param_persian_name(parameter_type)

    months = [r['month_name'] for r in results]
    mean_values = [r['mean_value'] if not np.isnan(r['mean_value']) else 0 for r in results]
    coverage_values = [r['water_coverage'] for r in results]
    valid_values = [m for m in mean_values if m != 0]

    st.markdown("#### 📈 خلاصه آماری")

    col1, col2, col3, col4 = st.columns(4)

    if valid_values:
        col1.metric(f"میانگین {param_fa}", format_param_value(parameter_type, np.mean(valid_values)))
        col2.metric(f"حداکثر {param_fa}", format_param_value(parameter_type, np.max(valid_values)))
        col3.metric(f"حداقل {param_fa}", format_param_value(parameter_type, np.min(valid_values)))
    else:
        col1.metric(f"میانگین {param_fa}", "—")
        col2.metric(f"حداکثر {param_fa}", "—")
        col3.metric(f"حداقل {param_fa}", "—")

    col4.metric("میانگین پوشش آب", f"{np.mean(coverage_values):.1f}%")

    _render_monthly_quality_table(results, parameter_type)


def generate_combined_timeseries_excel():
    """
    Build a single Excel (.xlsx) workbook containing the monthly time-series
    values for ALL monitored parameters — Turbidity (NDTI), Chlorophyll (NDCI)
    and CDOM — as one sheet each. Returns workbook bytes for st.download_button.
    """
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)  # drop the default empty sheet

    header_font = Font(name='Arial', bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1F77B4', end_color='1F77B4', fill_type='solid')
    body_font = Font(name='Arial')
    center = Alignment(horizontal='center')

    center_lat, center_lon = _get_roi_center_coordinates()
    lat_out = round(float(center_lat), 6) if center_lat is not None else "—"
    lon_out = round(float(center_lon), 6) if center_lon is not None else "—"

    sections = [
        (PARAM_TURBIDITY, "کدورت (NDTI)", "میانگین NDTI"),
        (PARAM_CHLOROPHYLL, "کلروفیل (NDCI)", "میانگین NDCI"),
        (PARAM_CDOM, "مواد آلی محلول (CDOM)", "میانگین CDOM"),
    ]

    for parameter_type, sheet_name, value_header in sections:
        ws = wb.create_sheet(title=sheet_name)
        ws.sheet_view.rightToLeft = True

        headers = ["ماه", value_header, "پوشش آب (%)", "عرض جغرافیایی مرکز", "طول جغرافیایی مرکز"]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center

        results = st.session_state.results.get(parameter_type, [])
        for r in sorted(results, key=lambda x: x['month_name']):
            mean_val = r['mean_value']
            mean_out = round(float(mean_val), 4) if not np.isnan(mean_val) else "بدون داده"
            ws.append([r['month_name'], mean_out, round(float(r['water_coverage']), 1), lat_out, lon_out])

        if not results:
            ws.cell(row=2, column=1, value="داده‌ای موجود نیست.").font = body_font
        else:
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.font = body_font
                    cell.alignment = center

        column_widths = [14, 18, 16, 20, 20]
        for i, w in enumerate(column_widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _get_roi_center_coordinates():
    """
    Return (lat, lon) of the center (centroid) of the region of interest used
    for the current/most recent monitoring run, based on the polygon stored
    in st.session_state.processing_config. Returns (None, None) if no run has
    been configured yet.
    """
    config = st.session_state.get('processing_config')
    if not config or not config.get('polygon_coords'):
        return None, None
    try:
        centroid = Polygon(config['polygon_coords']).centroid
        return centroid.y, centroid.x
    except Exception:
        return None, None


def render_parameter_page(parameter_type):
    """
    Full page for one parameter, in the required order:
    1. Statistics Summary (خلاصه آماری)
    2. Legend + Management Guidance Panel
    3. Time-series chart
    4. Side-by-side imagery (collapsible)
    """
    from .expert import _render_param_brief
    from .interface import _render_active_section_badge
    if parameter_type == PARAM_TURBIDITY:
        _render_active_section_badge("🌊", "کدورت آب", "#0B6E76", "#2FC2CE")
    elif parameter_type == PARAM_CDOM:
        _render_active_section_badge("🍂", "مواد آلی محلول رنگی", "#7A4A12", "#D9A05B")
    else:
        _render_active_section_badge("🌿", "شاخص کلروفیل", "#1B7A3D", "#4CC26B")

    results = st.session_state.results.get(parameter_type, [])

    # Expert brief for THIS parameter, at the very top of the page.
    # Generated once per parameter per monitoring run and then cached.
    if results:
        _render_param_brief(parameter_type)

    if results or parameter_type in (PARAM_TURBIDITY, PARAM_CHLOROPHYLL):
        display_statistics_summary(results, parameter_type)
        st.divider()

    if parameter_type == PARAM_TURBIDITY:
        render_turbidity_guidance_panel()
    elif parameter_type == PARAM_CDOM:
        render_cdom_guidance_panel()
    else:
        render_chlorophyll_guidance_panel()

    st.divider()

    if not results:
        st.info("برای مشاهده نتایج، ابتدا یک منطقه را انتخاب و پایش را اجرا کنید.")
        return

    # Time-series chart first (right after the legend), processed imagery after
    display_time_series_chart(results, parameter_type)

    _render_water_coverage_panel(results, parameter_type)

    st.divider()

    with st.expander("🖼️ تصاویر پردازش‌شده (برای نمایش/پنهان‌سازی کلیک کنید)", expanded=False):
        display_side_by_side_imagery(results, parameter_type)

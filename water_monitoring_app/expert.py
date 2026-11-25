"""Expert functions for the monitoring application."""
import os
import numpy as np
import math
import requests
import json
import streamlit as st

from .config import (
    ALL_PARAMETERS,
    OPENAI_BASE_URL,
    OPENAI_API_KEY,
    EXPERT_CHAT_MODEL,
    TAVILY_API_KEY,
    OPENWEATHER_API_KEY,
    EXECUTIVE_SUMMARY_PROMPT,
    PARAM_PAGE_SUMMARY_PROMPTS,
    PARAM_BRIEF_THEME,
    QUICK_BRIEF_PROMPT,
    QUICK_DEEP_PROMPT,
    EXPERT_REQUEST_TIMEOUT_SECONDS,
    EXPERT_MAX_WEATHER_MONTHS,
    EXPERT_RECURSION_LIMIT,
)

def _expert_compute_trend(values, n):
    """Mann-Kendall trend test: standard (always) + seasonal (once >=24 months exist)."""
    import pymannkendall as mk

    result = {}

    if n < 4:
        result["standard"] = {"available": False, "reason": f"Mann-Kendall needs at least ~4 points; only {n} available."}
        result["seasonal"] = {"available": False, "reason": "Not enough data."}
        return result

    mk_res = mk.original_test(values)
    result["standard"] = {
        "method": "Mann-Kendall (non-seasonal)",
        "trend": mk_res.trend,
        "significant": bool(mk_res.h),
        "p_value": float(mk_res.p),
        "tau": float(mk_res.Tau),
        "sen_slope_per_month": float(mk_res.slope),
        "note": ("Does not separate the seasonal cycle from the trend. "
                 "If a strong seasonal pattern exists, prefer the seasonal result below when available.")
    }

    if n >= 24:
        try:
            smk_res = mk.seasonal_test(values, period=12)
            result["seasonal"] = {
                "method": "Seasonal Mann-Kendall (Hirsch-Slack), period=12 months",
                "trend": smk_res.trend,
                "significant": bool(smk_res.h),
                "p_value": float(smk_res.p),
                "sen_slope_per_month": float(smk_res.slope),
                "replicates_per_season": round(n / 12, 1),
                "power_caution": (f"Only ~{n/12:.1f} years of data per calendar month are available. "
                                   "Seasonal Mann-Kendall has low statistical power below ~3-4 years; "
                                   "treat this result as directional, not conclusive.")
            }
        except Exception as e:
            result["seasonal"] = {"available": False, "reason": f"Could not compute: {e}"}
    else:
        result["seasonal"] = {
            "available": False,
            "reason": f"Needs at least 24 months (2 full years) to run; only {n} months available."
        }

    return result


def _expert_detect_anomalies_mad(df, date_col, value_column, threshold=3.5):
    """Median Absolute Deviation based anomaly detection."""
    values = df[value_column]
    median = values.median()
    mad = np.median(np.abs(values - median))

    if mad == 0:
        return []

    modified_z = 0.6745 * (values - median) / mad

    anomalies = []
    for idx, row in df.iterrows():
        if abs(modified_z[idx]) > threshold:
            anomalies.append({
                "date": row[date_col].strftime("%Y-%m"),
                "value": float(row[value_column]),
                "modified_z_score": round(float(modified_z[idx]), 2)
            })
    return anomalies


def _expert_analyze_sheet(df, value_column, date_col=None, water_col=None):
    import pandas as pd

    date_col = date_col or df.columns[0]
    water_col = water_col or df.columns[-1]

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col).reset_index(drop=True)

    values = df[value_column]
    n = len(values)

    raw_observations = [
        {
            "date": row[date_col].strftime("%Y-%m"),
            "value": float(row[value_column]),
            "water_coverage_pct": float(row[water_col])
        }
        for _, row in df.iterrows()
    ]

    trend = _expert_compute_trend(values.values, n)
    anomalies = _expert_detect_anomalies_mad(df, date_col, value_column)

    df["MonthNumber"] = df[date_col].dt.month
    seasonal_climatology = df.groupby("MonthNumber")[value_column].mean().round(4).to_dict()

    summary = {
        "period": {
            "start": df[date_col].min().strftime("%Y-%m"),
            "end": df[date_col].max().strftime("%Y-%m"),
            "months": int(n)
        },
        "raw_observations": raw_observations,
        "statistics": {
            "mean": float(values.mean()),
            "median": float(values.median()),
            "std": float(values.std()),
            "variance": float(values.var()),
            "min": float(values.min()),
            "max": float(values.max()),
            "range": float(values.max() - values.min())
        },
        "trend": trend,
        "extremes": {
            "minimum": {"date": df.loc[values.idxmin(), date_col].strftime("%Y-%m"), "value": float(values.min())},
            "maximum": {"date": df.loc[values.idxmax(), date_col].strftime("%Y-%m"), "value": float(values.max())}
        },
        "seasonal_climatology": seasonal_climatology,
        "water_coverage": {
            "mean_percent": float(df[water_col].mean()),
            "minimum_percent": float(df[water_col].min()),
            "maximum_percent": float(df[water_col].max())
        },
        "missing_values": int(values.isna().sum()),
        "anomalies": anomalies
    }
    return summary


def _expert_compute_correlation_by_date(df1, value_col1, df2, value_col2, date_col1=None, date_col2=None):
    """Correlate two parameter series matched by actual date, not row position."""
    import pandas as pd

    date_col1 = date_col1 or df1.columns[0]
    date_col2 = date_col2 or df2.columns[0]

    d1 = df1[[date_col1, value_col1]].copy()
    d1[date_col1] = pd.to_datetime(d1[date_col1])
    d1 = d1.rename(columns={date_col1: "date", value_col1: "v1"})

    d2 = df2[[date_col2, value_col2]].copy()
    d2[date_col2] = pd.to_datetime(d2[date_col2])
    d2 = d2.rename(columns={date_col2: "date", value_col2: "v2"})

    merged = pd.merge(d1, d2, on="date", how="inner")
    unmatched_1 = set(d1["date"]) - set(merged["date"])
    unmatched_2 = set(d2["date"]) - set(merged["date"])

    return {
        "pearson_correlation": float(merged["v1"].corr(merged["v2"])),
        "n_matched_dates": int(len(merged)),
        "unmatched_dates_first_only": sorted(d.strftime("%Y-%m") for d in unmatched_1),
        "unmatched_dates_second_only": sorted(d.strftime("%Y-%m") for d in unmatched_2),
    }


def analyze_water_quality_from_bytes(excel_bytes):
    """
    Same logic as the standalone analyze_water_quality(excel_file) from the
    analysis notebook, adapted to read the in-memory Excel bytes produced by
    generate_combined_timeseries_excel() instead of a file on disk.

    Robustness addition (not present in the original notebook): a month with
    no data is written into the Excel export as the text "بدون داده" rather
    than a number, which would otherwise turn the whole column non-numeric
    and silently drop that parameter from the analysis. Those cells are
    coerced to NaN and excluded here instead.

    Also mirrors the notebook's latest addition: the center coordinates of
    the region of interest (columns "عرض جغرافیایی مرکز" / "طول جغرافیایی
    مرکز" in the exported Excel) are lifted into top-level
    "center_latitude" / "center_longitude" keys of the returned summary, so
    the location-aware chat agent can use them directly (e.g. to fetch
    historical weather for that exact point) without re-parsing the sheet.
    """
    import io
    import pandas as pd

    excel_buffer = io.BytesIO(excel_bytes)
    xls = pd.ExcelFile(excel_buffer)
    sheet_names = xls.sheet_names
    results = {}
    cleaned_frames = {}

    for sheet in sheet_names:
        df = pd.read_excel(excel_buffer, sheet_name=sheet)
        numeric_like_cols = [c for c in df.columns if c != df.columns[0]]
        for c in numeric_like_cols:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=numeric_like_cols[:1])  # drop rows with no value for the main indicator

        numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
        if len(numeric_cols) < 2 or df.empty:
            continue
        value_col = numeric_cols[0]
        water_col = "پوشش آب (%)" if "پوشش آب (%)" in df.columns else None
        results[sheet] = _expert_analyze_sheet(df, value_col, water_col=water_col)
        cleaned_frames[sheet] = df

    # Correlations between every pair of parameters (turbidity ↔ chlorophyll,
    # turbidity ↔ CDOM, chlorophyll ↔ CDOM), each matched by calendar month.
    if len(cleaned_frames) >= 2:
        sheets = list(cleaned_frames.keys())
        relationships = {}
        for i in range(len(sheets)):
            for j in range(i + 1, len(sheets)):
                sheet_a, sheet_b = sheets[i], sheets[j]
                df1, df2 = cleaned_frames[sheet_a], cleaned_frames[sheet_b]
                try:
                    col1 = df1.select_dtypes(include=np.number).columns[0]
                    col2 = df2.select_dtypes(include=np.number).columns[0]
                    relationships[f"{sheet_a} ↔ {sheet_b}"] = \
                        _expert_compute_correlation_by_date(df1, col1, df2, col2)
                except Exception:
                    continue
        if relationships:
            results["relationships"] = relationships

    # --- Lift region center coordinates to the top level (for the chat agent) ---
    if cleaned_frames:
        first_sheet_df = list(cleaned_frames.values())[0]
        lat_col = "عرض جغرافیایی مرکز"
        lon_col = "طول جغرافیایی مرکز"
        if lat_col in first_sheet_df.columns and lon_col in first_sheet_df.columns:
            lat_vals = pd.to_numeric(first_sheet_df[lat_col], errors='coerce').dropna()
            lon_vals = pd.to_numeric(first_sheet_df[lon_col], errors='coerce').dropna()
            if not lat_vals.empty and not lon_vals.empty:
                results["center_latitude"] = float(lat_vals.iloc[0])
                results["center_longitude"] = float(lon_vals.iloc[0])

    return results


def _expert_results_signature():
    """Cheap signature used to detect when monitoring results changed, so the
    JSON summary + chat history for نظر متخصص آب can be refreshed automatically."""
    sig = []
    for p in ALL_PARAMETERS:
        results = st.session_state.results.get(p, [])
        sig.append(tuple(sorted(
            (r['month_name'], None if np.isnan(r['mean_value']) else round(float(r['mean_value']), 6))
            for r in results
        )))
    return tuple(sig)


def _build_agent_system_prompt(analysis_json):
    """
    Persian system prompt for the water-quality expert agent. Combines the
    original evidence-based / no-hallucination rules with instructions for
    when to use each of the three available tools (reverse geocoding, web
    search, historical weather). The statistical analysis JSON (including,
    when available, center_latitude / center_longitude of the monitored
    region) is embedded directly in the prompt, exactly as in the previous
    non-agent version — it is NOT exposed as a separate callable tool.
    """
    return f"""شما یک متخصص باتجربه در زمینه کیفیت آب، سنجش‌ازدور ماهواره‌ای (سنتینل-۲)، و اقلیم‌شناسی هستید.

در ادامه، خلاصه‌ی تحلیل آماری سری زمانی **سه شاخص** کیفیت آب یک بدنه‌ی آبی، به‌صورت JSON در اختیار
شما قرار گرفته است. این خلاصه شامل مختصات مرکز منطقه (کلیدهای center_latitude و center_longitude،
در صورت وجود)، نتیجه‌ی آزمون روند من-کندال، ناهنجاری‌های شناسایی‌شده (بر پایه‌ی انحراف مطلق از
میانه)، الگوی فصلی چندساله، آمار توصیفی، و همبستگی دوبه‌دوی شاخص‌ها (کلید relationships) است.

سه شاخص موجود در داده‌ها (هر سه روی دقیقاً یک ماسک پهنه‌ی آبی و یک زنجیره‌ی پیش‌پردازش یکسان —
حذف ابر، حذف برف، و استخراج پهنه آب با AWEIsh — محاسبه شده‌اند و بنابراین مستقیماً با هم قابل
مقایسه‌اند):

۱. «شاخص کدورت آب» (برگه‌ی «کدورت (NDTI)» در داده‌ها) — اختلاف نرمال‌شده‌ی باند قرمز و سبز؛ بی‌بعد و
   در بازه‌ی ۱- تا ۱+. افزایش آن یعنی آب گل‌آلودتر و ذرات معلق بیشتر (فرسایش خاک، رسوب، رواناب،
   فعالیت عمرانی بالادست).

۲. «شاخص کلروفیل» (برگه‌ی «کلروفیل (NDCI)») — اختلاف نرمال‌شده‌ی باند لبه‌ی قرمز و قرمز؛ بی‌بعد و در
   بازه‌ی ۱- تا ۱+. افزایش آن یعنی زیست‌توده‌ی جلبکی/فیتوپلانکتونی بیشتر و احتمال شکوفایی جلبکی.

۳. «شاخص مواد آلی محلول رنگی» (برگه‌ی «مواد آلی محلول (CDOM)») — از نسبت باند سبز به قرمز سنتینل-۲ و
   با رابطه‌ی CDOM = 537 × exp(−2.93 × (B3/B4)) محاسبه می‌شود (رابطه‌ی باندی Brezonik و همکاران،
   ۲۰۰۵) و تقریبی از ضریب جذب مواد آلی محلول در طول موج ۴۴۰ نانومتر، با واحد «بر متر»، است. این
   شاخص برخلاف دو شاخص قبلی نرمال‌شده نیست، همیشه مثبت است و خط خنثی در صفر ندارد؛ بنابراین مقادیر
   آن را هرگز مانند NDTI/NDCI در بازه‌ی ۱- تا ۱+ تفسیر نکنید.

نکات تفسیری مهم درباره‌ی شاخص مواد آلی محلول رنگی:
- این شاخص «ماده‌ی محلول» را می‌سنجد، نه ذرات معلق. بنابراین افزایش هم‌زمان کدورت و مواد آلی محلول
  معمولاً نشانه‌ی ورود رواناب از سطح حوضه (بارش سنگین یا ذوب برف) است، در حالی که افزایش مواد آلی
  محلول بدون افزایش کدورت بیشتر به زهکشی اراضی آلی/تالابی، تخلیه‌ی فاضلاب یا تجزیه‌ی مواد آلی درون
  خود بدنه‌ی آبی اشاره دارد.
- افزایش آن برای بهره‌بردار آب شرب مهم است: رنگ، بو و مزه‌ی آب را تغییر می‌دهد، مصرف مواد
  گندزدا را بالا می‌برد و خطر تشکیل فرآورده‌های جانبی گندزدایی را افزایش می‌دهد. این مواد با
  ته‌نشینی ساده حذف نمی‌شوند.
- مواد آلی محلول رنگی نور را جذب می‌کند و می‌تواند بر برآورد کلروفیل اثر بگذارد؛ اگر کلروفیل و مواد
  آلی محلول هم‌زمان و به‌شدت همبسته بودند، این احتمال را در تفسیر خود صریحاً ذکر کنید.

داده‌های تحلیل:
{analysis_json}

شما به سه ابزار دسترسی دارید:
۱. reverse_geocode: یافتن دقیق نام شهر/منطقه/کشور بر اساس یک مختصات جغرافیایی (عرض و طول جغرافیایی)،
   با استفاده از سرویس Geocoding شرکت OpenWeatherMap. هر زمان که نیاز به شناسایی نام منطقه‌ی مورد
   مطالعه بر اساس center_latitude / center_longitude موجود در داده‌های تحلیل بالا داشتید، ابتدا از
   همین ابزار استفاده کنید — نه حدس زدن و نه جست‌وجوی وب — چون این ابزار مستقیماً و با دقت بالا نام
   مکان را از روی مختصات برمی‌گرداند.
۲. tavily_search (جست‌وجوی وب عمومی): برای اطلاعات کیفی و کلی اقلیمی (نوع اقلیم، طبقه‌بندی کوپن،
   الگوهای فصلی بارش و دما، رویدادهای خاص مانند سیل یا خشک‌سالی در آن منطقه) که به‌صورت عددی در دسترس
   نیست و باید از منابع وب یافت شود. همچنین برای هر پرسش عمومی دیگری که نیاز به اطلاعات به‌روز از وب
   دارد.
۳. get_monthly_weather_stats: ابزار اصلیِ «تحلیل علّی» شما — داده‌های دقیق هواشناسی تاریخی (بایگانی
   Open-Meteo) را برای یک مختصات جغرافیایی و یک سال/ماه مشخص برمی‌گرداند: دمای بیشینه/کمینه روزانه،
   بارش، برف، سرعت باد و درصد روزهای برفی آن ماه. **نقش اصلی این ابزار پاسخ به سؤال مستقیم کاربر درباره‌ی
   وضعیت هوا نیست، بلکه یافتن دلیل محتمل روند یا ناهنجاری‌های شاخص‌های کیفیت آب است** — یعنی از این ابزار
   باید به‌عنوان بخشی از فرایند عادی و خودکار تحلیل خودتان استفاده کنید، دقیقاً مانند یک متخصص انسانی که
   قبل از تفسیر یک نوسان غیرعادی، بی‌درنگ و بدون نیاز به درخواست کاربر، داده‌ی هواشناسی همان ماه را
   بررسی می‌کند.

دستورالعمل‌های استفاده از ابزارها:
- **استفاده‌ی خودکار و پیش‌کنشانه از get_monthly_weather_stats (نه صرفاً واکنشی):** هر زمان که در
  داده‌های تحلیل بالا با یکی از موارد زیر مواجه شدید، پیش از پاسخ نهایی، به‌طور خودکار get_monthly_weather_stats
  را برای ماه‌(های) مرتبط فراخوانی کنید — حتی اگر کاربر صریحاً کلمه‌ی «هوا» یا «آب‌وهوا» را به کار نبرده
  باشد:
    الف) هر آیتم در فهرست anomalies (ناهنجاری‌های شناسایی‌شده با انحراف مطلق از میانه) — بررسی کنید آیا
         بارش شدید، ذوب برف (برف قابل‌توجه در ماه‌های قبل یا همان ماه)، یا باد شدید می‌تواند توضیح‌دهنده‌ی
         آن جهش/افت ناگهانی باشد.
    ب) ماه‌های extremes.minimum و extremes.maximum — همین بررسی برای مقادیر حداکثر و حداقل کل بازه.
    پ) وقتی trend.standard یا trend.seasonal یک روند معنادار (significant: true) نشان می‌دهد — چند ماه
       نمونه (برای مثال ابتدای، میانه، و انتهای بازه‌ی دارای روند) را از نظر بارش/دما/باد بررسی کنید تا
       ببینید آیا روند مشاهده‌شده با یک روند هواشناسی هم‌راستا (مثلاً کاهش تدریجی بارش، افزایش دما) قابل
       توضیح است یا خیر.
    ت) وقتی کاربر درباره‌ی «چرا» یک تغییر رخ داده، یا درباره‌ی مقایسه‌ی دو ماه/دو فصل سؤال می‌پرسد.
  خلاصه: get_monthly_weather_stats بخشی جدایی‌ناپذیر از تفسیر روند/ناهنجاری شماست، نه یک ابزار جانبی که
  فقط با درخواست مستقیم کاربر برای «وضعیت هوا» فعال می‌شود.
- برای مختصات این ابزار و reverse_geocode، همیشه از center_latitude / center_longitude موجود در داده‌های
  تحلیل بالا استفاده کنید (در صورت نبودن این مقادیر در داده‌ها، صریحاً به کاربر بگویید که مختصات منطقه
  در دسترس نیست). هرگز از کاربر نخواهید مختصات را برایتان ارسال کند.
- سایر ابزارها را فقط زمانی فراخوانی کنید که واقعاً لازم است: reverse_geocode هر زمان که نام منطقه هنوز
  شناسایی نشده؛ tavily_search برای زمینه‌ی کیفی/کلی اقلیمی یا رویدادهای خاص (سیل، خشک‌سالی) که در داده‌ی
  عددی Open-Meteo دیده نمی‌شود.
- هرگز مختصات یا نام منطقه را حدس نزنید. برای شناسایی نام منطقه، همیشه ابتدا از reverse_geocode استفاده
  کنید؛ فقط اگر reverse_geocode نتیجه‌ای نداد یا کاربر اطلاعات کیفی/توصیفی بیشتری خواست، سراغ
  tavily_search بروید.
- ترتیب پیشنهادی هنگام تفسیر یک نوسان یا ناهنجاری: ابتدا در صورت نامشخص بودن نام منطقه، آن را با
  reverse_geocode شناسایی کنید؛ سپس بلافاصله و بدون نیاز به تأیید کاربر، داده‌ی عددی دقیق ماه(های)
  موردنظر را با get_monthly_weather_stats بگیرید؛ و در صورت نیاز، زمینه‌ی کلی اقلیمی یا رویدادهای خاص
  را نیز با tavily_search تکمیل کنید.

محدودیت بررسی در هر پاسخ:
- از میان ماه‌های ناهنجاری، بیشینه/کمینه و روند، حداکثر چهار ماه متمایز و مهم را برای بررسی هواشناسی
  انتخاب کنید؛ لازم نیست برای تک‌تک موارد ابزار فراخوانی شود. ماه مشترک بین چند شاخص را تکرار نکنید.
- فراخوانی‌های مستقل را در یک مرحله انجام دهید و حداکثر در سه مرحله از ابزارها استفاده کنید؛ سپس
  پاسخ را با شواهد موجود تکمیل کنید. ابزار ناموفق را در همان پاسخ دوباره فراخوانی نکنید.
- اگر ابزار در دسترس نبود یا سقف بررسی تکمیل شد، همه بخش‌های تحلیل آب را از داده‌های موجود بنویسید
  و محدودیت بررسی علت هواشناسی را صریح بگویید. رابطه آماری به‌تنهایی اثبات علت نیست.

دستورالعمل‌های مربوط به تاریخ (تقویم شمسی):
- کاربر با تقویم شمسی (جلالی) صحبت می‌کند و تاریخ‌ها را در پاسخ‌های خود باید به همین صورت (مثلاً «مرداد
  ۱۴۰۳») بیان کنید، مگر آنکه خود کاربر از تقویم میلادی استفاده کند.
- داده‌های تحلیل بالا (raw_observations، anomalies، extremes، seasonal_climatology) و ورودی‌های ابزار
  get_monthly_weather_stats بر پایه‌ی تاریخ میلادی (year, month) هستند. تبدیل بین تقویم شمسی و میلادی را
  همیشه خودتان، به‌صورت داخلی و بی‌صدا انجام دهید.
- هرگز از کاربر معادل میلادی یک تاریخ شمسی، یا مختصات جغرافیایی منطقه را نپرسید؛ این اطلاعات یا در
  داده‌های تحلیل بالا موجود است یا باید خودتان با ابزارها به دست آورید.

دستورالعمل‌های پاسخ‌گویی:
۱. پاسخ خود را در درجه‌ی اول بر پایه‌ی داده‌های JSON بالا و در صورت لزوم نتایج ابزارها بنا کنید و از
   دانش عمومی خود درباره‌ی کیفیت آب، سنجش‌ازدور و علوم محیط‌زیست برای تفسیر و تکمیل پاسخ استفاده کنید.
۲. تفسیر را مبتنی بر شواهد ارائه دهید؛ به‌جای نسبت‌دادن هر نوسان یا ناهنجاری به‌طور پیش‌فرض به «خطای
   حسگر»، ابتدا توضیح‌های محیطی، هیدرولوژیکی و هواشناسی محتمل را با کمک get_monthly_weather_stats در
   نظر بگیرید (رواناب فصلی، بارندگی، ذوب برف، سیل، رسوب‌گذاری، شکوفایی جلبکی و مانند آن).
۳. اگر داده‌ی کافی برای نتیجه‌گیری قطعی وجود ندارد (برای نمونه کمتر از ۲۴ ماه برای آزمون فصلی، یا
   نتایج ابزارها ناکافی/متناقض بود)، این محدودیت را صریح بیان کنید؛ حدس قطعی نزنید و هیچ واقعیتی را از
   خود نسازید.
۴. در پاسخ نهایی، در صورت استفاده از ابزارها، بین «شناسایی منطقه»، «داده‌ی عددی دقیق هواشناسی مرتبط با
   روند/ناهنجاری» و «زمینه‌ی کلی اقلیمی» تمایز قائل شوید.
۵. هر سه شاخص را با هم و در کنار یکدیگر تفسیر کنید، نه جدا از هم. به همبستگی‌های موجود در کلید
   relationships توجه کنید و هر جا الگوی مشترک یا واگرایی معناداری بین شاخص‌ها دیدید (برای نمونه
   افزایش هم‌زمان کدورت و مواد آلی محلول پس از یک ماه پربارش، یا افزایش کلروفیل بدون تغییر دو شاخص
   دیگر)، آن را صریح بیان کرده و توضیح فرایندی آن را ارائه دهید.
۶. اگر پرسش کاربر به زبان فارسی باشد، پاسخ باید کاملاً و فقط به زبان فارسی نوشته شود و از هیچ مخفف یا
   واژه‌ی انگلیسی استفاده نشود (برای نمونه به‌جای NDTI بنویسید «شاخص کدورت آب»، به‌جای NDCI بنویسید
   «شاخص کلروفیل»، و به‌جای CDOM بنویسید «مواد آلی محلول رنگی»؛ به‌جای MAD بنویسید «انحراف مطلق از
   میانه»).
۷. اگر پرسش کاربر به زبان دیگری باشد، به همان زبان پاسخ دهید.

قاعده مخصوص خلاصه کوتاه یک پارامتر:
اگر درخواست، خلاصه ۲ تا ۳ خطی یک پارامتر است، پاسخ نهایی را فقط درباره همان پارامتر و در همان تعداد
خط، با زبان رسمی و ساده برای کاربر غیرمتخصص بنویسید. بررسی سایر شاخص‌ها و جزئیات ابزارها را در تحلیل
داخلی انجام دهید و در خروجی توضیح جداگانه نیاورید. از گزارش آمار و اعداد غیرضروری خودداری کنید و
قالب، طول و واژه‌های ساده خواسته‌شده در پرسش را رعایت کنید؛ محدودیت داده را نیز در همان چند خط بگویید.
"""


@st.cache_data(ttl=None, persist="disk", show_spinner=False)
def _reverse_geocode_cached(lat: float, lon: float) -> list:
    """Cached OpenWeatherMap reverse-geocoding lookup. Coordinates are rounded
    to ~100m precision so nearby points within the same monitored waterbody
    reuse the same cache entry instead of each triggering a fresh request."""
    params = {
        "lat": round(lat, 3),
        "lon": round(lon, 3),
        "limit": 5,
        "appid": OPENWEATHER_API_KEY,
    }
    r = requests.get(
        "https://api.openweathermap.org/geo/1.0/reverse",
        params=params,
        timeout=(5, 15),  # (connect, read) — fail fast instead of hanging up to 30s
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=None, persist="disk", show_spinner=False)
def _fetch_monthly_weather_cached(lat: float, lon: float, year: int, month: int) -> dict:
    """Cached Open-Meteo historical-archive lookup for one lat/lon/year/month.
    Historical weather for a past month never changes, so once fetched it is
    reused for every future question about that exact month — by any user,
    in any session — instead of hitting the API again.

    This helper backs the agent's get_monthly_weather_stats tool, whose main
    purpose is diagnostic: explaining *why* a turbidity/chlorophyll trend or
    anomaly occurred (heavy rainfall, snowmelt, wind-driven mixing, etc.),
    not just answering a direct question about the weather itself."""
    import calendar

    last_day = calendar.monthrange(year, month)[1]
    params = {
        "latitude": round(lat, 3),
        "longitude": round(lon, 3),
        "start_date": f"{year}-{month:02d}-01",
        "end_date": f"{year}-{month:02d}-{last_day}",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,wind_speed_10m_max",
        "timezone": "auto",
    }
    r = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params=params,
        timeout=(5, 15),  # (connect, read) — fail fast instead of hanging up to 30s
    )
    r.raise_for_status()
    return r.json()


def _get_expert_model():
    """Allow slow report generation without silently replaying a paid request.

    Streaming limits each read's wait for new bytes; this is an inactivity
    timeout, not a total generation deadline. The gateway may impose its own
    shorter limit, which must be diagnosed on the deployment if it persists.
    """
    from langchain.chat_models import init_chat_model

    return init_chat_model(
        model=EXPERT_CHAT_MODEL,
        model_provider="openai",
        base_url=OPENAI_BASE_URL,
        api_key=OPENAI_API_KEY,
        temperature=0.3,
        timeout=EXPERT_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
        streaming=True,
    )


def _summarize_expert_weather(daily):
    """Summarize observed values; missing weather must not become zero rain/snow."""
    import math

    def observed(key):
        return [float(value) for value in (daily.get(key) or [])
                if isinstance(value, (int, float)) and math.isfinite(value)]

    high = observed("temperature_2m_max")
    low = observed("temperature_2m_min")
    rain = observed("precipitation_sum")
    snow = observed("snowfall_sum")
    wind = observed("wind_speed_10m_max")
    snow_days = sum(value > 0 for value in snow) if snow else None
    return {
        "total_days": len(daily.get("time") or []),
        "snowfall_observed_days": len(snow),
        "snow_days": snow_days,
        "snow_day_percentage": round(100 * snow_days / len(snow), 1) if snow else None,
        "temperature_max_monthly": max(high) if high else None,
        "temperature_min_monthly": min(low) if low else None,
        "precipitation_monthly": sum(rain) if rain else None,
        "precipitation_observed_days": len(rain),
        "snowfall_monthly": sum(snow) if snow else None,
        "wind_speed_max_monthly": max(wind) if wind else None,
    }


def _get_expert_agent(analysis_json):
    """
    Build a fresh LangGraph ReAct agent with the three tools: OpenWeatherMap
    reverse geocoding, Tavily web search, and Open-Meteo historical weather.
    Rebuilt on every call since the system prompt embeds the current
    analysis JSON, which changes whenever new monitoring results are
    generated (see _expert_results_signature()). Building a ReAct agent is
    cheap (no network calls happen until a tool is actually invoked), so
    recreating it per question keeps the code simple and avoids
    stale-prompt bugs.

    API keys are read from environment-backed application configuration.

    The reverse_geocode and get_monthly_weather_stats tools below call the
    disk-cached helpers defined just above this function
    (_reverse_geocode_cached / _fetch_monthly_weather_cached) instead of
    hitting their APIs directly — see the comment on those helpers for why.

    Requires: langchain, langchain-openai, langgraph, langchain-tavily
    (pip install langchain langchain-openai langgraph langchain-tavily)
    """
    from threading import Lock
    from langchain_core.tools import tool
    from langchain_tavily import TavilySearch
    from langgraph.prebuilt import create_react_agent

    # TavilySearch reads its key from the environment.
    os.environ["TAVILY_API_KEY"] = TAVILY_API_KEY

    model = _get_expert_model()
    weather_months = set()
    weather_lock = Lock()  # a single model turn can request tools concurrently

    tavily_tool = TavilySearch(max_results=5, topic="general")

    @tool
    def reverse_geocode(lat: float, lon: float) -> str:
        """Reverse-geocode a latitude/longitude pair into a place name
        (city/town/village, state/province, and country) using the
        OpenWeatherMap Geocoding API. Use this whenever you need to identify
        the name of the region/city/country for a given set of coordinates
        (e.g. the center_latitude / center_longitude of the monitored water
        body) — it is faster and more precise than a general web search for
        this specific purpose."""
        try:
            results = _reverse_geocode_cached(lat, lon)
        except Exception as e:
            return str({
                "lat": lat, "lon": lon, "results": [],
                "error": f"geocoding service unavailable or timed out: {e}",
            })

        if not results:
            return str({"lat": lat, "lon": lon, "results": [], "note": "No place name found for these coordinates."})

        return str({
            "lat": lat,
            "lon": lon,
            "results": [
                {
                    "name": item.get("name"),
                    "state": item.get("state"),
                    "country": item.get("country"),
                    "local_names": item.get("local_names"),
                }
                for item in results
            ],
        })

    @tool
    def get_monthly_weather_stats(lat: float, lon: float, year: int, month: int) -> str:
        """Get daily historical weather data (max/min temperature, precipitation,
        snowfall, wind speed) for a given latitude/longitude and a specific
        year/month, using the Open-Meteo historical archive API. Also returns
        the number of days with snowfall and the snow-day percentage for that
        month.

        This is your primary DIAGNOSTIC tool, not just a way to answer direct
        weather questions. Its main purpose is to help you explain the likely
        physical cause of a turbidity/chlorophyll trend or anomaly found in
        the statistical analysis JSON (e.g. a spike coinciding with heavy
        precipitation or rapid snowmelt, or a chlorophyll rise coinciding with
        calm, warm, low-wind conditions favorable to algal blooms).

        Proactively select up to four important distinct months from anomalies,
        extremes or significant trends. Reuse a month shared by several water
        indices. Do not retry failed lookups in this answer. Once the budget is
        reached, finish the water-quality report with available evidence and
        explicitly state the limits on weather-based explanations.

        Always pass the center_latitude / center_longitude already present in
        the analysis JSON, and the Gregorian year/month equivalent of the
        month you are investigating (convert internally from Shamsi/Jalali if
        that is how the user phrased the date) — never ask the user for
        coordinates or for the Gregorian date."""
        weather_key = (round(lat, 3), round(lon, 3), year, month)
        with weather_lock:
            if weather_key in weather_months:
                return str({"error": "This month was already checked in this answer; use its earlier result, or state that it was unavailable."})
            if len(weather_months) >= EXPERT_MAX_WEATHER_MONTHS:
                return str({"error": "Weather lookup limit reached. Complete the report using available evidence and disclose unverified weather causes."})
            weather_months.add(weather_key)
        try:
            data = _fetch_monthly_weather_cached(lat, lon, year, month)
        except Exception as e:
            return str({"error": f"weather service unavailable or timed out: {e}"})

        daily = data.get("daily", {})
        # Preserve daily evidence once; do not duplicate the arrays as "summary".
        data = dict(data, summary=_summarize_expert_weather(daily))
        return str(data)

    tools = [reverse_geocode, tavily_tool, get_monthly_weather_stats]
    try:
        analysis_json = json.dumps(json.loads(analysis_json), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        pass
    system_prompt = _build_agent_system_prompt(analysis_json)
    return create_react_agent(model, tools, prompt=system_prompt)


def _expert_history_messages(chat_history):
    """Keep at most six recent complete, successful exchanges within 24k chars."""
    pairs = []
    pending = None
    for message in chat_history:
        if message.get("role") == "user":
            pending = message
        elif message.get("role") == "assistant":
            if (pending is not None and not message.get("error")
                    and not message.get("content", "").startswith("خطا در ارتباط با عامل هوش مصنوعی:")):
                pairs.append((pending, message))
            pending = None
    recent = []
    size = 0
    for pair in reversed(pairs[-6:]):
        pair_size = sum(len(message.get("content", "")) for message in pair)
        if size + pair_size > 24000:
            break
        recent.insert(0, pair)
        size += pair_size
    return [message for pair in recent for message in pair]


def _expert_message_text(content):
    """Display text only, excluding reasoning and tool-call content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content
                       if isinstance(block, dict) and block.get("type") == "text"
                       and isinstance(block.get("text"), str))
    return ""


def _expert_error_message(error):
    # Keep the original dependency/configuration error visible if the chatbot
    # packages themselves are missing; error rendering must not import-fail.
    try:
        from openai import APITimeoutError
        timeout_errors = (TimeoutError, APITimeoutError)
    except ImportError:
        timeout_errors = (TimeoutError,)

    if isinstance(error, timeout_errors):
        return ("پاسخ سرویس هوش مصنوعی در زمان مجاز دریافت نشد. لطفاً دوباره تلاش کنید. "
                "اگر این خطا تکرار شد، اتصال سرور برنامه به سرویس هوش مصنوعی و محدودیت زمان سرویس بررسی شود.")
    if any(cls.__name__ == "GraphRecursionError" for cls in type(error).__mro__):
        return "تعداد مراحل بررسی به سقف مجاز رسید. لطفاً پرسش را به یک موضوع یا بازه کوتاه‌تر محدود کنید."
    return f"خطا در ارتباط با عامل هوش مصنوعی: {error}"


def ask_water_quality_expert(question, analysis_json, chat_history, on_text=None, on_progress=None):
    """
    Send `question` to the water-quality expert agent, giving it the running
    chat history for context. chat_history is a list of
    {"role": "user"/"assistant", "content": ...} dicts — the same shape
    already used elsewhere in the app for st.session_state.expert_chat_history,
    so no other call site needs to change.
    """
    from langchain_core.messages import HumanMessage, AIMessage

    agent = _get_expert_agent(analysis_json)

    messages = []
    for msg in _expert_history_messages(chat_history):
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        else:
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=question))

    answer = ""
    partial = ""
    message_id = None
    tool_labels = {
        "reverse_geocode": "در حال شناسایی منطقه…",
        "get_monthly_weather_stats": "در حال بررسی داده‌های هواشناسی ماه‌های منتخب…",
        "tavily_search": "در حال بررسی منابع تکمیلی…",
    }
    if on_progress:
        on_progress("در حال تحلیل داده‌های آب؛ تهیه گزارش جامع ممکن است چند دقیقه طول بکشد…")
    for mode, event in agent.stream(
        {"messages": messages},
        config={"recursion_limit": EXPERT_RECURSION_LIMIT},
        stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            chunk, metadata = event
            if metadata.get("langgraph_node") != "agent":
                continue
            chunk_id = getattr(chunk, "id", None)
            if chunk_id != message_id:
                partial = ""
                message_id = chunk_id
            partial += _expert_message_text(chunk.content)
            if on_text and partial and not getattr(chunk, "tool_call_chunks", None):
                on_text(partial)
        elif mode == "updates":
            for node, update in event.items():
                if node != "agent" or not isinstance(update, dict):
                    continue
                for message in update.get("messages", []):
                    calls = getattr(message, "tool_calls", None)
                    if calls:
                        partial = ""
                        message_id = None
                        if on_text:
                            on_text("")
                        if on_progress:
                            on_progress(tool_labels.get(calls[0].get("name"), "در حال بررسی شواهد تکمیلی…"))
                    else:
                        answer = _expert_message_text(message.content)
                        # LangGraph can end with this sentinel instead of raising
                        # GraphRecursionError when its remaining steps run low.
                        if answer.strip() == "Sorry, need more steps to process this request.":
                            raise RuntimeError("تعداد مراحل بررسی به سقف مجاز رسید؛ لطفاً پرسش را به یک موضوع یا بازه کوتاه‌تر محدود کنید.")
                        if on_text:
                            on_text(answer)
    if not answer.strip():
        raise RuntimeError("سرویس هوش مصنوعی پاسخ متنی کامل برنگرداند؛ لطفاً دوباره تلاش کنید.")
    return answer


def _inject_persian_chat_css():
    """Readable Persian chat chrome, independent of the agent or its prompts."""
    st.markdown(
        """
        <style>
        [data-testid="stChatMessage"] {
            direction: rtl; text-align: right; background: #FFFFFF;
            border: 1px solid #DCE5EC; border-radius: 13px;
            padding: 1rem 1.2rem; margin-bottom: .8rem;
            box-shadow: 0 3px 12px rgba(20,44,64,.035);
        }
        [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"],
        [data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li {
            direction: rtl; text-align: right;
            font-family: 'Vazirmatn', Tahoma, 'B Nazanin', 'BNazanin', sans-serif;
            font-size: .98rem; line-height: 2;
        }
        [data-testid="stChatMessage"] pre, [data-testid="stChatMessage"] code {
            direction: ltr; text-align: left; unicode-bidi: isolate;
        }
        [data-testid="stChatInput"] textarea {
            direction: rtl; text-align: right; font-size: .96rem;
            font-family: 'Vazirmatn', Tahoma, 'B Nazanin', 'BNazanin', sans-serif;
        }
        [data-testid="stChatInput"] { border-radius: 12px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _force_quick_button_style():
    """Style the existing quick actions without altering the DOM or event handlers."""
    st.markdown(
        """
        <style>
        [class*="st-key-wqquick_"] button {
            min-height: 3.6rem; border-radius: 11px; padding: .7rem 1rem;
            font-family: 'Vazirmatn', Tahoma, 'B Nazanin', 'BNazanin', sans-serif;
            direction: rtl; font-size: 1rem; font-weight: 600; line-height: 1.8;
            box-shadow: 0 3px 12px rgba(20,44,64,.04); text-shadow: none;
        }
        [class*="st-key-wqquick_"] button p { font-size: 1rem; font-weight: 600; line-height: 1.8; }
        [class*="st-key-wqquick_brief"] button { background: #FFF7E9; color: #804B0F; border: 1px solid #E6D3AF; }
        [class*="st-key-wqquick_brief"] button:hover:not(:disabled) { background: #FAEFD9; border-color: #BA8E48; color: #6F400B; }
        [class*="st-key-wqquick_deep"] button { background: #EDF7F7; color: #086873; border: 1px solid #BFDDE0; }
        [class*="st-key-wqquick_deep"] button:hover:not(:disabled) { background: #DFEFF0; border-color: #589DA5; color: #07545D; }
        @supports selector(:has(*)) {
            [data-testid="stElementContainer"]:has(.wq-quick-anchor) + [data-testid="stHorizontalBlock"] button {
                min-height: 3.6rem; border-radius: 11px; font-size: 1rem;
                background: #EDF7F7; color: #086873; border: 1px solid #BFDDE0;
            }
            [data-testid="stElementContainer"]:has(.wq-nav-anchor) + [data-testid="stHorizontalBlock"] button[kind="primary"] {
                background: #142C40; color: #FFFFFF; border-color: #142C40;
                box-shadow: inset 0 -3px 0 #57B9BF;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _persist_expert_candidate(candidate, archive_current=False, mark_unsaved=False):
    """Commit an expert snapshot only if both its revision and run are current."""
    from .auth import AuthStore
    from .history import HistoryStore, HistoryConflict
    from .expert_snapshot import dump_expert_state

    if not st.session_state.get('active_history_id'):
        if mark_unsaved:
            st.session_state.history_unsaved = True
            st.session_state.history_unsaved_conflict = True
        st.error('این نشست به تحلیل ذخیره‌شده متصل نیست؛ اطلاعات متخصص خودکار ذخیره نمی‌شود. برای ذخیره، تحلیل تازه‌ای آغاز کنید.')
        return False
    try:
        actor = st.session_state['auth_user_id']
        run_id = st.session_state['active_history_id']
        token = st.session_state['history_expert_token']
        generation = st.session_state['history_generation']
        payload = dump_expert_state(candidate)
        store = HistoryStore(AuthStore())
        with store.run_lock(actor, run_id):
            new_token = store.save_expert_if_unchanged(
                actor, run_id, payload, token, generation, archive_current=archive_current
            )
        st.session_state.history_expert_token = new_token
        st.session_state.history_unsaved = False
        st.session_state.history_unsaved_conflict = False
        return True
    except Exception as exc:
        conflict = isinstance(exc, HistoryConflict)
        if mark_unsaved:
            st.session_state.history_unsaved = True
            st.session_state.history_unsaved_conflict = conflict
        if conflict:
            st.error('این تحلیل در نشست دیگری تغییر کرده است. متن این نشست ذخیره نشده؛ آن را کپی کنید و تحلیل را دوباره باز کنید.')
        else:
            st.error('ذخیره اطلاعات متخصص انجام نشد. متن این نشست را حفظ کنید و دوباره تلاش کنید.')
        return False


def _persist_expert_snapshot():
    return _persist_expert_candidate(st.session_state, mark_unsaved=True)


def _guard_pending_expert():
    """Preserve unsaved text and offer a retry for recoverable write failures."""
    if not st.session_state.get('history_unsaved', False):
        return True
    if st.session_state.get('history_unsaved_conflict', False):
        st.error('تغییرات این نشست با نسخه ذخیره‌شده تعارض دارند. متن را کپی و تحلیل را دوباره باز کنید.')
    else:
        st.warning('متن این نشست هنوز ذخیره نشده است. پیش از ادامه دوباره تلاش کنید.')
        if st.button('تلاش دوباره برای ذخیره اطلاعات متخصص', key='expert_retry_save'):
            if _persist_expert_snapshot():
                st.rerun()
    if st.button('متن را کپی کردم؛ کنارگذاشتن تغییرات و بازکردن دوباره تحلیل', key='expert_discard_unsaved'):
        run_id = st.session_state.get('active_history_id')
        if run_id:
            try:
                from .auth import AuthStore
                from .history import HistoryStore
                from .history_restore import restore_run
                replacement = {'history_unsaved': False, 'map_version': st.session_state.get('map_version', 0)}
                restore_run(HistoryStore(AuthStore()), st.session_state['auth_user_id'], run_id, replacement)
                replacement['history_unsaved_conflict'] = False
                st.session_state.update(replacement)
                st.rerun()
            except Exception:
                st.error('بازکردن دوباره انجام نشد؛ متن و تغییرات این نشست حفظ شده‌اند.')
        else:
            from .expert_snapshot import dump_expert_state, load_expert_state
            st.session_state.update(load_expert_state(dump_expert_state({})))
            st.session_state.history_unsaved = False
            st.session_state.history_unsaved_conflict = False
            st.warning('اطلاعات ذخیره‌نشده کنار گذاشته شد. برای ذخیره اطلاعات متخصص، تحلیل تازه‌ای آغاز کنید.')
    return False


def _render_expert_revisions():
    """Display previously archived conversations without mutating the session."""
    from .auth import AuthStore
    from .history import HistoryStore
    from .expert_snapshot import load_expert_state
    try:
        actor = st.session_state['auth_user_id']
        run_id = st.session_state['active_history_id']
        store = HistoryStore(AuthStore())
        with store.run_lock(actor, run_id):
            revisions = store.list_expert_revisions(actor, run_id)
        if not revisions:
            return
        with st.expander('خلاصه‌ها و گفتگوهای قبلی این تحلیل'):
            for index, payload in enumerate(reversed(revisions), 1):
                old = load_expert_state(payload)
                st.markdown(f'**نسخه قبلی {index}**')
                if old['executive_summary']:
                    st.write(old['executive_summary'])
                for message in old['expert_chat_history']:
                    st.write(('کاربر: ' if message['role'] == 'user' else 'متخصص: ') +
                             (message.get('display') or message['content']))
                for text in old['param_briefs'].values():
                    st.write(text)
    except Exception:
        st.warning('نسخه‌های قبلی اطلاعات متخصص قابل نمایش نیستند.')


def _ensure_expert_analysis():
    """
    Make sure the statistical-analysis JSON for the current monitoring results
    exists in session state, recomputing it when the results have changed.

    Shared by «خلاصه مدیریتی» and «چت بات» so both always talk to the agent
    about exactly the same analysis. Returns True when the JSON is ready.
    """
    from .results import generate_combined_timeseries_excel
    if 'expert_analysis_json' not in st.session_state:
        st.session_state.expert_analysis_json = None
    if 'expert_analysis_signature' not in st.session_state:
        st.session_state.expert_analysis_signature = None

    if not any(st.session_state.results.get(p) for p in ALL_PARAMETERS):
        return False

    signature = _expert_results_signature()
    if (st.session_state.expert_analysis_json is not None
            and st.session_state.expert_analysis_signature == signature):
        return True

    if not _guard_pending_expert():
        return False
    with st.spinner("در حال تحلیل آماری داده‌های سری زمانی..."):
        try:
            excel_bytes = generate_combined_timeseries_excel()
            analysis = analyze_water_quality_from_bytes(excel_bytes)
            from .expert_snapshot import dump_expert_state, load_expert_state
            candidate = load_expert_state(dump_expert_state(st.session_state))
            candidate.update(expert_analysis_json=json.dumps(analysis, ensure_ascii=False, indent=2),
                             expert_analysis_signature=signature, expert_chat_history=[],
                             executive_summary=None, executive_summary_signature=None,
                             param_briefs={}, param_brief_signatures={})
            if not _persist_expert_candidate(candidate, archive_current=True):
                return False
            st.session_state.update(candidate)
        except Exception as e:
            st.error(f"خطا در تحلیل داده‌ها: {e}")
            return False

    return True


def _render_summary_card(summary_text):
    """Render the 5-6 line summary as one readable RTL card, a line per row."""
    import html as _html

    lines = [ln.strip() for ln in str(summary_text).splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        lines = [str(summary_text).strip()]

    rows = "".join(
        f'<div class="wq-summary-line">{_html.escape(ln)}</div>' for ln in lines
    )
    st.markdown(f'<div class="wq-summary-card">{rows}</div>', unsafe_allow_html=True)


def _render_param_brief_card(parameter_type, brief_text):
    """Render the per-parameter expert brief as one styled RTL card, themed to
    match the colour of the page it sits on. Purely presentational."""
    import html as _html

    css_class, icon, title = PARAM_BRIEF_THEME.get(
        parameter_type,
        ("wq-brief-turbidity", "\U0001f9e0", "تحلیل کارشناسی هوش مصنوعی"),
    )

    # Preserve the requested short lines while normalising whitespace within
    # each line. Escape each line before inserting HTML line breaks.
    lines = [" ".join(line.split()) for line in str(brief_text).splitlines()]
    body = "<br>".join(_html.escape(line) for line in lines if line)

    st.markdown(
        f"""
        <div class="wq-param-brief {css_class}">
            <div class="wq-param-brief-head">
                <span class="wq-param-brief-icon">{icon}</span>
                <span class="wq-param-brief-title">{_html.escape(title)}</span>
            </div>
            <div class="wq-param-brief-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_param_brief(parameter_type):
    """
    تحلیل کارشناسی کوتاه بالای صفحه هر پارامتر: همان عامل متخصص و همان دستور
    سیستمی صفحه چت، اما با یک پرسش ثابتِ مخصوص همان پارامتر.

    برای هر پارامتر فقط یک بار (در نخستین باز شدن آن صفحه پس از هر اجرای پایش)
    تولید و سپس ذخیره می‌شود؛ بنابراین جابه‌جایی میان صفحه‌ها هزینه‌ای ندارد و
    با تغییر نتایج پایش یا متن پرسش، به‌طور خودکار بازتولید می‌شود.
    """
    if 'param_briefs' not in st.session_state:
        st.session_state.param_briefs = {}
    if 'param_brief_signatures' not in st.session_state:
        st.session_state.param_brief_signatures = {}

    prompt = PARAM_PAGE_SUMMARY_PROMPTS.get(parameter_type)
    if not prompt:
        return

    if not _guard_pending_expert():
        if st.session_state.param_briefs.get(parameter_type):
            _render_param_brief_card(parameter_type, st.session_state.param_briefs[parameter_type])
        return
    if not _ensure_expert_analysis():
        return

    signature = (_expert_results_signature(), prompt)
    is_stale = (
        not st.session_state.param_briefs.get(parameter_type)
        or st.session_state.param_brief_signatures.get(parameter_type) != signature
    )

    if is_stale:
        with st.spinner("در حال تهیه تحلیل کارشناسی این شاخص توسط متخصص هوش مصنوعی..."):
            try:
                brief = ask_water_quality_expert(
                    prompt,
                    st.session_state.expert_analysis_json,
                    []   # a standalone question: no chat history
                )
                st.session_state.param_briefs[parameter_type] = brief
                st.session_state.param_brief_signatures[parameter_type] = signature
                _persist_expert_snapshot()
            except Exception as e:
                st.warning(
                    "تحلیل کارشناسی این شاخص تهیه نشد (خطا در ارتباط با متخصص هوش مصنوعی): "
                    f"{e}"
                )
                return

    brief = st.session_state.param_briefs.get(parameter_type)
    if brief:
        _render_param_brief_card(parameter_type, brief)


def render_summary_page():
    """
    صفحه «خلاصه مدیریتی»: همان عامل متخصص و همان دستور سیستمی صفحه چت، اما با یک
    پرسش ثابت (EXECUTIVE_SUMMARY_PROMPT) که فقط یک بار اجرا و نتیجه‌اش ذخیره
    می‌شود. خروجی، یک خلاصه ۵ تا ۶ خطی و ساده برای مدیر است که هر سه شاخص را با
    هم می‌بیند.
    """
    from .interface import _render_active_section_badge
    _render_active_section_badge("🧭", "خلاصه مدیریتی کیفیت آب", "#14507A", "#4A9BD4")

    if 'executive_summary' not in st.session_state:
        st.session_state.executive_summary = None
    if 'executive_summary_signature' not in st.session_state:
        st.session_state.executive_summary_signature = None

    _render_expert_revisions()
    if not _guard_pending_expert():
        if st.session_state.executive_summary:
            _render_summary_card(st.session_state.executive_summary)
        return

    if not any(st.session_state.results.get(p) for p in ALL_PARAMETERS):
        st.info("برای مشاهده خلاصه مدیریتی، ابتدا پایش را اجرا کنید.")
        return

    if not _ensure_expert_analysis():
        return

    signature = _expert_results_signature()
    is_stale = (
        st.session_state.executive_summary is None
        or st.session_state.executive_summary_signature != signature
    )

    # Generated once per monitoring run and then cached, so simply switching
    # between pages never costs another call to the language model. It is
    # refreshed automatically whenever the monitoring results change.
    if is_stale:
        with st.spinner("در حال تهیه خلاصه مدیریتی توسط متخصص هوش مصنوعی..."):
            try:
                summary = ask_water_quality_expert(
                    EXECUTIVE_SUMMARY_PROMPT,
                    st.session_state.expert_analysis_json,
                    []   # a standalone question: no chat history
                )
                st.session_state.executive_summary = summary
                st.session_state.executive_summary_signature = signature
                _persist_expert_snapshot()
            except Exception as e:
                st.error(
                    "خلاصه مدیریتی تهیه نشد (خطا در ارتباط با متخصص هوش مصنوعی): "
                    f"{e}"
                )
                return

    if st.session_state.executive_summary:
        _render_summary_card(st.session_state.executive_summary)
        st.caption(
            "این خلاصه به‌طور خودکار از تحلیل آماری هر سه شاخص کدورت، کلروفیل و مواد آلی محلول "
            "تهیه شده است. برای پرسش‌های دقیق‌تر، از صفحه «چت بات» استفاده کنید."
        )


def render_expert_chat_tab():
    """
    صفحه «نظر متخصص آب»: به‌صورت خودکار خروجی اکسل پایش را می‌گیرد، پایپ‌لاین
    تحلیل آماری موجود را روی آن اجرا می‌کند، خلاصه JSON تولید می‌کند، و یک
    رابط گفتگو با یک عامل هوشمند (LangGraph ReAct agent) در اختیار کاربر
    قرار می‌دهد. این عامل علاوه بر خلاصه JSON، به سه ابزار نیز دسترسی دارد:
    شناسایی نام منطقه از روی مختصات (reverse geocoding با OpenWeatherMap،
    نتایج آن روی دیسک کش می‌شوند)، جست‌وجوی وب برای زمینه‌ی کلی اقلیمی
    (Tavily)، و دریافت داده‌های دقیق هواشناسی تاریخی (Open-Meteo، نتایج آن
    نیز روی دیسک کش می‌شوند) برای مختصات مرکز منطقه — این ابزار هواشناسی
    به‌طور خودکار در تحلیل علّی روندها و ناهنجاری‌ها به کار می‌رود، نه فقط در
    پاسخ به سؤال مستقیم درباره‌ی وضعیت هوا.
    """
    from .interface import _render_active_section_badge
    _inject_persian_chat_css()
    # Styles the two ready-made question buttons regardless of the Streamlit
    # version's DOM/class naming (see _force_quick_button_style). Invisible.
    _force_quick_button_style()

    _render_active_section_badge("💬", "چت با متخصص آب", "#E08E0B", "#F5A524")

    if 'expert_chat_history' not in st.session_state:
        st.session_state.expert_chat_history = []

    _render_expert_revisions()
    if not _guard_pending_expert():
        for msg in st.session_state.expert_chat_history:
            with st.chat_message(msg['role']):
                st.markdown(msg.get('display') or msg['content'])
        return

    if not any(st.session_state.results.get(p) for p in ALL_PARAMETERS):
        st.info("برای استفاده از این بخش، ابتدا پایش را اجرا کنید تا داده‌ای برای تحلیل وجود داشته باشد.")
        return

    # Same analysis JSON the «خلاصه مدیریتی» page uses
    if not _ensure_expert_analysis():
        return

    if st.button("🗑️ شروع گفتگوی جدید", key="expert_chat_reset"):
        from .expert_snapshot import dump_expert_state, load_expert_state
        candidate = load_expert_state(dump_expert_state(st.session_state))
        candidate['expert_chat_history'] = []
        if _persist_expert_candidate(candidate, archive_current=True):
            st.session_state.update(candidate)
            st.rerun()

    # ------------------------------------------------------------------
    # Ready-made questions. They go to exactly the same expert agent a typed
    # question goes to; only the wording is fixed (see QUICK_*_PROMPT).
    # ------------------------------------------------------------------
    st.markdown(
        '<div class="wq-quick-hint">می‌توانید سؤال خود را بنویسید، یا یکی از بررسی‌های آماده زیر را اجرا کنید:</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="wq-quick-anchor"></div>', unsafe_allow_html=True)

    quick_question = None
    quick_label = None

    qcol_left, qcol_right = st.columns(2)
    # Right-to-left: the quick review sits on the right, the deep one on the left
    if qcol_right.button("⚡ بررسی خلاصه و سریع", key="wqquick_brief",
                         use_container_width=True,
                         help="توضیح کوتاه و ساده از وضعیت کلی کیفیت آب، در ۳ تا ۴ خط"):
        quick_question = QUICK_BRIEF_PROMPT
        quick_label = "⚡ بررسی خلاصه و سریع"

    if qcol_left.button("🔬 بررسی جامع و دقیق", key="wqquick_deep",
                        use_container_width=True,
                        help="تحلیل کامل هر سه شاخص: روند، الگوی فصلی، ناهنجاری‌ها، همبستگی‌ها و توصیه‌ها"):
        quick_question = QUICK_DEEP_PROMPT
        quick_label = "🔬 بررسی جامع و دقیق"

    st.divider()

    for msg in st.session_state.expert_chat_history:
        with st.chat_message(msg["role"]):
            # A ready-made question is shown by its button title, while the full
            # prompt behind it stays in the history as the agent's context.
            st.markdown(msg.get("display") or msg["content"])

    typed_question = st.chat_input("سؤال خود را درباره کیفیت آب این منطقه بپرسید...")

    user_question = typed_question or quick_question
    display_text = None if typed_question else quick_label

    if user_question:
        st.session_state.expert_chat_history.append(
            {"role": "user", "content": user_question, "display": display_text}
        )
        if not _persist_expert_snapshot():
            st.write(display_text or user_question)
            st.info('پرسش ارسال نشد. پس از ذخیره موفق، می‌توانید آن را دوباره بپرسید.')
            return
        with st.chat_message("user"):
            st.markdown(display_text or user_question)

        with st.chat_message("assistant"):
            progress = st.empty()
            response = st.empty()
            failed = False
            with st.spinner("در حال بررسی توسط متخصص هوش مصنوعی..."):
                try:
                    answer = ask_water_quality_expert(
                        user_question,
                        st.session_state.expert_analysis_json,
                        st.session_state.expert_chat_history[:-1],
                        on_text=response.markdown,
                        on_progress=progress.caption,
                    )
                except Exception as e:
                    failed = True
                    answer = _expert_error_message(e)
                finally:
                    progress.empty()
                response.markdown(answer)

        st.session_state.expert_chat_history.append({"role": "assistant", "content": answer, "error": failed})
        _persist_expert_snapshot()

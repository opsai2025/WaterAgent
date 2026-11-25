"""Satellite functions for the monitoring application."""
import os
import rasterio
import ee
import requests
import time

from .config import (
    CLOUD_THRESHOLD,
    CLOUD_PROB_THRESHOLD,
    AWEI_THRESHOLD,
    NDSI_THRESHOLD,
    NIR_SNOW_THRESHOLD,
    GREEN_SNOW_THRESHOLD,
    PARAM_TURBIDITY,
    PARAM_CDOM,
    MAX_RETRIES,
    RETRY_DELAY_BASE,
    DOWNLOAD_TIMEOUT,
    CHUNK_SIZE,
    STATUS_COMPLETE,
    STATUS_FAILED,
)

def create_water_quality_collection(aoi, start_date, end_date, parameter_type, cloudy_pixel_percentage=CLOUD_THRESHOLD):
    """
    Create water quality collection for Turbidity, Chlorophyll or CDOM.

    Snow detection is used as a PREPROCESSING step to exclude snow/ice pixels
    from water detection. Snow mask is never downloaded or shown to the user.

    For TURBIDITY (NDTI):
    1. Link S2_SR with S2_CLOUD_PROBABILITY
    2. Apply cloud mask (probability < 15)
    3. Calculate NDSI for snow detection: (B3 - B11) / (B3 + B11)
    4. Create snow mask (MODIS-heritage water/snow test): NDSI > 0.42 AND B8 (NIR) > 0.11 AND B3 (Green) > 0.1
    5. Calculate AWEIsh for water body detection: B2 + 2.5*B3 - 1.5*(B8+B11) - 0.25*B12 > 0.05, excluding snow
    6. Calculate NDTI (turbidity index): (B4 - B3) / (B4 + B3)

    For CHLOROPHYLL:
    1. Link S2_SR with S2_CLOUD_PROBABILITY
    2. Apply cloud mask (probability < 15)
    3. Calculate NDSI for snow detection: (B3 - B11) / (B3 + B11)
    4. Create snow mask (MODIS-heritage water/snow test): NDSI > 0.42 AND B8 (NIR) > 0.11 AND B3 (Green) > 0.1
    5. Calculate AWEIsh for water body detection: B2 + 2.5*B3 - 1.5*(B8+B11) - 0.25*B12 > 0.05, excluding snow
    6. Calculate Chlorophyll Index (NDCI): (B5 - B4) / (B5 + B4)

    For CDOM (Colored Dissolved Organic Matter):
    1-5. Exactly the same preprocessing chain as above (cloud mask, snow mask,
         AWEIsh water body) so all three parameters are computed on the very
         same water pixels and are directly comparable.
    6. Calculate CDOM from the green/red band ratio:
           CDOM = 537 * exp(-2.93 * (B3 / B4))
       This is the Landsat-heritage band-ratio model of Brezonik, Menken &
       Bauer (2005), applied to the equivalent Sentinel-2 bands. Its output
       approximates the CDOM absorption coefficient at 440 nm (a440), in
       units of 1/m — higher values mean more dissolved organic ("humic")
       matter and darker, tea-coloured water.
    """
    s2_sr = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
             .filterBounds(aoi)
             .filterDate(start_date, end_date)
             .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloudy_pixel_percentage)))

    s2_cloud_prob = (ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY')
                     .filterBounds(aoi)
                     .filterDate(start_date, end_date))

    join_filter = ee.Filter.equals(leftField='system:index', rightField='system:index')
    joined = ee.Join.saveFirst('cloud_probability').apply(
        primary=s2_sr, secondary=s2_cloud_prob, condition=join_filter
    )

    def add_cloud_band(feature):
        img = ee.Image(feature)
        cloud_prob_img = ee.Image(img.get('cloud_probability'))
        return img.addBands(cloud_prob_img.select('probability'))

    s2_joined = ee.ImageCollection(joined.map(add_cloud_band))

    if parameter_type == PARAM_TURBIDITY:
        def calculate_turbidity(img):
            cloud = img.select('probability')
            cloud_free = cloud.lt(CLOUD_PROB_THRESHOLD)

            sr = img.select(['B2', 'B3', 'B4', 'B8', 'B11', 'B12']).multiply(0.0001)

            ndsi = sr.normalizedDifference(['B3', 'B11']).rename('ndsi')
            is_snow = (
                ndsi.gt(NDSI_THRESHOLD)                          # NDSI > 0.42 — spectral snow signature
                .And(sr.select('B8').gt(NIR_SNOW_THRESHOLD))     # NIR ~0.11 — excludes water, snow reflects strongly here
                .And(sr.select('B3').gt(GREEN_SNOW_THRESHOLD))   # Green ~0.1 — excludes dark shadow/non-snow surfaces
            )

            awei = sr.expression(
                'BLUE + 2.5 * GREEN - 1.5 * (NIR + SWIR1) - 0.25 * SWIR2',
                {
                    'BLUE': sr.select('B2'),
                    'GREEN': sr.select('B3'),
                    'NIR': sr.select('B8'),
                    'SWIR1': sr.select('B11'),
                    'SWIR2': sr.select('B12'),
                }
            ).rename('awei')
            water_body = awei.gt(AWEI_THRESHOLD).And(is_snow.Not())

            ndti = sr.normalizedDifference(['B4', 'B3']).rename('wq_index')

            wq_masked = ndti.updateMask(cloud_free).updateMask(water_body)

            rgb = sr.select(['B4', 'B3', 'B2'])

            combined = (wq_masked
                       .addBands(rgb)
                       .addBands(water_body.rename('water_mask')))

            return combined.clip(aoi).copyProperties(img, ['system:time_start'])

        return s2_joined.map(calculate_turbidity)

    elif parameter_type == PARAM_CDOM:
        def calculate_cdom(img):
            cloud = img.select('probability')
            cloud_free = cloud.lt(CLOUD_PROB_THRESHOLD)

            sr = img.select(['B2', 'B3', 'B4', 'B8', 'B11', 'B12']).multiply(0.0001)

            ndsi = sr.normalizedDifference(['B3', 'B11']).rename('ndsi')
            is_snow = (
                ndsi.gt(NDSI_THRESHOLD)                          # NDSI > 0.42 — spectral snow signature
                .And(sr.select('B8').gt(NIR_SNOW_THRESHOLD))     # NIR ~0.11 — excludes water, snow reflects strongly here
                .And(sr.select('B3').gt(GREEN_SNOW_THRESHOLD))   # Green ~0.1 — excludes dark shadow/non-snow surfaces
            )

            awei = sr.expression(
                'BLUE + 2.5 * GREEN - 1.5 * (NIR + SWIR1) - 0.25 * SWIR2',
                {
                    'BLUE': sr.select('B2'),
                    'GREEN': sr.select('B3'),
                    'NIR': sr.select('B8'),
                    'SWIR1': sr.select('B11'),
                    'SWIR2': sr.select('B12'),
                }
            ).rename('awei')
            water_body = awei.gt(AWEI_THRESHOLD).And(is_snow.Not())

            # CDOM from the green/red band ratio (Brezonik et al., 2005):
            #     CDOM = 537 * exp(-2.93 * (B3 / B4))
            # Red reflectance is the denominator, so pixels where it is zero or
            # negative (deep shadow, residual masking artefacts) are excluded
            # rather than producing an infinite ratio.
            red_ok = sr.select('B4').gt(0)

            cdom = sr.expression(
                '537 * exp(-2.93 * (B03 / B04))',
                {
                    'B03': sr.select('B3'),
                    'B04': sr.select('B4'),
                }
            ).rename('wq_index')

            wq_masked = (cdom
                         .updateMask(cloud_free)
                         .updateMask(water_body)
                         .updateMask(red_ok))

            rgb = sr.select(['B4', 'B3', 'B2'])

            combined = (wq_masked
                       .addBands(rgb)
                       .addBands(water_body.rename('water_mask')))

            return combined.clip(aoi).copyProperties(img, ['system:time_start'])

        return s2_joined.map(calculate_cdom)

    else:  # CHLOROPHYLL
        def calculate_chlorophyll(img):
            cloud = img.select('probability')
            cloud_free = cloud.lt(CLOUD_PROB_THRESHOLD)

            sr = img.select(['B1', 'B2', 'B3', 'B4', 'B5', 'B8', 'B11', 'B12']).multiply(0.0001)

            ndsi = sr.normalizedDifference(['B3', 'B11']).rename('ndsi')
            is_snow = (
                ndsi.gt(NDSI_THRESHOLD)                          # NDSI > 0.42 — spectral snow signature
                .And(sr.select('B8').gt(NIR_SNOW_THRESHOLD))     # NIR ~0.11 — excludes water, snow reflects strongly here
                .And(sr.select('B3').gt(GREEN_SNOW_THRESHOLD))   # Green ~0.1 — excludes dark shadow/non-snow surfaces
            )

            awei = sr.expression(
                'BLUE + 2.5 * GREEN - 1.5 * (NIR + SWIR1) - 0.25 * SWIR2',
                {
                    'BLUE': sr.select('B2'),
                    'GREEN': sr.select('B3'),
                    'NIR': sr.select('B8'),
                    'SWIR1': sr.select('B11'),
                    'SWIR2': sr.select('B12'),
                }
            ).rename('awei')
            water_body = awei.gt(AWEI_THRESHOLD).And(is_snow.Not())

            # NDCI (Normalized Difference Chlorophyll Index): (B5 - B4) / (B5 + B4)
            # https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/ndci/
            chl_index = sr.normalizedDifference(['B5', 'B4']).rename('wq_index')

            wq_masked = chl_index.updateMask(cloud_free).updateMask(water_body)

            rgb = sr.select(['B4', 'B3', 'B2'])

            combined = (wq_masked
                       .addBands(rgb)
                       .addBands(water_body.rename('water_mask')))

            return combined.clip(aoi).copyProperties(img, ['system:time_start'])

        return s2_joined.map(calculate_chlorophyll)


def get_monthly_composite(wq_collection, aoi, year, month):
    """Create monthly composite from water quality collection."""
    start = ee.Date.fromYMD(year, month, 1)
    end = start.advance(1, 'month')

    monthly = wq_collection.filterDate(start, end)
    count = monthly.size().getInfo()

    if count == 0:
        return None, 0, "No images"

    composite = monthly.median()

    stats = composite.select('wq_index').reduceRegion(
        reducer=ee.Reducer.mean().combine(
            ee.Reducer.count(), sharedInputs=True
        ).combine(
            ee.Reducer.minMax(), sharedInputs=True
        ),
        geometry=aoi,
        scale=10,
        maxPixels=1e13
    )

    return composite, count, stats


def download_band_with_retry(image, band, aoi, output_path, scale=10):
    """Download a single band with retry mechanism."""
    from .cache import _failure_message, validate_geotiff_file
    # --- Cache check FIRST, before any network call ---------------------------
    # A band already on disk must cost nothing. The old order asked Earth Engine
    # for the AOI bounds before looking at the cache, so every resume paid for a
    # round trip per band — and, with no connection, that call failed and the
    # month was discarded even though its files were sitting right there.
    if os.path.exists(output_path):
        is_valid, msg = validate_geotiff_file(output_path, expected_bands=1)
        if is_valid:
            return True, "cached"
        try:
            os.remove(output_path)
        except Exception:
            pass

    temp_path = output_path + '.tmp'
    if os.path.exists(temp_path):
        os.remove(temp_path)

    last_error = None

    for attempt in range(MAX_RETRIES):
        response = None
        try:
            # Bounds and URL generation are network calls too. Retry them with
            # the band, rather than failing permanently before the retry loop.
            region = aoi.bounds().getInfo()['coordinates']
            url = image.select(band).getDownloadURL({
                'scale': scale, 'region': region, 'format': 'GEO_TIFF', 'bands': [band]
            })

            from .earth_engine_network import earth_engine_proxy_kwargs
            response = requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT,
                                    **earth_engine_proxy_kwargs(url))

            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                if 'text/html' in content_type:
                    last_error = "GEE rate limit"
                    raise Exception(last_error)

                downloaded_size = 0
                started = time.monotonic()
                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if time.monotonic() - started > 180:
                            raise requests.exceptions.Timeout('Band transfer exceeded 180 seconds')
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)

                is_valid, msg = validate_geotiff_file(temp_path, expected_bands=1)
                if is_valid:
                    os.replace(temp_path, output_path)
                    return True, "success"
                else:
                    last_error = f"Validation failed: {msg}"
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise Exception(last_error)
            else:
                last_error = f"HTTP {response.status_code}"
                raise Exception(last_error)

        except requests.exceptions.Timeout:
            last_error = "Timeout"
        except requests.exceptions.ConnectionError:
            last_error = "Connection error"
        except Exception as e:
            last_error = _failure_message(e)
        finally:
            if response is not None:
                response.close()

        for f in [output_path, temp_path]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass

        if attempt < MAX_RETRIES - 1:
            wait_time = RETRY_DELAY_BASE ** (attempt + 1)
            time.sleep(wait_time)

    return False, last_error


def download_monthly_data(composite, aoi, temp_dir, month_name, param_short, scale=10):
    """
    Download monthly composite (Water Quality Index + RGB bands).
    Snow mask is NOT downloaded — used only server-side in GEE.
    No UI status is written here; progress is summarized at a higher level.
    """
    from .cache import validate_geotiff_file
    wq_path = os.path.join(temp_dir, f"wq_index_{param_short}_{month_name}.tif")
    rgb_path = os.path.join(temp_dir, f"rgb_{param_short}_{month_name}.tif")

    wq_valid, _ = validate_geotiff_file(wq_path, expected_bands=1)
    rgb_valid, _ = validate_geotiff_file(rgb_path, expected_bands=3)

    if wq_valid and rgb_valid:
        return wq_path, rgb_path, STATUS_COMPLETE, "Cached"

    try:
        success, msg = download_band_with_retry(composite, 'wq_index', aoi, wq_path, scale)
        if not success:
            return None, None, STATUS_FAILED, f"WQ Index download failed: {msg}"

        bands_dir = os.path.join(temp_dir, f"bands_{param_short}_{month_name}")
        os.makedirs(bands_dir, exist_ok=True)

        rgb_bands = ['B4', 'B3', 'B2']
        band_files = []

        for band in rgb_bands:
            band_file = os.path.join(bands_dir, f"{band}.tif")
            success, msg = download_band_with_retry(composite, band, aoi, band_file, scale)

            if not success:
                return None, None, STATUS_FAILED, f"RGB {band} download failed: {msg}"

            band_files.append(band_file)

        with rasterio.open(band_files[0]) as src:
            meta = src.meta.copy()
        meta.update(count=3)

        # Only a fully written, readable composite may occupy the final cache
        # path. Interrupted assembly leaves a disposable .tmp, never a cache hit.
        rgb_temp_path = rgb_path + '.tmp'
        try:
            with rasterio.open(rgb_temp_path, 'w', **meta) as dst:
                for i, band_file in enumerate(band_files):
                    with rasterio.open(band_file) as src:
                        dst.write(src.read(1), i+1)
            rgb_valid, rgb_message = validate_geotiff_file(rgb_temp_path, expected_bands=3)
            if not rgb_valid:
                raise ValueError(f"RGB composite validation failed: {rgb_message}")
            os.replace(rgb_temp_path, rgb_path)
        finally:
            if os.path.exists(rgb_temp_path):
                try:
                    os.remove(rgb_temp_path)
                except OSError:
                    pass

        return wq_path, rgb_path, STATUS_COMPLETE, "Downloaded"

    except Exception as e:
        return None, None, STATUS_FAILED, f"Error: {str(e)}"

"""One common Sentinel-2 preprocessing graph and one monthly raster transfer."""
import os
import time

import ee
import rasterio
import requests

from .config import (AWEI_THRESHOLD, CHUNK_SIZE, CLOUD_PROB_THRESHOLD, DOWNLOAD_TIMEOUT,
                     GREEN_SNOW_THRESHOLD, MAX_RETRIES, NDSI_THRESHOLD,
                     NIR_SNOW_THRESHOLD, RETRY_DELAY_BASE)

BANDS = ('turbidity', 'chlorophyll', 'cdom', 'B4', 'B3', 'B2')


def create_shared_collection(aoi, start_date, end_date, cloudy_pixel_percentage):
    """Compute each scene's indices before the monthly median, as before."""
    surface = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
               .filterBounds(aoi).filterDate(start_date, end_date)
               .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloudy_pixel_percentage)))
    cloud = (ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY')
             .filterBounds(aoi).filterDate(start_date, end_date))
    joined = ee.Join.saveFirst('cloud_probability').apply(
        primary=surface, secondary=cloud,
        condition=ee.Filter.equals(leftField='system:index', rightField='system:index'))

    def preprocess(feature):
        image = ee.Image(feature)
        cloud_free = ee.Image(image.get('cloud_probability')).select('probability').lt(CLOUD_PROB_THRESHOLD)
        sr = image.select(['B2', 'B3', 'B4', 'B5', 'B8', 'B11', 'B12']).multiply(0.0001)
        snow = (sr.normalizedDifference(['B3', 'B11']).gt(NDSI_THRESHOLD)
                .And(sr.select('B8').gt(NIR_SNOW_THRESHOLD))
                .And(sr.select('B3').gt(GREEN_SNOW_THRESHOLD)))
        water = sr.expression('BLUE + 2.5 * GREEN - 1.5 * (NIR + SWIR1) - 0.25 * SWIR2', {
            'BLUE': sr.select('B2'), 'GREEN': sr.select('B3'), 'NIR': sr.select('B8'),
            'SWIR1': sr.select('B11'), 'SWIR2': sr.select('B12'),
        }).gt(AWEI_THRESHOLD).And(snow.Not())
        ndti = sr.normalizedDifference(['B4', 'B3']).rename('turbidity').updateMask(cloud_free).updateMask(water)
        ndci = sr.normalizedDifference(['B5', 'B4']).rename('chlorophyll').updateMask(cloud_free).updateMask(water)
        cdom = (sr.expression('537 * exp(-2.93 * (B03 / B04))',
                             {'B03': sr.select('B3'), 'B04': sr.select('B4')})
                .rename('cdom').updateMask(cloud_free).updateMask(water).updateMask(sr.select('B4').gt(0)))
        return (ndti.addBands(ndci).addBands(cdom).addBands(sr.select(['B4', 'B3', 'B2']))
                .clip(aoi).copyProperties(image, ['system:time_start']))

    return ee.ImageCollection(joined.map(preprocess))


def monthly_shared_composite(collection, year, month):
    start = ee.Date.fromYMD(year, month, 1)
    images = collection.filterDate(start, start.advance(1, 'month'))
    count = images.size().getInfo()
    return (images.median() if count else None), count


def download_shared_month(composite, aoi, path, scale=10):
    """Transfer one six-band GeoTIFF; a completed transfer is reused on resume."""
    from .cache import _failure_message, validate_geotiff_file
    from .earth_engine_network import earth_engine_proxy_kwargs
    if validate_geotiff_file(path, expected_bands=6)[0]:
        return
    pending = path + '.tmp'
    last_error = 'Download failed'
    for attempt in range(MAX_RETRIES):
        response = None
        try:
            region = aoi.bounds().getInfo()['coordinates']
            image = composite.select(list(BANDS)).unmask(0).toFloat()
            url = image.getDownloadURL({'bands': list(BANDS), 'scale': scale,
                                        'region': region, 'format': 'GEO_TIFF'})
            response = requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT,
                                    **earth_engine_proxy_kwargs(url))
            response.raise_for_status()
            if 'text/html' in response.headers.get('content-type', ''):
                raise ValueError('Unexpected HTML response')
            started = time.monotonic()
            with open(pending, 'wb') as stream:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if time.monotonic() - started > 180:
                        raise requests.exceptions.Timeout('Transfer exceeded 180 seconds')
                    if chunk:
                        stream.write(chunk)
            if not validate_geotiff_file(pending, expected_bands=6)[0]:
                raise ValueError('Downloaded raster is invalid')
            os.replace(pending, path)
            return
        except Exception as exc:
            last_error = _failure_message(exc)
            # Earth Engine's synchronous endpoint has a fixed request-size
            # limit. Retrying the same oversized request cannot succeed.
            detail = str(exc).lower()
            if response is not None:
                try:
                    detail += ' ' + response.text[:4000].lower()
                except Exception:
                    pass
            if any(text in detail for text in ('total request size', 'request size exceeds',
                                                'must be less than or equal to 50331648',
                                                'must be less than or equal to 33554432')):
                raise ValueError('حجم تصویر مشترک از سقف دریافت مستقیم بیشتر است؛ محدوده کوچک‌تری انتخاب کنید.') from None
        finally:
            if response is not None:
                response.close()
            if os.path.exists(pending):
                os.remove(pending)
        if attempt + 1 < MAX_RETRIES:
            time.sleep(RETRY_DELAY_BASE ** (attempt + 1))
    raise ConnectionError(last_error)


def split_shared_month(source, folder, month):
    """Create compatible local per-index files from the shared transfer."""
    from .cache import validate_geotiff_file
    outputs = {}
    with rasterio.open(source) as src:
        if src.count != 6:
            raise ValueError('Expected six raster bands')
        for index, short in enumerate(('turbidity', 'chlorophyll', 'cdom'), 1):
            wq = os.path.join(folder, f'wq_index_{short}_{month}.tif')
            rgb = os.path.join(folder, f'rgb_{short}_{month}.tif')
            for target, band_numbers in ((wq, (index,)), (rgb, (4, 5, 6))):
                if validate_geotiff_file(target, expected_bands=len(band_numbers))[0]:
                    continue
                pending = target + '.tmp'
                try:
                    profile = src.profile.copy()
                    profile.update(count=len(band_numbers), driver='GTiff')
                    with rasterio.open(pending, 'w', **profile) as dst:
                        for out_band, source_band in enumerate(band_numbers, 1):
                            dst.write(src.read(source_band), out_band)
                    if not validate_geotiff_file(pending, expected_bands=len(band_numbers))[0]:
                        raise ValueError('Split raster is invalid')
                    os.replace(pending, target)
                finally:
                    if os.path.exists(pending):
                        os.remove(pending)
            outputs[short] = {'wq_index': wq, 'rgb': rgb}
    return outputs

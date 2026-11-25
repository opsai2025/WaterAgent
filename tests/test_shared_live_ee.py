"""Opt-in comparison against the previous per-index Earth Engine graphs."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


@unittest.skipUnless(os.environ.get('WATER_MONITOR_LIVE_EE_TEST') == '1',
                     'requires an explicitly enabled Earth Engine test')
class SharedLiveEarthEngineTests(unittest.TestCase):
    def test_shared_graph_matches_existing_indices_and_downloads_once(self):
        import ee
        from water_monitoring_app.cache import initialize_earth_engine
        from water_monitoring_app.config import ALL_PARAMETERS
        from water_monitoring_app.satellite import create_water_quality_collection, get_monthly_composite
        from water_monitoring_app.shared_satellite import (create_shared_collection, monthly_shared_composite,
                                                            download_shared_month, split_shared_month)
        ok, message = initialize_earth_engine()
        if not ok:
            lowered = message.lower()
            reason = ('network/timeout' if any(word in lowered for word in ('timeout', 'timed out', 'connection', 'proxy', 'network'))
                      else 'authentication/permission' if any(word in lowered for word in ('credential', 'authentication', 'permission', 'unauthorized'))
                      else 'other initialization error')
            self.fail('Earth Engine initialization was unsuccessful: ' + reason)
        try:
            aoi = ee.Geometry.Rectangle([51.0, 37.0, 51.006, 37.006])
            start, end = '2023-06-01', '2023-07-01'
            collection = create_shared_collection(aoi, start, end, 100)
            shared, count = monthly_shared_composite(collection, 2023, 6)
            if count == 0:
                self.fail('No input scenes in the selected test month')
            for parameter, short in zip(ALL_PARAMETERS, ('turbidity', 'chlorophyll', 'cdom')):
                previous = create_water_quality_collection(aoi, start, end, parameter, 100)
                old, old_count, _ = get_monthly_composite(previous, aoi, 2023, 6)
                self.assertEqual(old_count, count)
                difference = old.select('wq_index').subtract(shared.select(short)).abs().reduceRegion(
                    reducer=ee.Reducer.max(), geometry=aoi, scale=10, maxPixels=1000000).getInfo()
                values = [value for value in difference.values() if value is not None]
                self.assertTrue(values, 'No valid water pixels available for scientific comparison')
                self.assertLess(max(values), 1e-5)
            with TemporaryDirectory() as directory:
                target = str(Path(directory) / 'shared.tif')
                download_shared_month(shared, aoi, target)
                self.assertEqual(len(split_shared_month(target, directory, '2023-06')), 3)
        except AssertionError:
            raise
        except Exception:
            # Never surface connection strings, signed URLs or credentials.
            self.fail('Earth Engine comparison or download was unsuccessful')

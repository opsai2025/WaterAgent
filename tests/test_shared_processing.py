"""Shared monthly download, restart, counts and backwards-compatible rasters."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from water_monitoring_app import cache, shared_processing, shared_satellite
from water_monitoring_app.config import ALL_PARAMETERS, STATUS_COMPLETE, STATUS_FAILED, STATUS_NO_DATA
from water_monitoring_app.earth_engine_errors import EarthEngineInitializationError


class State(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


class Placeholder:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def raster_bytes():
    profile = {'driver': 'GTiff', 'width': 64, 'height': 64, 'count': 6,
               'dtype': 'float32', 'crs': 'EPSG:4326',
               'transform': from_origin(51, 35, .01, .01)}
    with MemoryFile() as memory:
        with memory.open(**profile) as dst:
            for band in range(1, 7):
                data = np.full((64, 64), band / 10, dtype='float32')
                if band == 3:
                    data[0, 0] = 0  # CDOM invalid at one pixel; other indices remain valid.
                dst.write(data, band)
        return memory.read()


class SharedProcessingTests(unittest.TestCase):
    def test_new_and_legacy_pipeline_cache_keys_are_distinct(self):
        with TemporaryDirectory() as folder:
            config = {'start_date': '2023-01-01', 'end_date': '2023-02-01',
                      'polygon_coords': [[51, 35], [51.1, 35], [51, 35.1], [51, 35]]}
            state = {'auth_user_id': '66ded0d3-96d8-4d53-aa64-4232515178c4'}
            with patch.object(cache, 'st', SimpleNamespace(session_state=state)), \
                    patch.object(cache, '_cache_root', return_value=folder):
                legacy = cache.get_cache_dir_for_config(config)
                shared = cache.get_cache_dir_for_config(dict(config, pipeline_version='shared_v1'))
            self.assertNotEqual(legacy, shared)

    def test_execution_dispatches_new_runs_to_shared_pipeline(self):
        config = {'pipeline_version': 'shared_v1', 'start_date': '2023-01-01',
                  'end_date': '2023-02-01'}
        state = State(processing_config=config, processing_in_progress=False)
        ui = SimpleNamespace(session_state=state, error=Mock(), warning=Mock())
        with patch.object(cache, 'st', ui), \
                patch.object(shared_processing, 'run_shared_analysis', return_value=True) as shared, \
                patch('water_monitoring_app.processing.run_full_analysis') as legacy:
            cache._execute_monitoring_run(config, resume=False)
        shared.assert_called_once()
        legacy.assert_not_called()
        self.assertTrue(state.processing_complete)

    def test_one_scene_graph_applies_common_masks_and_three_formulas(self):
        surface, cloud_collection = MagicMock(), MagicMock()
        surface.filterBounds.return_value.filterDate.return_value.filter.return_value = surface
        cloud_collection.filterBounds.return_value.filterDate.return_value = cloud_collection
        source, cloud_image, sr = MagicMock(), MagicMock(), MagicMock()
        source.get.return_value = 'cloud-asset'
        source.select.return_value.multiply.return_value = sr
        sr.select.return_value = MagicMock()
        source.copyProperties.return_value = source
        joined = MagicMock()
        joined.map.side_effect = lambda func: [func('scene')]
        fake = MagicMock()
        fake.ImageCollection.side_effect = [surface, cloud_collection, 'shared-collection']
        fake.Join.saveFirst.return_value.apply.return_value = joined
        fake.Image.side_effect = lambda item: cloud_image if item == 'cloud-asset' else source
        with patch.object(shared_satellite, 'ee', fake):
            result = shared_satellite.create_shared_collection('aoi', '2023-01-01', '2023-02-01', 10)
        self.assertEqual(result, 'shared-collection')
        joined.map.assert_called_once()
        self.assertEqual([call.args[0] for call in sr.normalizedDifference.call_args_list],
                         [['B3', 'B11'], ['B4', 'B3'], ['B5', 'B4']])
        expressions = [call.args[0] for call in sr.expression.call_args_list]
        self.assertIn('BLUE + 2.5 * GREEN - 1.5 * (NIR + SWIR1) - 0.25 * SWIR2', expressions)
        self.assertIn('537 * exp(-2.93 * (B03 / B04))', expressions)

    def test_single_multiband_transfer_and_split(self):
        with TemporaryDirectory() as folder:
            path = str(Path(folder) / 'shared.tif')
            image = Mock()
            image.select.return_value.unmask.return_value.toFloat.return_value.getDownloadURL.return_value = 'https://example.invalid/image'
            aoi = Mock()
            aoi.bounds.return_value.getInfo.return_value = {'coordinates': []}
            response = Mock(headers={'content-type': 'image/tiff'})
            response.iter_content.return_value = [raster_bytes()]
            proxies = {'http': 'http://proxy.invalid:8080', 'https': 'http://proxy.invalid:8080'}
            with patch('water_monitoring_app.earth_engine_network.earth_engine_proxy_kwargs',
                       return_value={'proxies': proxies}), \
                    patch.object(shared_satellite.requests, 'get', return_value=response) as get:
                shared_satellite.download_shared_month(image, aoi, path)
                shared_satellite.download_shared_month(image, aoi, path)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(get.call_args.kwargs['proxies'], proxies)
            image.select.return_value.unmask.return_value.toFloat.return_value.getDownloadURL.assert_called_once()
            paths = shared_satellite.split_shared_month(path, folder, '2023-01')
            for i, short in enumerate(('turbidity', 'chlorophyll', 'cdom'), 1):
                with rasterio.open(paths[short]['wq_index']) as src:
                    self.assertAlmostEqual(float(src.read(1)[0, 1]), i / 10, places=6)
                with rasterio.open(paths[short]['rgb']) as src:
                    self.assertEqual(src.count, 3)
                    self.assertAlmostEqual(float(src.read(1)[0, 0]), .4, places=6)

    def test_oversized_request_reports_limit_without_repeating_transfer(self):
        with TemporaryDirectory() as folder:
            image = Mock()
            image.select.return_value.unmask.return_value.toFloat.return_value.getDownloadURL.return_value = 'https://example.invalid/image'
            aoi = Mock()
            aoi.bounds.return_value.getInfo.return_value = {'coordinates': []}
            response = Mock(text='Total request size exceeds the limit')
            response.raise_for_status.side_effect = ValueError('request failed')
            with patch('water_monitoring_app.earth_engine_network.earth_engine_proxy_kwargs', return_value={}), \
                    patch.object(shared_satellite.requests, 'get', return_value=response) as get:
                with self.assertRaisesRegex(ValueError, 'محدوده کوچک'):
                    shared_satellite.download_shared_month(image, aoi, str(Path(folder) / 'shared.tif'))
            self.assertEqual(get.call_count, 1)

    def test_shared_months_and_resume_do_not_redownload(self):
        with TemporaryDirectory() as folder:
            config = {'start_date': '2023-01-01', 'end_date': '2023-03-01',
                      'polygon_coords': [[51, 35], [51.1, 35], [51, 35.1], [51, 35]],
                      'cloudy_pixel_percentage': 10, 'scale': 10, 'pipeline_version': 'shared_v1'}
            state = State(processing_config=config, download_summary={}, current_temp_dir=None)
            ui = SimpleNamespace(session_state=state, progress=lambda value: Placeholder(),
                                 empty=lambda: Placeholder())
            def write_download(composite, aoi, path, scale):
                Path(path).write_bytes(raster_bytes())
            with patch.object(shared_processing, 'st', ui), \
                    patch.object(cache, '_cache_dir_for_active_run', return_value=folder), \
                    patch.object(cache, 'prune_old_cache_dirs'), \
                    patch.object(shared_satellite, 'create_shared_collection', return_value=object()) as create, \
                    patch.object(shared_satellite, 'monthly_shared_composite',
                                 side_effect=[(object(), 3), (None, 0)]) as monthly, \
                    patch.object(shared_satellite, 'download_shared_month', side_effect=write_download) as download:
                self.assertTrue(shared_processing.run_shared_analysis(object(), config))
                self.assertEqual(create.call_count, 1)
                self.assertEqual(monthly.call_count, 2)
                self.assertEqual(download.call_count, 1)
                self.assertEqual(state.shared_scene_counts, {'2023-01': 3, '2023-02': 0})
                for parameter in ALL_PARAMETERS:
                    self.assertEqual(state.month_statuses[parameter]['2023-01']['status'], STATUS_COMPLETE)
                    self.assertEqual(state.month_statuses[parameter]['2023-02']['status'], STATUS_NO_DATA)
                    self.assertEqual(len(state.results[parameter]), 1)
                self.assertEqual(state.results[ALL_PARAMETERS[0]][0]['valid_pixels'], 4096)
                self.assertEqual(state.results[ALL_PARAMETERS[2]][0]['valid_pixels'], 4095)
                self.assertFalse(state.resume_after_interruption)
                create.reset_mock()
                monthly.reset_mock()
                download.reset_mock()
                self.assertTrue(shared_processing.run_shared_analysis(object(), config))
                create.assert_not_called()
                monthly.assert_not_called()
                download.assert_not_called()

    def test_interruption_after_transfer_resumes_without_another_download(self):
        with TemporaryDirectory() as folder:
            config = {'start_date': '2023-01-01', 'end_date': '2023-02-01',
                      'polygon_coords': [[51, 35], [51.1, 35], [51, 35.1], [51, 35]],
                      'cloudy_pixel_percentage': 10, 'scale': 10, 'pipeline_version': 'shared_v1'}
            state = State(processing_config=config, download_summary={}, current_temp_dir=None)
            ui = SimpleNamespace(session_state=state, progress=lambda value: Placeholder(),
                                 empty=lambda: Placeholder())
            original_split = shared_satellite.split_shared_month
            def write_download(composite, aoi, path, scale):
                Path(path).write_bytes(raster_bytes())
            with patch.object(shared_processing, 'st', ui), \
                    patch.object(cache, '_cache_dir_for_active_run', return_value=folder), \
                    patch.object(cache, 'prune_old_cache_dirs'), \
                    patch.object(shared_satellite, 'create_shared_collection', return_value=object()) as create, \
                    patch.object(shared_satellite, 'monthly_shared_composite', return_value=(object(), 2)) as monthly, \
                    patch.object(shared_satellite, 'download_shared_month', side_effect=write_download) as download, \
                    patch.object(shared_satellite, 'split_shared_month',
                                 side_effect=ValueError('interrupted')) as split:
                self.assertFalse(shared_processing.run_shared_analysis(object(), config))
                self.assertTrue(state.resume_after_interruption)
                split.side_effect = original_split
                create.reset_mock()
                monthly.reset_mock()
                download.reset_mock()
                self.assertTrue(shared_processing.run_shared_analysis(object(), config))
                self.assertFalse(state.resume_after_interruption)
                create.assert_not_called()
                monthly.assert_not_called()
                download.assert_not_called()
                # Derived outputs remain sufficient if the shared source is lost.
                Path(folder, 'shared_month_2023-01.tif').unlink()
                self.assertTrue(shared_processing.run_shared_analysis(object(), config))
                monthly.assert_not_called()
                download.assert_not_called()

    def test_early_network_failure_keeps_later_completed_month_visible(self):
        with TemporaryDirectory() as folder:
            config = {'start_date': '2023-01-01', 'end_date': '2023-04-01',
                      'polygon_coords': [[51, 35], [51.1, 35], [51, 35.1], [51, 35]],
                      'cloudy_pixel_percentage': 10, 'scale': 10, 'pipeline_version': 'shared_v1'}
            source = Path(folder, 'shared_month_2023-03.tif')
            source.write_bytes(raster_bytes())
            shared_satellite.split_shared_month(str(source), folder, '2023-03')
            shared_processing._save_manifest(folder, {'version': 1, 'months': {'2023-03': {'scene_count': 2}}})
            for parameter in ALL_PARAMETERS:
                cache._save_month_checkpoint(folder, parameter,
                                             {'2023-03': {'status': STATUS_COMPLETE, 'message': 'saved'}})
            state = State(processing_config=config, download_summary={}, current_temp_dir=None)
            ui = SimpleNamespace(session_state=state, progress=lambda value: Placeholder(),
                                 empty=lambda: Placeholder())
            with patch.object(shared_processing, 'st', ui), \
                    patch.object(cache, '_cache_dir_for_active_run', return_value=folder), \
                    patch.object(cache, 'prune_old_cache_dirs'), \
                    patch.object(shared_satellite, 'create_shared_collection', side_effect=ConnectionError('offline')):
                self.assertTrue(shared_processing.run_shared_analysis(object(), config))
            self.assertTrue(state.resume_after_interruption)
            for parameter in ALL_PARAMETERS:
                self.assertEqual(state.results[parameter][0]['month_name'], '2023-03')

    def test_initialization_failure_stops_once_and_retry_reuses_all_saved_months(self):
        with TemporaryDirectory() as folder:
            config = {'start_date': '2023-01-01', 'end_date': '2023-05-01',
                      'polygon_coords': [[51, 35], [51.1, 35], [51, 35.1], [51, 35]],
                      'cloudy_pixel_percentage': 10, 'scale': 10, 'pipeline_version': 'shared_v1'}
            manifest = {'version': 1, 'months': {}}
            originals = {}
            for month in ('2023-01', '2023-03'):
                source = Path(folder, f'shared_month_{month}.tif')
                source.write_bytes(raster_bytes())
                originals[month] = source.read_bytes()
                shared_satellite.split_shared_month(str(source), folder, month)
                manifest['months'][month] = {'scene_count': 2}
            shared_processing._save_manifest(folder, manifest)
            for parameter in ALL_PARAMETERS:
                cache._save_month_checkpoint(folder, parameter, {
                    month: {'status': STATUS_COMPLETE, 'message': 'saved'} for month in originals
                })
            state = State(processing_config=config, download_summary={}, current_temp_dir=None)
            ui = SimpleNamespace(session_state=state, progress=lambda value: Placeholder(),
                                 empty=lambda: Placeholder())
            resolve = Mock(side_effect=EarthEngineInitializationError('راه‌اندازی ناموفق'))

            def write_download(composite, aoi, path, scale):
                Path(path).write_bytes(raster_bytes())

            with patch.object(shared_processing, 'st', ui), \
                    patch.object(cache, '_cache_dir_for_active_run', return_value=folder), \
                    patch.object(cache, 'prune_old_cache_dirs'), \
                    patch.object(shared_satellite, 'create_shared_collection', return_value=object()) as create, \
                    patch.object(shared_satellite, 'monthly_shared_composite', return_value=(object(), 2)) as monthly, \
                    patch.object(shared_satellite, 'download_shared_month', side_effect=write_download) as download:
                self.assertTrue(shared_processing.run_shared_analysis(resolve, config))
                resolve.assert_called_once()
                create.assert_not_called()
                monthly.assert_not_called()
                download.assert_not_called()
                self.assertTrue(state.connection_interrupted)
                self.assertTrue(state.resume_after_interruption)
                for parameter in ALL_PARAMETERS:
                    self.assertEqual(state.month_statuses[parameter]['2023-02']['status'], STATUS_FAILED)
                    self.assertNotIn('2023-04', state.month_statuses[parameter])
                    self.assertEqual([item['month_name'] for item in state.results[parameter]],
                                     ['2023-01', '2023-03'])

                resolve.side_effect = None
                resolve.return_value = object()
                resolve.reset_mock()
                self.assertTrue(shared_processing.run_shared_analysis(resolve, config))
                resolve.assert_called_once()
                self.assertFalse(state.resume_after_interruption)
                self.assertEqual(monthly.call_count, 2)
                self.assertEqual(download.call_count, 2)
                self.assertEqual({Path(call.args[2]).name for call in download.call_args_list},
                                 {'shared_month_2023-02.tif', 'shared_month_2023-04.tif'})
                for month, original in originals.items():
                    self.assertEqual(Path(folder, f'shared_month_{month}.tif').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()

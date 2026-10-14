import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_input_products_http as http_fixture


class StationNetworkHttpTests(unittest.TestCase):
    setUp = http_fixture.InputProductsHttpTests.setUp
    tearDown = http_fixture.InputProductsHttpTests.tearDown
    request = http_fixture.InputProductsHttpTests.request

    def test_network_roundtrip_validation_and_source_listing(self):
        path = 'station-network'
        body = {'stations': [{'id': 'custom-rain', 'lon': 119., 'lat': 30.}], 'flowStation': 'custom-flow'}
        code, saved, _ = self.request(path, 'POST', body)
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved, body)
        self.assertEqual(self.request(path)[1], body)
        invalid = copy.deepcopy(body)
        invalid['stations'][0]['id'] = "1' OR 1=1"
        self.assertEqual(self.request(path, 'POST', invalid)[0], 400)
        self.assertEqual(self.request(path)[1], body)
        self.assertEqual(self.request('database/sources')[0], 200)

    def test_network_is_in_workspace_backup(self):
        from station_network import station_network
        from workspace_backup import create_backup, restore_backup
        from input_products import InputProducts
        body = {'stations': [{'id': 'rain', 'lon': 119., 'lat': 30.}], 'flowStation': 'flow'}
        station_network(self.products, self.products.resolve(), body)
        backup = create_backup(self.fixture.runtime)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'restored'
            restore_backup(backup['path'], target)
            restored = InputProducts(self.products.model_root, target)
            self.assertEqual(station_network(restored, restored.resolve()), body)

    def test_registered_rain_network_checks_station_and_flow_before_saving(self):
        from urllib.parse import urlencode
        code, source, _ = self.request('input-sources', 'POST', self.fixture.source_payload())
        self.assertEqual(code, 201)
        path = 'station-network?' + urlencode({'sourceId': source['sourceId']})
        code, network, _ = self.request(path)
        self.assertEqual(code, 200)
        invalid = copy.deepcopy(network)
        invalid['stations'][0]['id'] = 'unknown'
        self.assertEqual(self.request(path, 'POST', invalid)[0], 400)
        invalid = copy.deepcopy(network)
        invalid['flowStation'] = 'other-flow'
        self.assertEqual(self.request(path, 'POST', invalid)[0], 400)
        network['stations'][0]['lon'] += .01
        self.assertEqual(self.request(path, 'POST', network)[0], 200)
        self.assertEqual(self.request(path)[1], network)


if __name__ == '__main__': unittest.main()

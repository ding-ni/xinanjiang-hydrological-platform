"""Small deterministic fixtures for grid slope and registered-weight contracts."""
import csv
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grid_routing import build_grid_routing


class GridRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.bundle = {'bundleVersionId': 'TEST', 'manifestSHA256': '0' * 64,
                       'paths': {key: root / key for key in ('grids', 'geometry', 'mapping', 'topology')}}
        # B has the largest drop, but C has the steeper slope after diagonal distance.
        self.grids = [dict(unit_id=name, source_row=row, source_col=col, elevation_m=z, area_km2=1)
                      for name, row, col, z in [('A', 0, 0, 100), ('B', 1, 1, 86), ('C', 0, 1, 89)]]
        self.geometry = {'features': [
            {'properties': {'row': row['source_row'], 'col': row['source_col']},
             'geometry': {'type': 'Polygon', 'coordinates': [[[119 + index * .01, 30],
                 [119.005 + index * .01, 30], [119.005 + index * .01, 30.005],
                 [119 + index * .01, 30]]]}}
            for index, row in enumerate(self.grids)]}
        self.topology = {'reaches': [{'reach_id': 'R', 'downstream_reach_id': None}],
                         'units': [{'unit_id': 'U', 'reach_id': 'R'}]}
        self.mapping = [dict(grid_id=row['unit_id'], reach_id='R', unit_id='U',
                             source_fraction=1, adjusted_overlap_area_km2=1, mapping_method='AREA_OVERLAP')
                        for row in self.grids]

    def build(self):
        for name, rows in [('grids', self.grids), ('mapping', self.mapping)]:
            with self.bundle['paths'][name].open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        for name, value in [('geometry', self.geometry), ('topology', self.topology)]:
            self.bundle['paths'][name].write_text(json.dumps(value), encoding='utf-8')
        return build_grid_routing(self.bundle)

    def test_diagonal_distance_steepest_descent_and_no_cycles(self):
        rows = {row['id']: row for row in self.build()['grids']}
        self.assertEqual(rows['A']['downstreamGridId'], 'C')
        self.assertEqual(rows['C']['downstreamGridId'], 'B')
        self.assertIsNone(rows['B']['downstreamGridId'])
        for start in rows:
            visited = set()
            current = start
            while rows[current]['downstreamGridId']:
                self.assertNotIn(current, visited)
                visited.add(current)
                following = rows[current]['downstreamGridId']
                self.assertLess(rows[following]['elevationM'], rows[current]['elevationM'])
                current = following

    def test_equal_heights_are_terminals(self):
        for row in self.grids:
            row['elevation_m'] = 100
        product = self.build()
        self.assertEqual(product['summary']['d8EdgeCount'], 0)
        self.assertEqual(product['summary']['localTerminalCount'], 3)

    def test_source_hashes_and_weights_are_retained(self):
        self.mapping[0]['mapping_method'] = 'NEAREST_FALLBACK'
        product = self.build()
        self.assertEqual(product['summary']['fallbackGridCount'], 1)
        self.assertEqual(product['source']['bundleVersionId'], 'TEST')
        self.assertTrue(all(len(value) == 64 for value in product['source']['files'].values()))
        self.assertEqual(product['grids'][0]['mapping']['links'][0]['fraction'], 1)

    def test_nonfinite_or_nonpositive_grid_values(self):
        for field, value in [('elevation_m', math.nan), ('elevation_m', math.inf), ('area_km2', 0), ('area_km2', -1)]:
            with self.subTest(field=field, value=value):
                original = self.grids[0][field]
                self.grids[0][field] = value
                with self.assertRaises(ValueError):
                    self.build()
                self.grids[0][field] = original

    def test_geometry_must_be_closed_and_match_cells(self):
        self.geometry['features'][0]['geometry']['coordinates'][0].pop()
        with self.assertRaisesRegex(ValueError, '边界不闭合'):
            self.build()
        ring = self.geometry['features'][0]['geometry']['coordinates'][0]
        ring.append(list(ring[0]))
        self.geometry['features'].pop()
        with self.assertRaisesRegex(ValueError, '清单不一致'):
            self.build()

    def test_unknown_mapping_references_rejected(self):
        for field in ('grid_id', 'reach_id', 'unit_id'):
            with self.subTest(field=field):
                original = self.mapping[0][field]
                self.mapping[0][field] = 'UNKNOWN'
                with self.assertRaisesRegex(ValueError, '未登记'):
                    self.build()
                self.mapping[0][field] = original

    def test_weights_must_be_finite_and_close_to_one(self):
        for value in [0.5, -1, 1.1, math.nan]:
            self.mapping[0]['source_fraction'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.build()

    def test_mapping_reach_must_match_unit_and_ids_are_unique(self):
        self.topology['reaches'].append({'reach_id': 'R2'})
        self.mapping[0]['reach_id'] = 'R2'
        with self.assertRaisesRegex(ValueError, '所属河段不一致'):
            self.build()
        self.topology['units'].append(dict(self.topology['units'][0]))
        with self.assertRaisesRegex(ValueError, '编号重复'):
            self.build()


if __name__ == '__main__':
    unittest.main()

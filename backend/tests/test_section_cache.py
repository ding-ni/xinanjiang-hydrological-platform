"""Terrain reuse preserves geometry, topology and registered coefficients."""
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import MODEL
sys.path.insert(0,str(MODEL/'code'))
import numpy as np
import section_cache
from xaj_routing.point_query import PointFlowQuery
from xaj_routing.raster_formats import cell_center_xy


class TerrainCacheTests(unittest.TestCase):
    def test_cached_engine_preserves_every_terrain_array_and_point_location(self):
        with tempfile.TemporaryDirectory() as temporary,patch('section_cache.RUNTIME',Path(temporary)):
            root=Path(temporary)
            dem=root/'dem.asc'
            rows=np.fromfunction(lambda i,j:1000-i*10-j,(12,12))
            dem.write_text('ncols 12\nnrows 12\nxllcorner 450000\nyllcorner 3000000\ncellsize 30\nNODATA_value -9999\n'+'\n'.join(' '.join(map(str,row)) for row in rows),encoding='utf-8')
            paths={'dem':dem,'output':root,'control':root/'absent-control.json'}
            config={'stream_threshold_km2':0.002,'default_channel_width_m':17}
            engine=PointFlowQuery(MODEL,paths,config)
            section_cache.save_engine(engine,'roundtrip')
            restored=section_cache.load_engine(PointFlowQuery,MODEL,paths,{**config,'default_channel_width_m':23},'roundtrip')
            self.assertEqual(restored.default_width_m,23)
            self.assertEqual(restored.assignments,engine.assignments)
            self.assertEqual(restored.stream_cells,engine.stream_cells)
            for name in ('base_topology_result','topology_result'):
                before,after=getattr(engine,name),getattr(restored,name)
                for field in fields(before):
                    if isinstance(getattr(before,field.name),np.ndarray):
                        np.testing.assert_array_equal(getattr(before,field.name),getattr(after,field.name))
                    else:self.assertEqual(getattr(before,field.name),getattr(after,field.name))
            for cell in engine.stream_cells:
                lon,lat=engine.inverse_transformer.transform(*cell_center_xy(engine.grid,*cell))
                self.assertEqual(restored._point_cell(lon,lat),engine._point_cell(lon,lat))
                self.assertEqual(restored._nearest_stream(cell),engine._nearest_stream(cell))
            archive=root/'section-cache/terrain/roundtrip/terrain.npz'
            with archive.open('ab') as output:output.write(b'changed')
            self.assertIsNone(section_cache.load_engine(PointFlowQuery,MODEL,paths,config,'roundtrip'))


if __name__=='__main__':unittest.main()

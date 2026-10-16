"""Reusable terrain preparation using JSON and non-pickled NumPy arrays."""
import hashlib
import json
import os
import uuid
from dataclasses import fields
from pathlib import Path
from common import RUNTIME, read_json, write_json


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def terrain_key(paths, model):
    code=sorted((model/'code/xaj_routing').glob('*.py'))
    code += [Path(__file__),Path(__file__).with_name('routing_adapter.py')]
    return identity({str(path):sha(path) for path in [paths['dem'],paths['control'],paths['topology'],*code]})


def query_implementation():
    from science_context import model_implementation_identity
    return identity({'diagnostic':sha(Path(__file__).with_name('diagnostic_jobs.py')),
                     'cache':sha(Path(__file__)),
                     'model':model_implementation_identity()['modelImplementationSHA256']})


def load_engine(engine_class, model, paths, config, key):
    import numpy as np
    from pyproj import Transformer
    from xaj_routing.spatial import GridTopologyResult, ReachSegment
    from xaj_routing.raster_formats import AsciiGrid
    folder=RUNTIME/'section-cache/terrain'/key
    if not (folder/'manifest.json').is_file():
        return None
    metadata=read_json(folder/'manifest.json')
    if metadata.get('key')!=key or sha(folder/'terrain.npz')!=metadata.get('sha256'):
        return None
    with np.load(folder/'terrain.npz',allow_pickle=False) as saved:
        arrays={name:saved[name].copy() for name in saved.files}
    engine=engine_class.__new__(engine_class)
    engine.project_root=model.resolve();engine.paths=paths;engine.config=config
    engine.grid=AsciiGrid(elevation=arrays['elevation'],**metadata['grid'])
    engine.mask=arrays['mask']
    for prefix in ('base','current'):
        row=metadata[prefix]
        reaches=[ReachSegment(reach_id=reach['reach_id'],start_cell=tuple(reach['start_cell']),
            end_cell=tuple(reach['end_cell']),cells=tuple(tuple(cell) for cell in reach['cells']),
            downstream_reach_id=reach['downstream_reach_id'],length_km=reach['length_km']) for reach in row['reaches']]
        values={field.name:arrays[prefix+'_'+field.name] for field in fields(GridTopologyResult) if prefix+'_'+field.name in arrays}
        topology=GridTopologyResult(**{**row,**values,'reaches':reaches})
        setattr(engine,'base_topology_result' if prefix=='base' else 'topology_result',topology)
    engine.control_points=metadata['controlPoints']
    engine.assignments={tuple(cell):value for cell,value in zip(arrays['assignmentCells'].tolist(),metadata['assignmentValues'])}
    engine.reach_by_id={reach.reach_id:reach for reach in engine.topology_result.reaches}
    engine.stream_cells=[cell for reach in engine.topology_result.reaches for cell in reach.cells]
    engine.transformer=Transformer.from_crs('EPSG:4326','EPSG:32650',always_xy=True)
    engine.inverse_transformer=Transformer.from_crs('EPSG:32650','EPSG:4326',always_xy=True)
    engine.default_width_m=float(config.get('default_channel_width_m',20))
    engine.default_velocity_kmh=float(config.get('default_channel_velocity_kmh',3))
    return engine


def save_engine(engine,key):
    import numpy as np
    folder=RUNTIME/'section-cache/terrain'/key
    folder.mkdir(parents=True,exist_ok=True)
    arrays={'elevation':engine.grid.elevation,'mask':engine.mask,'assignmentCells':np.asarray(list(engine.assignments))}
    metadata={'key':key,'grid':{field.name:getattr(engine.grid,field.name) for field in fields(engine.grid) if field.name!='elevation'},
              'controlPoints':engine.control_points,'assignmentValues':list(engine.assignments.values())}
    for prefix,topology in [('base',engine.base_topology_result),('current',engine.topology_result)]:
        values={}
        for field in fields(topology):
            value=getattr(topology,field.name)
            if isinstance(value,np.ndarray):
                arrays[prefix+'_'+field.name]=value
            elif field.name=='reaches':
                values[field.name]=[{**reach.to_mapping(),'cells':reach.cells} for reach in value]
                for row in values[field.name]:row.pop('cell_count',None)
            else:values[field.name]=value
        metadata[prefix]=values
    pending=folder/('terrain-'+uuid.uuid4().hex+'.npz')
    np.savez_compressed(pending,**arrays)
    os.replace(pending,folder/'terrain.npz')
    metadata['sha256']=sha(folder/'terrain.npz')
    write_json(folder/'manifest.json',metadata)

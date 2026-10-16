"""Prepare existing numerical signatures once per workspace without changing inputs."""
import os
import subprocess
import sys
import time
from pathlib import Path
from common import MODEL, RUNTIME, ROOT, now, read_json, write_json


def status(runtime=RUNTIME):
    path=Path(runtime)/'numeric-readiness.json'
    return read_json(path) if path.is_file() else {'status':'not-started','label':'首次计算时准备数值缓存'}


def start(runtime=RUNTIME):
    runtime=Path(runtime)
    path=runtime/'numeric-readiness.json'
    write_json(path,{'status':'preparing','label':'正在准备数值编译缓存','startedAt':now()})
    log=(runtime/'numeric-warmup.log').open('w',encoding='utf-8')
    try:
        process=subprocess.Popen([sys.executable,'-B',str(ROOT/'numeric_runtime.py'),'--warm'],
            env={**os.environ,'XAJ_RUNTIME_DIR':str(runtime),'PYTHONUTF8':'1'},stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        import threading
        def finish():
            code=process.wait();log.close()
            if code:
                write_json(path,{'status':'failed','label':'数值预热未完成，计算时将再次准备','error':'详情见 numeric-warmup.log'})
        threading.Thread(target=finish,daemon=True).start()
        return process
    except Exception:
        log.close()
        write_json(path,{'status':'failed','label':'计算时准备数值缓存'})
        return None


def warm():
    started=time.perf_counter()
    sys.path[:0]=[str(MODEL/'code'),str(MODEL/'scripts')]
    import numpy as np
    from runoff_platform.config_loader import load_parameter_set
    from runoff_platform.models.xaj_vectorized import simulate_xaj_liquid_grids
    from state_initialization.core import build_uniform_land_state
    from calibration_platform.routing_kernel import route_arrays
    parameters=load_parameter_set(MODEL/'config/parameter_set_land.json')
    state=build_uniform_land_state(read_json(MODEL/'config/initial_state_land.json'),110,parameters)
    for diagnostics in (False,True):
        simulate_xaj_liquid_grids(np.zeros((1,110)),parameters,np.zeros(1),state,return_diagnostics=diagnostics)
    route_arrays(np.zeros((1,1,3)),np.ones(1),np.zeros(1,dtype=np.int64),np.full(1,-1,dtype=np.int64),
        np.full((1,3),.5),np.asarray([[.2,.3,.5]]),0,np.zeros((1,3)),np.zeros(1),np.zeros(1))
    from runoff_platform.models import xaj_vectorized
    enabled=bool(getattr(xaj_vectorized._array_kernel,'signatures',[])) and bool(getattr(route_arrays,'signatures',[]))
    write_json(RUNTIME/'numeric-readiness.json',{'status':'ready','enabled':enabled,'label':'数值加速缓存已就绪' if enabled else '使用原数值内核',
        'seconds':round(time.perf_counter()-started,3),'completedAt':now()})


if __name__=='__main__':warm()

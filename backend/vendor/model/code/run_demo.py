"""生成演示气象，运行冰川区和非冰川区模型，并导出结果与诊断图。"""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from runoff_model import (
    simulate_glacier, simulate_land, validate_result, GlacierState, GridInfo, RunContext,
)

ROOT=Path(__file__).resolve().parent; OUT=ROOT/"demo_output"; OUT.mkdir(exist_ok=True)

def make_meteo():
    time=pd.date_range("2026-06-01",periods=24*31,freq="h"); n=len(time); hour=np.arange(n)
    temp=-1.5+7*np.sin(2*np.pi*(hour%24-7)/24)+3*np.sin(2*np.pi*hour/(24*31))
    sw=650*np.maximum(0,np.sin(np.pi*(hour%24-6)/12))
    p=np.zeros(n)
    # 同时包含弱降水、持续降水和短时强降雨，用于检验三种产流分量。
    events={
        35:[1,3,8,16,22,10,5,2],
        120:[1,2,3,5,8,12,18,12,8,5,3,2],
        245:[2,5,12,25,18,9,4,2],
        390:[1,2,4,7,12,20,28,18,10,6,3,2],
        580:[2,4,8,15,21,12,6,3],
        690:[1,3,7,18,24,12,5,2],
    }
    for start,values in events.items():
        p[start:start+len(values)]=np.asarray(values,dtype=float)*0.55
    pet=np.maximum(0,0.00045*sw+0.003*np.maximum(temp,0))
    return pd.DataFrame({"precipitation":p,"temperature":temp,"shortwave":sw,"pet":pet},index=time)

def plot_result(g,l):
    fig,ax=plt.subplots(4,1,figsize=(14,11),sharex=True,layout="constrained")
    ax[0].bar(g.index,g.precipitation,width=.035,color="#74b7c3",label="Precipitation"); ax[0].plot(g.index,g.temperature,color="#d28243",lw=.8,label="Temperature"); ax[0].legend(ncol=2); ax[0].set_ylabel("P / T")
    ax[1].plot(g.index,g.snowmelt,label="Snowmelt"); ax[1].plot(g.index,g.icemelt,label="Ice melt"); ax[1].plot(g.index,g.glacier_runoff,label="Glacier runoff",lw=1.5); ax[1].legend(ncol=3); ax[1].set_ylabel("Glacier mm/h")
    ax[2].bar(l.index,l.RS,width=.035,color="#c95f45",alpha=.75,label="XAJ surface runoff RS")
    ax2=ax[2].twinx()
    ax2.plot(l.index,l.interflow,color="#e58a3a",label="Interflow RI",lw=1.2)
    ax2.plot(l.index,l.baseflow,color="#4a9a57",label="Groundwater runoff RG",lw=1.3)
    h1,n1=ax[2].get_legend_handles_labels(); h2,n2=ax2.get_legend_handles_labels()
    ax[2].legend(h1+h2,n1+n2,ncol=3,loc="upper right")
    ax[2].set_ylabel("RS mm/h"); ax2.set_ylabel("RI / RG mm/h")
    ax[3].plot(l.index,l.WU,label="WU"); ax[3].plot(l.index,l.WL,label="WL"); ax[3].plot(l.index,l.WD,label="WD"); ax[3].legend(ncol=3); ax[3].set_ylabel("Tension storage mm")
    fig.suptitle("Hourly runoff model diagnostic",fontweight="bold")
    try:
        fig.savefig(OUT/"demo_diagnostic.png",dpi=180)
    except OSError:
        # Windows 中图片被查看器占用时保留旧图，并另存更新结果。
        fig.savefig(OUT/"demo_diagnostic_updated.png",dpi=180)
    plt.close(fig)

def summary(name,df):
    return {"grid_type":name,"precipitation_mm":df.precipitation.sum(),"rainfall_mm":df.rainfall.sum(),"snowfall_mm":df.snowfall.sum(),"snowmelt_mm":df.snowmelt.sum(),"icemelt_mm":df.icemelt.sum(),"snow_sublimation_mm":df.snow_sublimation.sum(),"ice_sublimation_mm":df.ice_sublimation.sum(),"glacier_ablation_mm":df.glacier_ablation.sum(),"runoff_mm":df.total_runoff.sum(),"et_mm":df.et_actual.sum(),"peak_runoff_mm_h":df.total_runoff.max(),"balance_error_mm":df.water_balance_error.sum()}

if __name__=="__main__":
    met=make_meteo()
    # 演示用较薄初始积雪，确保 31 天内同时覆盖积雪期和裸冰融化期。
    context=RunContext(run_id="DEMO-202607",scenario_id="REFERENCE",param_set_id="TYPICAL-V1")
    g=simulate_glacier(
        met,state=GlacierState(swe=60.0,snow_liquid=1.0,glacier_storage=5.0),
        grid=GridInfo(unit_id="G-GLACIER",zone_type="GLACIER",elevation_m=5200,slope_degree=12),
        context=context,
    )
    l=simulate_land(
        met,grid=GridInfo(unit_id="G-LAND",zone_type="NON_GLACIER",elevation_m=4300,slope_degree=25),
        context=context,
    )
    met.to_csv(OUT/"demo_meteorology.csv",encoding="utf-8-sig"); g.to_csv(OUT/"glacier_hourly_result.csv",encoding="utf-8-sig"); l.to_csv(OUT/"land_hourly_result.csv",encoding="utf-8-sig")
    pd.DataFrame([summary("glacier",g),summary("non_glacier",l)]).to_csv(OUT/"run_summary.csv",index=False,encoding="utf-8-sig")
    plot_result(g,l)
    print(pd.DataFrame([summary("glacier",g),summary("non_glacier",l)]).to_string(index=False))
    print("\nValidation:",validate_result(g,"glacier"),validate_result(l,"land")); print("\nOutput:",OUT)

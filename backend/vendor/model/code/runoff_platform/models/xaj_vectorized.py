"""[XAJ-WARMUP-CORE] Vectorized hourly XAJ calculation for liquid precipitation."""

from __future__ import annotations

from typing import Mapping

import numpy as np

from ..core.types import LandParams


LAND_STATE_FIELDS = ("swe", "snow_liquid", "WU", "WL", "WD", "S", "FR")


def _copy_and_validate_state(
    state: Mapping[str, np.ndarray],
    params: LandParams,
    grid_count: int,
) -> dict[str, np.ndarray]:
    missing = [name for name in LAND_STATE_FIELDS if name not in state]
    if missing:
        raise ValueError(f"initial land state is missing fields: {missing}")
    result = {
        name: np.asarray(state[name], dtype=float).copy()
        for name in LAND_STATE_FIELDS
    }
    wrong_size = [name for name, values in result.items() if values.shape != (grid_count,)]
    if wrong_size:
        raise ValueError(
            f"initial land state fields must have shape ({grid_count},): {wrong_size}"
        )
    limits = {
        "swe": (0.0, np.inf),
        "snow_liquid": (0.0, np.inf),
        "WU": (0.0, params.WUM),
        "WL": (0.0, params.WLM),
        "WD": (0.0, params.WDM),
        "S": (0.0, params.SM),
        "FR": (0.0, 1.0),
    }
    invalid = [
        name
        for name, values in result.items()
        if not np.isfinite(values).all()
        or np.any(values < limits[name][0])
        or np.any(values > limits[name][1])
    ]
    if invalid:
        raise ValueError(f"initial land state is outside parameter bounds: {invalid}")
    return result


# [XAJ-WARMUP-CORE-01] Shared non-glacier liquid-rain XAJ implementation.
def simulate_xaj_liquid_grids(
    precipitation_mm: np.ndarray,
    params: LandParams,
    pet_mm_h: float | np.ndarray,
    initial_state: Mapping[str, np.ndarray],
    return_diagnostics: bool = False,
):
    """Simulate all grids and return RS, RI, RG and the final state.

    The function intentionally requires an explicit initial state. This keeps
    platform configuration outside the XAJ equations and makes warm-up runs
    reproducible. For this non-glacier application precipitation is already
    liquid, so temperature and phase partition are not inputs here.
    """
    precip = np.asarray(precipitation_mm, dtype=float)
    if precip.ndim != 2:
        raise ValueError("precipitation_mm must have shape [time, grid]")
    if not np.isfinite(precip).all() or np.any(precip < 0.0):
        raise ValueError("precipitation_mm must contain finite non-negative values")
    if params.WM <= 0.0 or params.WDM < 0.0 or params.KI + params.KG > 1.0:
        raise ValueError("invalid XAJ storage or source-separation parameters")
    if params.source_coefficient_time_step_hours != 1.0:
        raise ValueError("KI and KG must be coefficients for the 1-hour model time step")
    if params.conversion.enabled:
        raise ValueError(
            "vectorized warm-up requires conversion.enabled=false because the "
            "current batch forcing does not include per-grid slope conversion inputs"
        )

    n_grids = precip.shape[1]
    pet = np.asarray(pet_mm_h, dtype=float)
    if pet.ndim == 0:
        pet = np.full(precip.shape, float(pet), dtype=float)
    elif pet.ndim == 1 and pet.shape == (precip.shape[0],):
        pet = np.repeat(pet[:, None], n_grids, axis=1)
    elif pet.ndim == 2 and pet.shape == precip.shape:
        pet = pet.copy()
    else:
        raise ValueError(
            "pet_mm_h must be a scalar, [time] array or [time, grid] array"
        )
    if not np.isfinite(pet).all() or np.any(pet < 0.0):
        raise ValueError("pet_mm_h must contain finite non-negative values")
    state = _copy_and_validate_state(initial_state, params, n_grids)
    swe = state["swe"]
    snow_liquid = state["snow_liquid"]
    wu = state["WU"]
    wl = state["WL"]
    wd = state["WD"]
    free_s = state["S"]
    fr = state["FR"]

    rs_out: list[np.ndarray] = []
    ri_out: list[np.ndarray] = []
    rg_out: list[np.ndarray] = []
    et_out: list[np.ndarray] = []
    balance_out: list[np.ndarray] = []

    for time_index, rain in enumerate(precip):
        # [XAJ-WARMUP-CORE-01A] Three-layer evapotranspiration.
        old_state_s = free_s.copy()
        old_free = old_state_s * fr
        old_storage = swe + snow_liquid + wu + wl + wd + old_free
        demand = pet[time_index] * float(params.KC)
        available_upper = rain + wu
        eu = np.minimum(available_upper, demand)
        rain_left = rain - np.minimum(rain, eu)
        wu = wu - (eu - np.minimum(rain, eu))
        remaining = demand - eu
        el = np.zeros(n_grids, dtype=float)
        ed = np.zeros(n_grids, dtype=float)
        mask_l1 = remaining > 1.0e-10
        mask_l2 = mask_l1 & (wl >= params.C * params.WLM)
        el[mask_l2] = np.minimum(
            wl[mask_l2],
            remaining[mask_l2] * wl[mask_l2] / max(params.WLM, 1.0e-10),
        )
        mask_l3 = mask_l1 & ~mask_l2 & (wl >= params.C * remaining)
        el[mask_l3] = np.minimum(wl[mask_l3], params.C * remaining[mask_l3])
        mask_deep = mask_l1 & ~mask_l2 & ~mask_l3
        el[mask_deep] = wl[mask_deep]
        ed[mask_deep] = np.minimum(
            wd[mask_deep],
            np.maximum(params.C * remaining[mask_deep] - el[mask_deep], 0.0),
        )
        wl -= el
        wd -= ed
        net = np.maximum(rain_left, 0.0)

        # [XAJ-WARMUP-CORE-01B] Tension-water capacity curve.
        pervious_input = net * (1.0 - params.IM)
        w_before = wu + wl + wd
        w = np.clip(w_before, 0.0, params.WM)
        pe = np.maximum(pervious_input, 0.0)
        wmm = params.WM * (1.0 + params.B)
        a = wmm * (
            1.0
            - np.power(
                np.maximum(1.0 - w / params.WM, 0.0),
                1.0 / (1.0 + params.B),
            )
        )
        runoff = np.where(
            pe + a < wmm,
            pe
            - (params.WM - w)
            + params.WM
            * np.power(
                np.maximum(1.0 - (pe + a) / wmm, 0.0),
                1.0 + params.B,
            ),
            pe - (params.WM - w),
        )
        runoff = np.clip(runoff, 0.0, pe)
        w_after = np.minimum(params.WM, w + pe - runoff)
        addition = np.maximum(w_after - w_before, 0.0)
        take = np.minimum(np.maximum(params.WUM - wu, 0.0), addition)
        wu += take
        addition -= take
        take = np.minimum(np.maximum(params.WLM - wl, 0.0), addition)
        wl += take
        addition -= take
        wd += np.minimum(np.maximum(params.WDM - wd, 0.0), addition)

        # [XAJ-WARMUP-CORE-01C] Free-water storage and RS/RI/RG separation.
        old_volume = np.maximum(free_s, 0.0) * np.clip(fr, 0.0, 1.0)
        no_runoff = runoff <= 1.0e-10
        fr_new = np.clip(runoff / np.maximum(pervious_input, 1.0e-10), 1.0e-10, 1.0)
        overflow = np.maximum(old_volume - params.SM * fr_new, 0.0)
        s = np.minimum(old_volume / fr_new, params.SM)
        smm = params.SM * (1.0 + params.EX)
        relative = np.clip(s / params.SM, 0.0, 1.0)
        au = smm * (
            1.0
            - np.power(
                np.maximum(1.0 - relative, 0.0),
                1.0 / (1.0 + params.EX),
            )
        )
        rs_curve = np.where(
            pervious_input + au < smm,
            fr_new
            * (
                pervious_input
                - params.SM
                + s
                + params.SM
                * np.power(
                    np.maximum(1.0 - (pervious_input + au) / smm, 0.0),
                    1.0 + params.EX,
                )
            ),
            fr_new * (pervious_input + s - params.SM),
        )
        rs_curve = np.clip(rs_curve, 0.0, runoff)
        rs = overflow + rs_curve
        s = np.minimum(
            params.SM,
            np.maximum(0.0, s + (runoff - rs_curve) / fr_new),
        )
        ri = s * params.KI * fr_new
        rg = s * params.KG * fr_new
        free_s = np.maximum(s * (1.0 - params.KI - params.KG), 0.0)
        rs = np.where(no_runoff, 0.0, rs)
        ri = np.where(no_runoff, params.KI * np.maximum(old_free, 0.0), ri)
        rg = np.where(no_runoff, params.KG * np.maximum(old_free, 0.0), rg)
        free_s = np.where(
            no_runoff,
            np.maximum(old_state_s * (1.0 - params.KI - params.KG), 0.0),
            free_s,
        )
        fr = np.where(no_runoff, np.clip(fr, 0.0, 1.0), fr_new)
        rs += net * params.IM
        et_actual = eu + el + ed
        new_storage = swe + snow_liquid + wu + wl + wd + free_s * fr
        balance_error = rain - rs - ri - rg - et_actual - (new_storage - old_storage)
        rs_out.append(rs.astype(np.float32))
        ri_out.append(ri.astype(np.float32))
        rg_out.append(rg.astype(np.float32))
        if return_diagnostics:
            et_out.append(et_actual.astype(np.float32))
            balance_out.append(balance_error.astype(np.float32))

    shape = (0, n_grids)
    rs_values = np.stack(rs_out) if rs_out else np.empty(shape, dtype=np.float32)
    ri_values = np.stack(ri_out) if ri_out else np.empty(shape, dtype=np.float32)
    rg_values = np.stack(rg_out) if rg_out else np.empty(shape, dtype=np.float32)
    final_state = {
        "swe": swe,
        "snow_liquid": snow_liquid,
        "WU": wu,
        "WL": wl,
        "WD": wd,
        "S": free_s,
        "FR": fr,
    }
    if not return_diagnostics:
        return rs_values, ri_values, rg_values, final_state
    diagnostics = {
        "et_actual": np.stack(et_out) if et_out else np.empty(shape, dtype=np.float32),
        "water_balance_error": (
            np.stack(balance_out) if balance_out else np.empty(shape, dtype=np.float32)
        ),
    }
    return rs_values, ri_values, rg_values, final_state, diagnostics

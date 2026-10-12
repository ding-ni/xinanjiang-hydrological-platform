/**
 * 地理数据层预留：后续 SHP / DEM 转 GeoJSON 后由此加载
 * 当前回退到 data/basin.js 内置示意几何
 */
import {
  basinBoundary,
  subBasins,
  riverNetwork,
  stations,
  BASIN_BOUNDS,
  BASIN_CENTER,
  DEFAULT_ZOOM
} from '@/data/basin'

export const geoMeta = {
  source: 'builtin-mock',
  note: '替换为 public/geo/*.geojson 后改 loadGeoBundle',
  bounds: BASIN_BOUNDS,
  center: BASIN_CENTER,
  zoom: DEFAULT_ZOOM
}

export async function loadGeoBundle() {
  // 预留：const res = await fetch('/geo/basin.geojson')
  return {
    basin: basinBoundary,
    subbasins: subBasins,
    rivers: riverNetwork,
    stations,
    meta: geoMeta
  }
}

export function getBuiltinGeo() {
  return {
    basin: basinBoundary,
    subbasins: subBasins,
    rivers: riverNetwork,
    stations,
    meta: geoMeta
  }
}

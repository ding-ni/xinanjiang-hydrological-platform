import TinySDF from '@mapbox/tiny-sdf'
import Pbf from 'pbf'

let installed = false
const cache = new Map()
export function installLocalMapGlyphs(maplibre) {
  if (installed) return
  installed = true
  maplibre.addProtocol('hydro-font', async ({ url }) => {
    const match = /\/(\d+)-(\d+)\.pbf$/.exec(url)
    if (!match) throw new Error('地图文字范围无效')
    const first = Number(match[1]), last = Number(match[2])
    if (last - first !== 255 || first < 0 || last > 65535) throw new Error('地图文字范围无效')
    const key = `${first}-${last}`
    if (!cache.has(key)) {
      const sdf = new TinySDF({ fontSize: 24, buffer: 3, radius: 8, cutoff: 0.25, fontFamily: 'Microsoft YaHei, SimSun, sans-serif' })
      const writer = new Pbf()
      writer.writeMessage(1, (_, stack) => {
        for (let id = first; id <= last; id++) {
          if (id < 32 || (id >= 127 && id < 160)) continue
          const glyph = sdf.draw(String.fromCodePoint(id))
          stack.writeMessage(3, (_, field) => {
            field.writeVarintField(1, id)
            // MapLibre's glyph format has a three-pixel SDF border.
            const width = glyph.glyphWidth, height = glyph.glyphHeight
            field.writeBytesField(2, glyph.data.length ? new Uint8Array(glyph.data) : new Uint8Array((width + 6) * (height + 6)))
            field.writeVarintField(3, width); field.writeVarintField(4, height)
            field.writeSVarintField(5, Math.round(glyph.glyphLeft))
            field.writeSVarintField(6, Math.round(glyph.glyphTop - 27))
            field.writeVarintField(7, Math.round(glyph.glyphAdvance))
          }, null)
        }
      }, null)
      cache.set(key, writer.finish())
    }
    return { data: cache.get(key).slice().buffer }
  })
}

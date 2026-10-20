import { it } from 'node:test'
import assert from 'node:assert/strict'
import {
  buildSmoothGridRainfallDataURL,
  createGridRainfallSampler,
  gridRainfallColorExpression,
  gridRainfallDisplaySignature,
  hasGridRainfall
} from '../src/services/gridRainfallDisplayService.js'

function cell(col, row, qpf) {
  return {
    id: `${col}:${row}`, row: row + 10, col: col + 20, qpf,
    bounds: [[col, row], [col + 1, row], [col + 1, row + 1], [col, row + 1], [col, row]]
  }
}

function rectangle(minLng, minLat, maxLng, maxLat) {
  return [[minLng, minLat], [maxLng, minLat], [maxLng, maxLat], [minLng, maxLat], [minLng, minLat]]
}

function withCanvas(action) {
  const previous = globalThis.document
  const images = []
  globalThis.document = {
    createElement: (tag) => {
      assert.equal(tag, 'canvas')
      const canvas = {
        width: 0, height: 0,
        getContext: () => ({
          createImageData: (width, height) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) }),
          putImageData: (image) => images.push(image)
        }),
        toDataURL: () => 'data:image/png;base64,pixels'
      }
      return canvas
    }
  }
  try { action(images) } finally {
    if (previous === undefined) delete globalThis.document
    else globalThis.document = previous
  }
}

function pixel(image, col, row) {
  const offset = (row * image.width + col) * 4
  return Array.from(image.data.slice(offset, offset + 4))
}

it('preserves grid centre values and locally blends neighbouring grid rainfall continuously', () => {
  const cells = [cell(0, 0, 0), cell(1, 0, 10), cell(0, 1, 20), cell(1, 1, 30)]
  const sample = createGridRainfallSampler(cells)
  assert.equal(sample(0.5, 0.5), 0)
  assert.equal(sample(1.5, 0.5), 10)
  assert.equal(sample(0.5, 1.5), 20)
  assert.equal(sample(1.5, 1.5), 30)
  assert.equal(sample(1, 1), 15)
  assert.ok(Math.abs(sample(1 - 1e-8, 0.5) - sample(1 + 1e-8, 0.5)) < 1e-5)
  for (let x = 0.1; x < 2; x += 0.13) {
    for (let y = 0.1; y < 2; y += 0.13) {
      const value = sample(x, y)
      assert.ok(value >= 0 && value <= 30)
    }
  }
})

it('keeps genuine zero separate from missing, invalid and negative rainfall', () => {
  assert.equal(hasGridRainfall([cell(0, 0, 0)]), true)
  const invalid = [null, undefined, '', ' ', false, true, [], {}, NaN, Infinity, -1]
  for (const value of invalid) {
    const cells = [cell(0, 0, value)]
    assert.equal(hasGridRainfall(cells), false)
    assert.equal(createGridRainfallSampler(cells)(0.5, 0.5), null)
  }
  assert.equal(createGridRainfallSampler([cell(0, 0, 0)])(0.5, 0.5), 0)
})

it('does not fill missing containing cells, absent cells or gaps with surrounding rainfall', () => {
  const sample = createGridRainfallSampler([cell(0, 0, 10), cell(1, 0, null), cell(2, 0, 80)])
  assert.equal(sample(0.9, 0.5), 10)
  assert.equal(sample(1.1, 0.5), null)
  assert.equal(sample(1.9, 0.5), null)
  assert.equal(sample(2.1, 0.5), 80)
  assert.equal(sample(3.1, 0.5), null)
  const sparse = createGridRainfallSampler([cell(0, 0, 10), cell(2, 0, 80)])
  assert.equal(sparse(1.5, 0.5), null)
  const diagonal = createGridRainfallSampler([cell(0, 0, 10), cell(1, 1, 80)])
  assert.equal(diagonal(0.9, 0.9), 10)
  assert.equal(diagonal(1.1, 1.1), 80)
})

it('uses full geometry and rainfall precision in display cache signatures', () => {
  const cells = [cell(0, 0, 1)]
  const baseline = gridRainfallDisplaySignature(cells)
  assert.equal(gridRainfallDisplaySignature(structuredClone(cells)), baseline)
  const rainfall = structuredClone(cells)
  rainfall[0].qpf += 0.000001
  assert.notEqual(gridRainfallDisplaySignature(rainfall), baseline)
  const geometry = structuredClone(cells)
  geometry[0].bounds[0][0] += 0.000001
  assert.notEqual(gridRainfallDisplaySignature(geometry), baseline)
})

it('does not mutate source cells, time values or geometry while sampling and rendering', () => {
  const cells = [cell(0, 0, 10), cell(1, 0, 20)]
  cells[0].values = [10, 11]
  const before = structuredClone(cells)
  const sample = createGridRainfallSampler(cells)
  assert.equal(sample(1, 0.5), 15)
  withCanvas(() => buildSmoothGridRainfallDataURL({
    cells, basin: { type: 'Polygon', coordinates: [rectangle(0, 0, 2, 1)] }, size: 4
  }))
  assert.deepEqual(cells, before)
  cells[0].qpf = 99
  assert.equal(sample(0.5, 0.5), 10, 'sampler captures its own display snapshot')
})

it('renders only the intersection of basin and valid containing grid cells', () => {
  withCanvas((images) => {
    const url = buildSmoothGridRainfallDataURL({
      cells: [cell(0, 0, 10), cell(1, 0, null), cell(2, 0, 80)],
      basin: { type: 'Polygon', coordinates: [rectangle(0, 0, 3, 1)] },
      bounds: [[-1, 0], [3, 1]], size: 8
    })
    assert.equal(url, 'data:image/png;base64,pixels')
    const image = images.at(-1)
    assert.equal(pixel(image, 0, 4)[3], 0, 'outside basin is transparent')
    assert.ok(pixel(image, 2, 4)[3] > 0)
    assert.equal(pixel(image, 4, 4)[3], 0, 'missing cell remains transparent')
    assert.equal(pixel(image, 5, 4)[3], 0)
    assert.ok(pixel(image, 6, 4)[3] > 0)
  })
})

it('retains basin holes and every island in MultiPolygon or FeatureCollection masks', () => {
  const geometry = {
    type: 'MultiPolygon', coordinates: [
      [rectangle(0, 0, 2, 2), rectangle(0.5, 0.5, 1.5, 1.5)],
      [rectangle(3, 0, 4, 2)]
    ]
  }
  const cells = []
  for (let col = 0; col < 4; col += 1) for (let row = 0; row < 2; row += 1) cells.push(cell(col, row, 10))
  withCanvas((images) => {
    buildSmoothGridRainfallDataURL({ cells, basin: geometry, bounds: [[0, 0], [4, 2]], size: 8 })
    let image = images.at(-1)
    assert.ok(pixel(image, 0, 4)[3] > 0)
    assert.equal(pixel(image, 1, 4)[3], 0, 'first island hole is transparent')
    assert.equal(pixel(image, 4, 4)[3], 0, 'space between islands is transparent')
    assert.ok(pixel(image, 6, 4)[3] > 0, 'second island is present')
    buildSmoothGridRainfallDataURL({
      cells,
      basin: { type: 'FeatureCollection', features: geometry.coordinates.map((coordinates) => ({
        type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates }
      })) }, bounds: [[0, 0], [4, 2]], size: 8
    })
    image = images.at(-1)
    assert.equal(pixel(image, 1, 4)[3], 0)
    assert.ok(pixel(image, 6, 4)[3] > 0)
  })
})

it('shares the exact grid colour scale and alpha values, including transparent zero', () => {
  const expression = gridRainfallColorExpression()
  assert.deepEqual(expression.slice(0, 3), ['interpolate', ['linear'], ['coalesce', ['get', 'qpf'], 0]])
  assert.equal(expression[expression.indexOf(10) + 1], 'rgba(38,119,196,0.56)')
  withCanvas((images) => {
    const basin = { type: 'Polygon', coordinates: [rectangle(0, 0, 1, 1)] }
    for (const [rain, expected] of [[0, [255, 255, 255, 0]], [10, [38, 119, 196, 143]], [80, [211, 64, 51, 189]], [120, [211, 64, 51, 189]]]) {
      buildSmoothGridRainfallDataURL({ cells: [cell(0, 0, rain)], basin, size: 1 })
      assert.deepEqual(pixel(images.at(-1), 0, 0), expected)
    }
  })
})

it('returns no image for unavailable grid data and caps the display raster size', () => {
  withCanvas((images) => {
    const basin = { type: 'Polygon', coordinates: [rectangle(0, 0, 1, 1)] }
    assert.equal(buildSmoothGridRainfallDataURL({ cells: [], basin }), null)
    assert.equal(buildSmoothGridRainfallDataURL({ cells: [cell(0, 0, null)], basin }), null)
    assert.equal(buildSmoothGridRainfallDataURL({ cells: [cell(0, 0, 10)], basin: null }), null)
    assert.equal(images.length, 0)
    buildSmoothGridRainfallDataURL({ cells: [cell(0, 0, 10)], basin, size: 1024 })
    assert.equal(images.at(-1).width, 192)
    assert.equal(images.at(-1).height, 192)
  })
})

import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { latestInputProcessingWindow, mergeInputSourceChoices } from '../src/services/inputSourceChoices.js'

describe('new rainfall sources and source-clock input windows', () => {
  it('makes a freshly saved source immediately selectable with all receipt metadata', () => {
    const old = { sourceId: 'SRC-old', name: 'previous', stationCount: 3 }
    const saved = { sourceId: 'SRC-new', name: 'selected rainfall', stationCount: 5, start: '2024-06-01T00:00', end: '2024-06-07T23:00', sourceSHA256: 'source-digest' }
    const choices = mergeInputSourceChoices([old], [saved])
    assert.deepEqual(choices.find(row => row.sourceId === saved.sourceId), saved)
    assert.equal(choices.length, 2)
  })

  it('keeps newly registered sources when a stale refresh completes, without duplicates or modifying inputs', () => {
    const oldResponse = [{ sourceId: 'SRC-old', stationCount: 3 }, { sourceId: 'SRC-new', name: 'earlier name' }]
    const savedReceipts = [{ sourceId: 'SRC-new', name: 'current name', stationCount: 5 }, { sourceId: 'SRC-newer', name: 'second saved source' }]
    const before = JSON.stringify({ oldResponse, savedReceipts })
    const once = mergeInputSourceChoices(oldResponse, savedReceipts)
    const twice = mergeInputSourceChoices(once, savedReceipts)
    assert.equal(twice.length, 3)
    assert.equal(twice.find(row => row.sourceId === 'SRC-new').name, 'current name')
    assert.equal(twice.find(row => row.sourceId === 'SRC-new').stationCount, 5)
    assert.equal(JSON.stringify({ oldResponse, savedReceipts }), before)
    assert.deepEqual(mergeInputSourceChoices([], []), [])
  })

  it('chooses consecutive source-clock hours identically across browser timezones and a daylight-saving boundary', () => {
    const previousTimezone = process.env.TZ
    try {
      for (const timezone of ['UTC', 'Asia/Shanghai', 'America/New_York']) {
        process.env.TZ = timezone
        assert.deepEqual(latestInputProcessingWindow({ start: '2024-03-01T00:00:00', end: '2024-03-10T12:00:00' }), {
          start: '2024-03-09T13:00', end: '2024-03-10T12:00'
        })
      }
    } finally {
      if (previousTimezone == null) delete process.env.TZ
      else process.env.TZ = previousTimezone
    }
  })

  it('clamps short source intervals and rejects invalid or non-hourly source clocks', () => {
    assert.deepEqual(latestInputProcessingWindow({ start: '2024-06-30T13:00:00', end: '2024-06-30T15:00:00' }), {
      start: '2024-06-30T13:00', end: '2024-06-30T15:00'
    })
    for (const range of [null, { start: '2024-02-30T00:00', end: '2024-03-01T00:00' },
      { start: '2024-06-30T13:30:00', end: '2024-06-30T15:00:00' },
      { start: '2024-07-01T00:00', end: '2024-06-30T15:00' }]) assert.equal(latestInputProcessingWindow(range), null)
    assert.equal(latestInputProcessingWindow({ start: '2024-06-30T13:00', end: '2024-06-30T15:00' }, 0), null)
  })
})

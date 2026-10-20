import { it } from 'node:test'
import assert from 'node:assert/strict'
import { resolveDomainContext } from '../src/data/domainContext.js'
import {
  parameterLibraryFor, productionParameterVersion, registerCalibrationResult,
  reviewSimulationParameter, publishAutomaticParameter, releaseReadiness, independentTestPassed
} from '../src/services/parameterLibraryService.js'

const STORAGE_KEY = 'hydro_parameter_library_overrides_v1'
const context = resolveDomainContext('nananxi-xufan', { modelScale: '1km' })
const frozenContext = {
  projectId: context.basinId, resolutionId: context.modelScale,
  modelConfigId: context.configId, contextKey: context.contextKey
}

function withStorage(saved, run) {
  const storage = new Map([[STORAGE_KEY, JSON.stringify(saved)]])
  const previous = globalThis.localStorage
  globalThis.localStorage = { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) }
  try { run(storage) } finally { globalThis.localStorage = previous }
}

function candidate(patch = {}) {
  return {
    id: 'PARAM-JOB-qualified', source: 'model-backend', resultSource: 'model-backend',
    sourceTaskId: 'JOB-qualified', context: frozenContext, contextKey: context.contextKey,
    projectId: context.basinId, resolutionId: context.modelScale, modelConfigId: context.configId,
    parameters: { WM: 150 }, metrics: { calibration: { nse: 0.2 } },
    independentTest: { passed: true }, review: { status: '审核通过' }, productionEligible: false,
    ...patch
  }
}

function libraries(versions = [], automatic = {}) {
  return {
    simulationRateLibrary: { versions },
    automaticForecastLibrary: { versions: [], productionVersion: null, ...automatic }
  }
}

function scoped(library) { return { 'nananxi-xufan:1km': library } }
function options(patch = {}) {
  return { projectId: context.basinId, resolutionId: context.modelScale, projectData: null, activeContext: context, ...patch }
}
function lookup(projectData = null, activeContext = context) {
  return parameterLibraryFor(projectData, context.basinId, context.modelScale, activeContext)
}

it('offline unified libraries quarantine old mock, unlabelled and foreign candidates without changing storage', () => {
  const old = [
    candidate({ id: 'OLD-FIXTURE', source: 'frontend-fixture', resultSource: 'frontend-fixture', sourceTaskId: 'SIM-old', productionEligible: true }),
    candidate({ id: 'OLD-UNLABELLED', source: null, resultSource: null }),
    candidate({ id: 'WRONG-TASK', sourceTaskId: 'SIM-forged' }),
    candidate({ id: 'WRONG-SCALE', contextKey: resolveDomainContext('nananxi-xufan', { modelScale: '0p1deg' }).contextKey }),
    candidate({ id: 'WRONG-NESTED-SCOPE', context: { ...frozenContext, modelConfigId: 'foreign' } }),
    candidate({ id: 'SOURCE-CONFLICT', resultSource: 'frontend-fixture' }),
    candidate({ id: 'TASK-CONFLICT', taskId: 'JOB-another' }),
    candidate({ id: 'KEY-MISSING', context: null, contextKey: null })
  ]
  const original = scoped(libraries(old, { versions: old.map((row) => ({ ...row, status: 'production' })), productionVersion: 'OLD-FIXTURE', status: '已发布生产版本' }))
  withStorage(original, (storage) => {
    const snapshot = storage.get(STORAGE_KEY)
    const library = lookup()
    assert.deepEqual(library.simulationRateLibrary.versions, [])
    assert.deepEqual(library.automaticForecastLibrary.versions, [])
    assert.equal(productionParameterVersion(library), null)
    assert.equal(storage.get(STORAGE_KEY), snapshot)
    for (const row of old) {
      assert.throws(() => reviewSimulationParameter(options({ versionId: row.id, decision: 'approve' })), /找不到/)
      assert.throws(() => publishAutomaticParameter(options({ versionId: row.id })), /找不到/)
    }
    assert.equal(storage.get(STORAGE_KEY), snapshot)
  })
})

it('genuine same-workspace candidates survive an outage but cannot cross case, scale or config', () => {
  const genuine = candidate()
  const saved = scoped(libraries([genuine]))
  withStorage(saved, () => {
    assert.deepEqual(lookup({ backendError: 'offline' }).simulationRateLibrary.versions, [genuine])
    assert.deepEqual(parameterLibraryFor(null, 'nananxi-xufan', '0p1deg').simulationRateLibrary.versions, [])
    assert.deepEqual(parameterLibraryFor(null, 'selinco', '1km').simulationRateLibrary.versions, [])
    assert.deepEqual(lookup(null, { ...context, configId: 'unknown-config' }).simulationRateLibrary.versions, [])
    assert.deepEqual(lookup(null, { ...context, projectId: 'foreign-framework' }).simulationRateLibrary.versions, [])
    assert.deepEqual(lookup(null, { ...context, modelScale: '0p1deg' }).simulationRateLibrary.versions, [])
    assert.deepEqual(parameterLibraryFor(null, 'unknown-case', '1km').simulationRateLibrary.versions, [])
  })
})

it('all separately declared context scopes must agree instead of letting nested context hide a conflict', () => {
  const fields = ['projectId', 'domainId', 'resolutionId', 'modelScale', 'modelConfigId', 'modelProjectId']
  withStorage(scoped(libraries(fields.map((field) => candidate({ id: `BAD-${field}`, [field]: 'foreign' })))), () => {
    assert.deepEqual(lookup().simulationRateLibrary.versions, [])
  })
})

it('server candidates override cached metrics and production eligibility while preserving only identical-task review', () => {
  const cached = candidate({ productionEligible: true, metrics: { calibration: { nse: 0.99 } } })
  const automatic = { ...cached, id: 'AUTO-PARAM-JOB-qualified', status: 'production' }
  const remote = {
    taskId: 'JOB-qualified', parameterVersionId: cached.id, context: frozenContext, resultSource: 'model-backend',
    bestParameters: { WM: 151 }, calibrationMetrics: { nse: 0.15 }, independentTest: { passed: false }, productionEligible: false
  }
  withStorage(scoped(libraries([cached], { versions: [automatic], productionVersion: automatic.id })), (storage) => {
    const snapshot = storage.get(STORAGE_KEY)
    const library = lookup({ backendParameterVersions: [remote] })
    const visible = library.simulationRateLibrary.versions[0]
    assert.deepEqual(visible.parameters, { WM: 151 })
    assert.equal(visible.metrics.calibration.nse, 0.15)
    assert.equal(visible.productionEligible, false)
    assert.equal(visible.review.status, '审核通过')
    assert.equal(releaseReadiness(visible).canPublish, false)
    assert.deepEqual(library.automaticForecastLibrary.versions, [])
    assert.equal(productionParameterVersion(library), null)
    assert.equal(storage.get(STORAGE_KEY), snapshot)
    const refreshed = lookup({ backendParameterVersions: [{ ...remote, taskId: 'JOB-new-origin' }] })
    assert.equal(refreshed.simulationRateLibrary.versions[0].review.status, '待审核')
  })
})

it('untrusted remote rows are not relabelled as model-backend candidates', () => {
  const remote = { taskId: 'JOB-remote', parameterVersionId: 'REMOTE', context: frozenContext, resultSource: 'model-backend' }
  const invalid = [null, { ...remote, resultSource: null }, { ...remote, resultSource: 'frontend-fixture' },
    { ...remote, taskId: 'SIM-forged' }, { ...remote, source: 'frontend-fixture' },
    { ...remote, projectId: 'foreign' }, { ...remote, context: { ...frozenContext, resolutionId: '0p1deg' } }]
  withStorage({}, () => assert.deepEqual(lookup({ backendParameterVersions: invalid }).simulationRateLibrary.versions, []))
})

it('unsafe registration cannot manufacture a trusted source or overwrite retained records', () => {
  withStorage(scoped(libraries([candidate({ source: 'frontend-fixture', resultSource: 'frontend-fixture', sourceTaskId: 'SIM-old' })])), (storage) => {
    const snapshot = storage.get(STORAGE_KEY)
    const baseline = { parameterVersionId: 'P1', taskId: 'JOB-registration', resultSource: 'model-backend', context: frozenContext }
    const invalid = [
      { ...baseline, resultSource: null }, { ...baseline, resultSource: 'frontend-fixture' },
      { ...baseline, taskId: 'SIM-forged' }, { ...baseline, domainId: 'selinco' },
      { ...baseline, context: { ...frozenContext, modelConfigId: 'foreign' } }
    ]
    for (const result of invalid) {
      assert.equal(registerCalibrationResult({ ...options(), request: { context: frozenContext }, result }), null)
    }
    assert.equal(registerCalibrationResult({ ...options(), request: { context: { ...frozenContext, projectId: 'selinco' } }, result: baseline }), null)
    assert.equal(registerCalibrationResult({ ...options(), request: { context: frozenContext }, result: baseline, taskId: 'JOB-conflicting' }), null)
    assert.equal(storage.get(STORAGE_KEY), snapshot)
  })
})

it('registering and reviewing a genuine candidate retain hidden legacy rows and unrelated libraries verbatim', () => {
  const old = candidate({ id: 'LEGACY', source: 'frontend-fixture', resultSource: 'frontend-fixture', sourceTaskId: 'SIM-old' })
  const oldAutomatic = { versions: [{ ...old, id: 'AUTO-LEGACY' }], productionVersion: 'AUTO-LEGACY', note: '原记录保留' }
  const saved = { ...scoped(libraries([old], oldAutomatic)), 'other-case:1km': { untouched: true } }
  withStorage(saved, (storage) => {
    const result = { parameterVersionId: 'REAL-NEW', taskId: 'JOB-new', resultSource: 'model-backend', context: frozenContext,
      bestParameters: { WM: 140 }, independentTest: { passed: true }, productionEligible: false }
    const version = registerCalibrationResult({ ...options(), request: { context: frozenContext }, result })
    assert.equal(version.source, 'model-backend')
    assert.equal(version.contextKey, context.contextKey)
    assert.deepEqual(version.context, frozenContext)
    const reviewed = reviewSimulationParameter(options({ versionId: version.id, decision: 'approve' }))
    assert.equal(reviewed.review.status, '审核通过')
    assert.equal(releaseReadiness(reviewed).canPublish, false)
    assert.throws(() => publishAutomaticParameter(options({ versionId: version.id })), /生产验收/)
    const persisted = JSON.parse(storage.get(STORAGE_KEY))
    assert.deepEqual(persisted['nananxi-xufan:1km'].simulationRateLibrary.versions.find((row) => row.id === old.id), old)
    assert.deepEqual(persisted['nananxi-xufan:1km'].automaticForecastLibrary, oldAutomatic)
    assert.deepEqual(persisted['other-case:1km'], { untouched: true })
    assert.deepEqual(lookup().simulationRateLibrary.versions.map((row) => row.id), ['REAL-NEW'])
  })
})

it('real publication requires explicit boolean production eligibility as well as trusted lineage and review', () => {
  for (const productionEligible of [undefined, null, false, 0, 1, 'true']) {
    const version = candidate({ productionEligible })
    assert.equal(releaseReadiness(version).canPublish, false)
    withStorage(scoped(libraries([version])), () => {
      assert.throws(() => publishAutomaticParameter(options({ versionId: version.id })), /生产验收/)
    })
  }
  const valid = candidate({ productionEligible: true })
  assert.equal(releaseReadiness(valid).canPublish, true)
  for (const invalid of [{ ...valid, sourceTaskId: 'SIM-forged' }, { ...valid, contextKey: 'foreign' },
    { ...valid, independentTest: { passed: false, nse: 0.99 } }, { ...valid, review: { status: '待审核' } }]) {
    assert.equal(releaseReadiness(invalid).canPublish, false)
  }
  assert.equal(releaseReadiness({ productionEligible: true, independentTest: { passed: true }, review: { status: '审核通过' } }).canPublish, false)
})

it('a future explicitly qualified real version can publish without removing quarantined automatic history', () => {
  const valid = candidate({ productionEligible: true })
  const old = candidate({ id: 'AUTO-OLD', source: 'frontend-fixture', resultSource: 'frontend-fixture', sourceTaskId: 'SIM-old', status: 'production' })
  withStorage(scoped(libraries([valid], { versions: [old], productionVersion: old.id })), (storage) => {
    const published = publishAutomaticParameter(options({ versionId: valid.id }))
    assert.equal(published.status, 'production')
    assert.equal(productionParameterVersion(lookup()).id, published.id)
    const saved = JSON.parse(storage.get(STORAGE_KEY))['nananxi-xufan:1km']
    assert.deepEqual(saved.automaticForecastLibrary.versions.find((row) => row.id === old.id), old)
    assert.deepEqual(saved.simulationRateLibrary.versions, [valid])
  })
})

it('both registered Selinco demos remain visible only within their own complete scope and never acquire real publication rights', () => {
  for (const modelScale of ['1km', '0p1deg']) {
    const demo = resolveDomainContext('selinco', { modelScale })
    const version = { id: `DEMO-${modelScale}`, source: 'frontend-fixture', sourceTaskId: 'SIM-demo', contextKey: demo.contextKey,
      modelConfigId: demo.configId, resolutionId: modelScale, independentTest: { passed: true }, review: { status: '审核通过' }, productionEligible: true }
    withStorage({ [`selinco:${modelScale}`]: libraries([version]) }, () => {
      assert.deepEqual(parameterLibraryFor(null, 'selinco', modelScale, demo).simulationRateLibrary.versions, [version])
      assert.equal(releaseReadiness(version).canPublish, false)
      assert.throws(() => publishAutomaticParameter({ projectId: 'selinco', resolutionId: modelScale, activeContext: demo, versionId: version.id }), /生产验收/)
      assert.deepEqual(parameterLibraryFor(null, 'selinco', modelScale, { ...demo, configId: 'unknown' }).simulationRateLibrary.versions, [])
    })
  }
})

it('missing independent metrics are not filled with zero peak error or accepted through numeric coercion', () => {
  assert.equal(independentTestPassed({ nse: 0.99 }), false)
  assert.equal(independentTestPassed({ nse: 0.99, peakErrorPercent: -200 }), false)
  for (const value of [null, undefined, '', '  ', true, false, [], {}]) {
    assert.equal(independentTestPassed({ nse: 0.99, peakErrorPercent: value }), false)
  }
  assert.equal(independentTestPassed({ nse: '0.7', peakErrorPercent: '-10' }), true)
})

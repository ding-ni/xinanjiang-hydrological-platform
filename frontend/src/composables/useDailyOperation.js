import { onBeforeUnmount, ref } from 'vue'
import { activeOperation, belongsToOperationContext, completedOperation, dailyClient, operationId } from '@/services/dailyOperationService'
import { modelApi, isMissingResult } from '@/services/modelBackendService'
import { trackTemporary } from '@/services/resultRetentionService'

export function useDailyOperation({ client = dailyClient, onChange = () => {}, contextKey = () => null } = {}) {
  const operation = ref(null), result = ref(null), error = ref(''), submitting = ref(false)
  let timer = null, generation = 0, submissionSequence = 0, disposed = false
  async function inspect(id) {
    const revision = ++generation
    const expectedContext = contextKey()
    clearTimeout(timer); result.value = null; error.value = ''
    const refresh = async () => {
      try {
        const row = await client.read(id)
        if (disposed || revision !== generation) return
        if (expectedContext && !belongsToOperationContext(row, expectedContext)) throw new Error('请在该成果所属流域查看计算')
        operation.value = row; onChange(row)
        if (completedOperation(row)) {
          const payload = await client.result(id)
          if (!disposed && revision === generation) {
            result.value = payload
            modelApi('result-catalog').then(catalog=>{const record=catalog.results.find(item=>item.id===id);if(record)trackTemporary(record)}).catch(()=>{})
          }
        } else if (activeOperation(row)) timer = setTimeout(refresh, 1500)
        else if (row.status === 'failed') error.value = row.errorMessage || row.error || '计算未完成'
      } catch (failure) {
        if (!disposed && revision === generation) {
          if (isMissingResult(failure)) { operation.value=null; result.value=null; onChange(null) }
          error.value = failure.message
        }
      }
    }
    await refresh()
  }
  async function submit(body) {
    if (submitting.value || activeOperation(operation.value)) return
    const revision = generation, sequence = ++submissionSequence
    submitting.value = true; error.value = ''
    try {
      const row = await client.submit(body)
      if (disposed || revision !== generation || sequence !== submissionSequence) return row
      operation.value = row; onChange(row); await inspect(operationId(row)); return row
    }
    catch (failure) { if (sequence === submissionSequence && !disposed) error.value = failure.message; return null }
    finally { if (sequence === submissionSequence) submitting.value = false }
  }
  async function cancel() {
    if (!operationId(operation.value)) return
    try { await client.cancel(operationId(operation.value)); await inspect(operationId(operation.value)) }
    catch (failure) { error.value = failure.message }
  }
  function clear() { ++generation; ++submissionSequence; clearTimeout(timer); operation.value = null; result.value = null; error.value = ''; submitting.value = false }
  onBeforeUnmount(() => { disposed = true; ++generation; clearTimeout(timer) })
  return { operation, result, error, submitting, inspect, submit, cancel, clear }
}

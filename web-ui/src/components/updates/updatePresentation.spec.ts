import { describe, expect, it } from 'vitest'
import {
  canRetryUpdateTarget,
  incompatibilityReasons,
  resolvedNodeId,
  updateStatusLabel,
  updateStatusType,
} from './updatePresentation'

describe('update presentation helpers', () => {
  it('maps deployment states without hiding unknown protocol values', () => {
    expect(updateStatusLabel('awaiting_approval')).toBe('等待批准')
    expect(updateStatusType('failed')).toBe('danger')
    expect(updateStatusLabel('future_state')).toBe('future_state')
  })

  it('normalizes target resolution compatibility fields', () => {
    expect(resolvedNodeId({ node_id: 'node-1', compatible: false })).toBe('node-1')
    expect(incompatibilityReasons({
      id: 'node-2',
      compatible: false,
      incompatibility_reasons: ['update protocol mismatch'],
    })).toEqual(['update protocol mismatch'])
  })

  it('only retries failed or paused targets', () => {
    const base = { node_id: 'node-1', progress: 0, attempts: 1 }
    expect(canRetryUpdateTarget({ ...base, status: 'failed' })).toBe(true)
    expect(canRetryUpdateTarget({ ...base, status: 'completed' })).toBe(false)
  })
})

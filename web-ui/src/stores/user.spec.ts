import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useUserStore, type CurrentUser } from './user'

function administrator(expiresAt: string | null): CurrentUser {
  return {
    id: 'admin-id',
    username: 'admin',
    role: 'admin',
    disabled: false,
    permissions: ['tasks.low', 'terminal.admin', 'settings.write'],
    step_up_expires_at: expiresAt,
  }
}

describe('user permission expiry', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('keeps base permissions but rejects expired step-up permissions', () => {
    const store = useUserStore()
    store.user = administrator(new Date(Date.now() - 1_000).toISOString())

    expect(store.hasPermission('tasks.low')).toBe(true)
    expect(store.hasPermission('terminal.admin')).toBe(false)
    expect(store.hasPermission('settings.write')).toBe(false)
  })

  it('accepts high-risk permissions only while step-up is current', () => {
    const store = useUserStore()
    store.user = administrator(new Date(Date.now() + 60_000).toISOString())

    expect(store.hasPermission('terminal.admin')).toBe(true)
    expect(store.hasPermission('settings.write')).toBe(true)
  })
})

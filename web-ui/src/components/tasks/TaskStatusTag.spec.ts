// @vitest-environment jsdom
import { createApp, defineComponent, h } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'
import TaskStatusTag from './TaskStatusTag.vue'

let mounted: { app: ReturnType<typeof createApp>; element: HTMLElement } | null = null

afterEach(() => {
  mounted?.app.unmount()
  mounted?.element.remove()
  mounted = null
})

function mountStatus(status: string) {
  const element = document.createElement('div')
  document.body.appendChild(element)
  const app = createApp(TaskStatusTag, { status })
  app.component('el-tag', defineComponent({
    props: { type: String },
    setup(props, { slots }) {
      return () => h('span', { 'data-type': props.type }, slots.default?.())
    },
  }))
  app.mount(element)
  mounted = { app, element }
  return element
}

describe('TaskStatusTag', () => {
  it('renders the localized paused state and warning type', () => {
    const element = mountStatus('paused')
    expect(element.textContent).toContain('已暂停')
    expect(element.querySelector('span')?.dataset.type).toBe('warning')
  })

  it('fails safely for an unknown server state', () => {
    const element = mountStatus('future_state')
    expect(element.textContent).toContain('future_state')
    expect(element.querySelector('span')?.dataset.type).toBe('info')
  })
})

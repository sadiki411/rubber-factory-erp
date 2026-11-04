import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { JSDOM } from 'jsdom'
import { describe, expect, it } from 'vitest'

const html = readFileSync(resolve('../docs/prototypes/erp-rework-pilot.html'), 'utf8')

describe('isolated return/rework/reinspection/reship demonstration', () => {
  function demo() {
    const dom = new JSDOM(html, { runScripts: 'dangerously' })
    const element = (id) => dom.window.document.getElementById(id)
    return { dom, element }
  }
  it('requires explicitly selected people and records each stage without adding delivery early', () => {
    const { dom, element } = demo()
    element('work').click()
    expect(element('message').textContent).toContain('请选择实际返工人员')
    element('worker').value = '演示返工甲'
    element('work').click()
    expect(element('net').textContent).toContain('0件')
    element('checker').value = '演示复检乙'
    element('fail').click()
    expect(element('message').textContent).toContain('退回仍是第1轮')
    element('work').click()
    element('pass').click()
    expect(element('net').textContent).toContain('0件')
    element('shipper').value = '演示本轮品检甲'
    element('ship').click()
    expect(element('net').textContent).toContain('280件')
    expect(element('events').textContent).toContain('实际作业第2次')
    expect(element('state').textContent).toBe('已再出货')
    dom.window.close()
  })
  it('keeps missing evidence explicit on the proposed skip path and resets in memory only', () => {
    const { dom, element } = demo()
    element('shipper').value = '演示本轮品检乙'
    element('skip').click()
    expect(element('state').textContent).toContain('过程证据不完整')
    expect(element('events').textContent).not.toContain('实际作业第1次')
    element('reset').click()
    expect(element('events').children).toHaveLength(1)
    expect(element('net').textContent).toContain('0件')
    expect(html).toContain("connect-src 'none'")
    expect(html).not.toMatch(/\b(fetch|XMLHttpRequest|localStorage|sessionStorage)\s*[.(]/)
    dom.window.close()
  })
})

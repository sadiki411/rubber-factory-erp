import { render, screen } from '@testing-library/react'
import { ReadOnlyContext } from '../access'
import { WriteAccess } from './WriteAccess'
import { QualityEmployeeDrawer, QualityReworkDrawer, QualityShipmentDrawer } from './QualityFormDrawers'
import { ManualFinancialDrawer, ManualPerformanceDrawer } from './AnalyticsEntryDrawers'
import { QualityFlowCardReturnDrawer, QualityProcessCardReplacementDrawer } from './QualityFlowCardReturnDrawer'
import { QualityWeightShipmentDrawer } from './QualityWeightShipmentDrawer'

it('never mounts a write form for read-only users, including a direct drawer entry', () => {
  const mount = vi.fn()
  function Form() { mount(); return <button>保存任务</button> }
  render(<ReadOnlyContext.Provider value><WriteAccess open onClose={vi.fn()}><Form /></WriteAccess></ReadOnlyContext.Provider>)
  expect(screen.getByText('此账号只能查看记录，不能新增、修改或取消。')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '保存任务' })).not.toBeInTheDocument()
  expect(mount).not.toHaveBeenCalled()
})

it('keeps the normal write form for an authorized account', () => {
  render(<WriteAccess open onClose={vi.fn()}><button>保存任务</button></WriteAccess>)
  expect(screen.getByRole('button', { name: '保存任务' })).toBeInTheDocument()
})

it.each([
  ['employee', <QualityEmployeeDrawer open onClose={vi.fn()} />],
  ['piece shipment', <QualityShipmentDrawer open mode="PIECES" orders={[]} employees={[]} onClose={vi.fn()} />],
  ['piece return', <QualityReworkDrawer open shipments={[]} employees={[]} onClose={vi.fn()} />],
  ['weighted shipment', <QualityWeightShipmentDrawer open orders={[]} employees={[]} onClose={vi.fn()} />],
  ['scan return', <QualityFlowCardReturnDrawer open employees={[]} onClose={vi.fn()} onSaved={vi.fn()} onBackfillShipment={vi.fn()} />],
  ['replacement', <QualityProcessCardReplacementDrawer open onClose={vi.fn()} onSaved={vi.fn()} />],
  ['performance', <ManualPerformanceDrawer open machines={[]} employees={[]} onClose={vi.fn()} />],
  ['financial', <ManualFinancialDrawer open machines={[]} onClose={vi.fn()} />],
])('blocks the actual %s deep write form before its data hooks mount', (_, form) => {
  // No QueryClientProvider: mounting the write component would itself fail.
  render(<ReadOnlyContext.Provider value>{form}</ReadOnlyContext.Provider>)
  expect(screen.getByText('此账号只能查看记录，不能新增、修改或取消。')).toBeInTheDocument()
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
})

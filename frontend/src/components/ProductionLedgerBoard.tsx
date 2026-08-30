import { CheckCircleOutlined, EditOutlined, HistoryOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { App, Button, Card, Empty, Input, Popconfirm, Progress, Space, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { productionApi, toList } from '../api/client'
import type { ProductionDailyLog, ProductionRun } from '../types'
import { ProductionCounterDrawer } from './ProductionCounterDrawer'
import { ProductionLedgerTaskDrawer } from './ProductionLedgerTaskDrawer'

function taskStatus(run: ProductionRun) {
  if (run.status === 'CANCELLED') return <Tag>已取消</Tag>
  if (run.status === 'COMPLETED') return <Tag color="success">生产任务已完成</Tag>
  if (run.status === 'PAUSED_ON_MACHINE') return <Tag color="warning">暂停·模具在机</Tag>
  if (run.status === 'PAUSED_UNLOADED') return <Tag color="warning">暂停·已下机</Tag>
  if (run.target_reached) return <Tag color="success">已达目标·待确认结束</Tag>
  return <Tag color="processing">进行中</Tag>
}

function logDate(log: ProductionDailyLog) {
  return log.date || '日期未记录'
}

export function ProductionLedgerBoard() {
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const [taskDrawerOpen, setTaskDrawerOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<ProductionRun>()
  const [counterTarget, setCounterTarget] = useState<{ run: ProductionRun; log?: ProductionDailyLog }>()
  const [showFinished, setShowFinished] = useState(false)
  const [expandedLogTaskIds, setExpandedLogTaskIds] = useState<Set<number>>(() => new Set())

  const tasksQuery = useQuery({
    queryKey: ['production', 'ledger-tasks'],
    queryFn: async () => {
      const rows = toList(await productionApi.listRuns({ page_size: 1000 }))
      return rows.filter((run) => run.is_ledger_only)
    },
  })

  const resetMutation = useMutation({
    mutationFn: (run: ProductionRun) => productionApi.resetCounter(run.id, '现场确认机台计数器已清零，开始新的累计分段'),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['production'] })
      message.success('已开始新的计数分段，下一条累计读数将从0计算')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const cancelLogMutation = useMutation({
    mutationFn: ({ runId, logId, reason }: { runId: number; logId: number; reason: string }) => productionApi.cancelCounterLog(runId, logId, reason),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
      ])
      message.success('错误交接记录已取消，后续人员模数已自动重算')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const completeTaskMutation = useMutation({
    mutationFn: ({ run, note, confirmBelowTarget }: { run: ProductionRun; note?: string; confirmBelowTarget?: boolean }) => productionApi.completeLedger(run.id, { note, confirm_below_target: confirmBelowTarget }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
      ])
      message.success('当前生产任务已结束，历史模数已计入订单进度')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const completeTask = (run: ProductionRun) => {
    if (run.target_reached) {
      modal.confirm({
        title: '确认结束当前生产任务？',
        content: '任务已达到目标模数。结束后不再新增交接记录；订单仍可建立新的生产段。',
        okText: '确认完成',
        cancelText: '继续生产',
        onOk: () => completeTaskMutation.mutateAsync({ run }),
      })
      return
    }
    let reason = ''
    modal.confirm({
      title: '当前尚未达到目标模数',
      content: <Input.TextArea autoFocus rows={3} placeholder="请填写提前结束原因，例如：暂停换急单，后续将另开生产段" onChange={(event) => { reason = event.target.value }} />,
      okText: '确认提前结束',
      cancelText: '继续生产',
      onOk: () => {
        if (!reason.trim()) {
          message.error('提前结束必须填写原因')
          return Promise.reject(new Error('reason-required'))
        }
        return completeTaskMutation.mutateAsync({ run, note: reason.trim(), confirmBelowTarget: true })
      },
    })
  }

  const askCancelLog = (run: ProductionRun, log: ProductionDailyLog) => {
    let reason = ''
    modal.confirm({
      title: `取消第${log.sequence_no || '-'}条交接记录？`,
      content: <Input.TextArea autoFocus rows={3} placeholder="请填写错误原因（必填）" onChange={(event) => { reason = event.target.value }} />,
      okText: '确认取消并重算',
      okButtonProps: { danger: true },
      cancelText: '保留记录',
      onOk: () => {
        if (!reason.trim()) {
          message.error('请填写取消原因')
          return Promise.reject(new Error('reason-required'))
        }
        return cancelLogMutation.mutateAsync({ runId: run.id, logId: log.id, reason: reason.trim() })
      },
    })
  }

  const tasks = (tasksQuery.data || []).filter((run) => showFinished
    ? ['COMPLETED', 'CANCELLED'].includes(run.status)
    : !['COMPLETED', 'CANCELLED'].includes(run.status))

  return (
    <section className="production-ledger-board">
      <div className="section-heading production-ledger-heading">
        <div><Typography.Title level={3}>生产手工账</Typography.Title><Typography.Text type="secondary">按“机台累计读数”交接，系统自动计算每个人本段模数、订单生产量、欠模和超产；人员、机台、日期可后补。</Typography.Text></div>
        <Space wrap>
          <Button icon={<HistoryOutlined />} onClick={() => setShowFinished((value) => !value)}>{showFinished ? '查看进行中' : '查看已结束'}</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditingTask(undefined); setTaskDrawerOpen(true) }}>新增生产任务</Button>
        </Space>
      </div>
      {tasksQuery.isLoading ? <Card loading /> : tasks.length === 0 ? <Card><Empty description={showFinished ? '暂无已结束的手工账任务' : '暂无进行中的手工账任务'} /></Card> : (
        <div className="production-ledger-grid">
          {tasks.map((run) => {
            const allLogs = (run.daily_logs || []).filter((log) => !log.is_cancelled)
            const logsExpanded = expandedLogTaskIds.has(run.id)
            const logs = (logsExpanded ? allLogs : allLogs.slice(-4)).slice().reverse()
            const percent = Math.max(0, Number(run.progress_percent || 0))
            return (
              <Card key={run.id} className={`production-ledger-card ${run.target_reached ? 'target-reached' : ''}`}>
                <div className="production-ledger-card-heading">
                  <div><Typography.Title level={4}>{run.order_no}{run.order_item_no ? ` / ${run.order_item_no}` : ''}</Typography.Title><Typography.Text>{run.specification} · {run.material || '材质未记录'}</Typography.Text></div>
                  {taskStatus(run)}
                </div>
                <div className="production-ledger-meta"><span>第{run.segment_no || 1}生产段</span><span>{run.station ? `${run.station.code}号机台` : '机台待补'}</span><span>{run.mold ? `${run.mold.asset_code} · ${run.cavities}孔` : `${run.cavities}孔 · 模具待补`}</span></div>
                <Progress percent={Math.round(percent)} status={run.target_reached ? 'success' : 'active'} />
                <div className="production-ledger-numbers"><span><small>累计</small><b>{run.produced_mold_count || 0} 模</b></span><span><small>目标</small><b>{run.planned_mold_count} 模</b></span><span><small>欠模</small><b>{run.remaining_mold_count || 0} 模</b></span><span><small>实际件数</small><b>{run.qualified_production_quantity || 0}</b></span>{Number(run.overproduction_quantity || 0) > 0 && <span className="over"><small>超产</small><b>+{run.overproduction_quantity}</b></span>}</div>
                <Button type="primary" block size="large" disabled={['COMPLETED', 'CANCELLED', 'PAUSED_UNLOADED'].includes(run.status)} onClick={() => setCounterTarget({ run })}>交接录入累计模数</Button>
                <Space className="production-ledger-secondary-actions" wrap>
                  <Button icon={<EditOutlined />} onClick={() => { setEditingTask(run); setTaskDrawerOpen(true) }}>编辑任务</Button>
                  {!['COMPLETED', 'CANCELLED'].includes(run.status) && <><Popconfirm title="确认机台计数已清零？" description="下一次累计读数将从0开始计算，历史记录不会删除。" okText="已清零" cancelText="取消" onConfirm={() => resetMutation.mutate(run)}><Button icon={<ReloadOutlined />}>计数已清零</Button></Popconfirm><Button icon={<CheckCircleOutlined />} onClick={() => completeTask(run)}>结束当前任务</Button></>}
                </Space>
                <div className="production-ledger-log-list">
                  <div className="production-ledger-log-heading">
                    <Typography.Text strong>{logsExpanded ? '全部交接' : '最近交接'}</Typography.Text>
                    {allLogs.length > 4 && <Button type="link" size="small" onClick={() => setExpandedLogTaskIds((current) => {
                      const next = new Set(current)
                      if (next.has(run.id)) next.delete(run.id)
                      else next.add(run.id)
                      return next
                    })}>{logsExpanded ? '收起' : `查看全部 ${allLogs.length} 条`}</Button>}
                  </div>
                  {logs.length ? logs.map((log) => <div key={log.id} className="production-ledger-log-row"><button type="button" onClick={() => setCounterTarget({ run, log })}><span>{log.operator || <Tag color="warning">人员待补</Tag>} · {logDate(log)} · {log.shift === 'NIGHT' ? '夜班' : log.shift === 'DAY' ? '白班' : '班次待补'}</span><b>读数 {log.cumulative_mold_count} · 本段 +{log.produced_mold_count}模</b></button><Button danger type="text" onClick={() => askCancelLog(run, log)}>取消</Button></div>) : <Typography.Text type="secondary">尚未录入交接读数</Typography.Text>}
                </div>
              </Card>
            )
          })}
        </div>
      )}
      <ProductionLedgerTaskDrawer open={taskDrawerOpen} run={editingTask} onClose={() => setTaskDrawerOpen(false)} />
      <ProductionCounterDrawer open={Boolean(counterTarget)} run={counterTarget?.run} log={counterTarget?.log} onClose={() => setCounterTarget(undefined)} />
    </section>
  )
}

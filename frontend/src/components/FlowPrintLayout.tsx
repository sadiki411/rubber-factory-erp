import { App, Button, Select, Space } from 'antd'
import { useEffect, useState } from 'react'
import { flowApi, PAPER_LAYOUT_LABEL, type FlowPrintOrderScope, type PaperLayout } from '../flowcards'

export function FlowPrintLayout({ value, onChange, disabled }: { value: PaperLayout; onChange: (value: PaperLayout) => void; disabled?: boolean }) {
  return <Space wrap>纸张排版<Select<PaperLayout> aria-label="纸张排版" value={value} onChange={onChange} disabled={disabled}
    options={Object.entries(PAPER_LAYOUT_LABEL).map(([value, label]) => ({ value, label }))} /></Space>
}

export function FlowPrintPreview({ ids, layout, second, disabled, scope, includeReprints }: { ids: string[]; layout: PaperLayout; second: boolean; disabled?: boolean; scope?:FlowPrintOrderScope; includeReprints?:boolean }) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<{ key: string; url: string }>()
  const key = JSON.stringify([ids, layout, second, scope, includeReprints])
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview.url) }, [preview])
  const show = async () => {
    setBusy(true)
    try {
      const url = await flowApi.printPreview({ card_ids: ids, paper_layout: layout, second_inspection: second,
        ...(scope ? {order_scope:scope, include_reprints:Boolean(includeReprints)} : {}) })
      setPreview({ key, url })
    } catch (error) { message.error((error as Error).message) }
    finally { setBusy(false) }
  }
  return <Space wrap><Button disabled={disabled || !ids.length} loading={busy} onClick={() => void show()}>预览纸张（不计补打）</Button>
    {preview?.key === key && <Button href={preview.url} target="_blank" rel="noopener noreferrer">打开预览PDF</Button>}</Space>
}

import { EnterOutlined } from '@ant-design/icons'
import { App, Button, Input, Space, Typography } from 'antd'
import { useMemo, useState } from 'react'
import { isLikelyProcessCardNo, normalizeProcessCardQrText } from '../quality'

export interface QualityManualProcessCardInputProps {
  existingValues?: string[]
  disabled?: boolean
  onSubmit: (cardNo: string) => boolean | void | Promise<boolean | void>
}

/**
 * Manual fallback for worn process-card QR labels.
 *
 * The callback is deliberately the same callback used by the camera scanner.
 * This keeps replacement-card, return/rework, duplicate-card and server-side
 * binding rules in one place instead of creating a second business flow.
 */
export function QualityManualProcessCardInput({
  existingValues = [],
  disabled = false,
  onSubmit,
}: QualityManualProcessCardInputProps) {
  const { message } = App.useApp()
  const [value, setValue] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const existingSet = useMemo(
    () => new Set(existingValues.map(normalizeProcessCardQrText).filter(Boolean)),
    [existingValues],
  )

  const submit = async () => {
    const cardNo = normalizeProcessCardQrText(value)
    if (!cardNo) return
    if (!isLikelyProcessCardNo(cardNo)) {
      message.warning('流程卡单号格式不正确，请填写完整单号，例如 04-M003-2608210028。')
      return
    }
    if (existingSet.has(cardNo)) {
      message.info(`流程卡 ${cardNo} 已加入本次记录。`)
      return
    }
    setSubmitting(true)
    try {
      const accepted = await onSubmit(cardNo)
      if (accepted !== false) setValue('')
    } catch (error) {
      message.error((error as Error).message || `流程卡 ${cardNo} 处理失败，请重试。`)
    } finally {
      setSubmitting(false)
    }
  }

  return <div className="quality-manual-process-card-input">
    <div className="quality-manual-process-card-heading">
      <Typography.Text strong>二维码模糊？可直接输入流程卡单号</Typography.Text>
      <Typography.Text type="secondary">保留原扫码功能，手动输入与扫码使用同一套校验。</Typography.Text>
    </div>
    <Space.Compact block>
      <Input
        aria-label="手动输入流程卡单号"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onPressEnter={() => void submit()}
        placeholder="请填写完整流程卡单号"
        autoCapitalize="characters"
        disabled={disabled || submitting}
      />
      <Button
        type="primary"
        icon={<EnterOutlined />}
        loading={submitting}
        disabled={disabled || !value.trim()}
        onClick={() => void submit()}
      >添加流程卡</Button>
    </Space.Compact>
  </div>
}

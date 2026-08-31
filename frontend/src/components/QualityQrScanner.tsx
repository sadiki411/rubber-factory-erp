import {
  BulbOutlined,
  CameraOutlined,
  CheckCircleOutlined,
  CloseOutlined,
  EnterOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { Alert, App, Button, Drawer, Input, Space, Tag, Typography } from 'antd'
import { useCallback, useEffect, useRef, useState } from 'react'
import { isLikelyProcessCardNo, normalizeProcessCardQrText } from '../quality'

type DetectedBarcode = { rawValue?: string }
type BarcodeDetectorLike = { detect: (source: CanvasImageSource) => Promise<DetectedBarcode[]> }
type BarcodeDetectorConstructor = new (options?: { formats?: string[] }) => BarcodeDetectorLike
const EMPTY_SCAN_VALUES: string[] = []

declare global {
  interface Window {
    BarcodeDetector?: BarcodeDetectorConstructor & {
      getSupportedFormats?: () => Promise<string[]>
    }
  }
}

export interface QualityQrScannerProps {
  open: boolean
  title?: string
  description?: string
  continuous?: boolean
  initialValues?: string[]
  onClose: () => void
  /** Return false when the server rejects a scan so the same card can retry. */
  onScan: (cardNo: string) => boolean | void | Promise<boolean | void>
}

function cameraErrorText(error: unknown) {
  const name = String((error as { name?: string })?.name || '')
  if (name === 'NotAllowedError' || name === 'SecurityError') return '相机权限未开启。请允许本网站或“东橡生产助手”使用相机后重试。'
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return '没有找到可用的后置摄像头。'
  if (name === 'NotReadableError') return '相机正被其他应用占用，请关闭其他扫码或拍照应用后重试。'
  return (error as Error)?.message || '相机启动失败，请重试或手动输入流程卡单号。'
}

function scanFeedback(kind: 'success' | 'duplicate' | 'error') {
  if ('vibrate' in navigator) {
    navigator.vibrate(kind === 'success' ? 80 : kind === 'duplicate' ? [60, 45, 60] : [100, 50, 100])
  }
  if (kind !== 'success') return
  try {
    const AudioContextClass = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AudioContextClass) return
    const context = new AudioContextClass()
    const oscillator = context.createOscillator()
    const gain = context.createGain()
    oscillator.frequency.value = 920
    gain.gain.setValueAtTime(0.08, context.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.12)
    oscillator.connect(gain)
    gain.connect(context.destination)
    oscillator.start()
    oscillator.stop(context.currentTime + 0.12)
    oscillator.addEventListener('ended', () => void context.close(), { once: true })
  } catch {
    // Vibration and the visible success counter remain available.
  }
}

/** Full-screen, continuous QR scanner shared by shipment and return entry. */
export function QualityQrScanner({
  open,
  title = '扫描流程卡',
  description = '将流程卡二维码放入取景框，可连续扫描多张。',
  continuous = true,
  initialValues = EMPTY_SCAN_VALUES,
  onClose,
  onScan,
}: QualityQrScannerProps) {
  const { message } = App.useApp()
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | undefined>(undefined)
  const frameRef = useRef<number | undefined>(undefined)
  const cameraSessionRef = useRef(0)
  const lastDetectionRef = useRef(0)
  const handlingRef = useRef(false)
  const scannedRef = useRef(new Set<string>())
  const initialValuesRef = useRef<string[]>([])
  const onCloseRef = useRef(onClose)
  const onScanRef = useRef(onScan)
  const continuousRef = useRef(continuous)
  const messageRef = useRef(message)
  const [manualValue, setManualValue] = useState('')
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [scanned, setScanned] = useState<string[]>([])
  const [torchAvailable, setTorchAvailable] = useState(false)
  const [torchEnabled, setTorchEnabled] = useState(false)

  // Parent forms update after every accepted scan. Keep the latest callbacks
  // and seed values in refs without making those ordinary renders tear down
  // and restart an active phone camera session. This effect is declared before
  // the open/close effect below, so a newly opened session sees fresh seeds.
  useEffect(() => {
    initialValuesRef.current = initialValues.map(normalizeProcessCardQrText).filter(Boolean)
    onCloseRef.current = onClose
    onScanRef.current = onScan
    continuousRef.current = continuous
    messageRef.current = message
  }, [continuous, initialValues, message, onClose, onScan])

  const stopCamera = useCallback(() => {
    cameraSessionRef.current += 1
    if (frameRef.current != null) window.cancelAnimationFrame(frameRef.current)
    frameRef.current = undefined
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = undefined
    if (videoRef.current) videoRef.current.srcObject = null
    handlingRef.current = false
    lastDetectionRef.current = 0
    setStarting(false)
    setTorchAvailable(false)
    setTorchEnabled(false)
  }, [])

  const acceptValue = useCallback(async (rawValue: string) => {
    const cardNo = normalizeProcessCardQrText(rawValue)
    if (!isLikelyProcessCardNo(cardNo)) {
      scanFeedback('error')
      messageRef.current.warning('未识别为流程卡单号，请对准完整二维码或手动核对。')
      return false
    }
    if (scannedRef.current.has(cardNo)) {
      scanFeedback('duplicate')
      messageRef.current.info(`流程卡 ${cardNo} 已扫过，本次未重复加入。`)
      return false
    }
    scannedRef.current.add(cardNo)
    setScanned((values) => [...values, cardNo])
    scanFeedback('success')
    try {
      const accepted = await onScanRef.current(cardNo)
      if (accepted === false) {
        scannedRef.current.delete(cardNo)
        setScanned((values) => values.filter((value) => value !== cardNo))
        return false
      }
      if (!continuousRef.current) onCloseRef.current()
      return true
    } catch (scanError) {
      scannedRef.current.delete(cardNo)
      setScanned((values) => values.filter((value) => value !== cardNo))
      scanFeedback('error')
      messageRef.current.error((scanError as Error).message || `流程卡 ${cardNo} 处理失败`)
      return false
    }
  }, [])

  const startCamera = useCallback(async () => {
    stopCamera()
    const cameraSession = cameraSessionRef.current
    setStarting(true)
    setError('')
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        throw new Error('当前页面无法安全调用相机。请使用 HTTPS 网站或最新版“东橡生产助手”。')
      }
      if (!window.BarcodeDetector) {
        throw new Error('当前浏览器内核不支持实时二维码识别，请更新系统 WebView，或先使用下方手动输入。')
      }
      const supported = await window.BarcodeDetector.getSupportedFormats?.()
      if (supported && !supported.includes('qr_code')) throw new Error('当前设备不支持二维码识别。')
      const detector = new window.BarcodeDetector({ formats: ['qr_code'] })
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
      })
      if (cameraSession !== cameraSessionRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      const video = videoRef.current
      if (!video) {
        stopCamera()
        return
      }
      video.srcObject = stream
      await video.play()
      if (cameraSession !== cameraSessionRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      const track = stream.getVideoTracks()[0]
      const capabilities = track?.getCapabilities?.() as MediaTrackCapabilities & { torch?: boolean }
      setTorchAvailable(Boolean(capabilities?.torch))

      const detectFrame = async (timestamp: number) => {
        if (cameraSession !== cameraSessionRef.current) return
        frameRef.current = window.requestAnimationFrame(detectFrame)
        if (handlingRef.current || timestamp - lastDetectionRef.current < 220 || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return
        lastDetectionRef.current = timestamp
        try {
          const results = await detector.detect(video)
          const rawValue = results.find((result) => result.rawValue)?.rawValue
          if (!rawValue) return
          handlingRef.current = true
          await acceptValue(rawValue)
        } catch {
          // Individual frames can fail while autofocus or orientation changes.
        } finally {
          handlingRef.current = false
        }
      }
      frameRef.current = window.requestAnimationFrame(detectFrame)
    } catch (cameraError) {
      if (cameraSession === cameraSessionRef.current) {
        stopCamera()
        setError(cameraErrorText(cameraError))
      }
    } finally {
      if (cameraSession === cameraSessionRef.current) setStarting(false)
    }
  }, [acceptValue, stopCamera])

  useEffect(() => {
    if (!open) {
      // Camera tracks are an external resource and must be stopped as soon as
      // the scanner closes; stopCamera also clears the torch indicator.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      stopCamera()
      scannedRef.current.clear()
      setScanned([])
      setManualValue('')
      setError('')
      return
    }
    scannedRef.current = new Set(initialValuesRef.current)
    setScanned([...scannedRef.current])
    setManualValue('')
    const timer = window.setTimeout(() => void startCamera(), 120)
    return () => {
      window.clearTimeout(timer)
      stopCamera()
    }
  }, [open, startCamera, stopCamera])

  const toggleTorch = async () => {
    const track = streamRef.current?.getVideoTracks()[0]
    if (!track) return
    const enabled = !torchEnabled
    try {
      await track.applyConstraints({ advanced: [{ torch: enabled } as MediaTrackConstraintSet] })
      setTorchEnabled(enabled)
    } catch {
      message.warning('当前摄像头无法切换闪光灯。')
    }
  }

  const submitManual = async () => {
    const accepted = await acceptValue(manualValue)
    if (accepted) setManualValue('')
  }

  return <Drawer
    open={open}
    onClose={onClose}
    placement="bottom"
    height="100%"
    closable={false}
    className="quality-qr-scanner"
    title={<div className="quality-qr-scanner-title"><div><CameraOutlined /><span>{title}</span></div><Button type="text" icon={<CloseOutlined />} aria-label="关闭扫码" onClick={onClose} /></div>}
    footer={<div className="quality-qr-scanner-footer"><Button block size="large" onClick={onClose}>{scanned.length ? `完成（已扫 ${scanned.length} 张）` : '关闭扫码'}</Button></div>}
  >
    <div className="quality-qr-scanner-body">
      <Typography.Paragraph type="secondary">{description}</Typography.Paragraph>
      <div className="quality-qr-viewport">
        <video ref={videoRef} muted playsInline aria-label="流程卡二维码取景画面" />
        <div className="quality-qr-frame" aria-hidden="true"><span /><span /><span /><span /></div>
        {starting && <div className="quality-qr-overlay">正在启动相机…</div>}
        {error && <div className="quality-qr-overlay is-error"><CameraOutlined /><span>相机暂不可用</span></div>}
      </div>
      {error && <Alert type="warning" showIcon message={error} action={<Button icon={<ReloadOutlined />} onClick={() => void startCamera()}>重试</Button>} />}
      <Space className="quality-qr-actions" wrap>
        {torchAvailable && <Button icon={<BulbOutlined />} type={torchEnabled ? 'primary' : 'default'} onClick={() => void toggleTorch()}>{torchEnabled ? '关闭闪光灯' : '打开闪光灯'}</Button>}
        <Tag color="success" icon={<CheckCircleOutlined />}>本次已扫 {scanned.length} 张</Tag>
      </Space>
      {scanned.length > 0 && <div className="quality-qr-results" aria-label="已扫描流程卡">
        {scanned.slice(-6).reverse().map((cardNo, index) => <div key={cardNo}><CheckCircleOutlined /><strong>{cardNo}</strong>{index === 0 && <Tag color="green">刚刚</Tag>}</div>)}
      </div>}
      <div className="quality-qr-manual">
        <Typography.Text strong>扫码失败时手动输入</Typography.Text>
        <Space.Compact block>
          <Input value={manualValue} onChange={(event) => setManualValue(event.target.value)} onPressEnter={() => void submitManual()} placeholder="流程卡完整单号，如 04-M003-2608210028" autoCapitalize="characters" />
          <Button type="primary" icon={<EnterOutlined />} disabled={!manualValue.trim()} onClick={() => void submitManual()}>加入</Button>
        </Space.Compact>
      </div>
    </div>
  </Drawer>
}

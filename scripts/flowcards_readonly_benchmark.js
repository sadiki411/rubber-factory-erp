/* Paste into the console of a logged-in ERP test page. GET ONLY.
 * Then: await measureFlowCardReads('ERPFC:<isolated-card-uuid>', 20)
 * This measures request/response only, NOT camera recognition or page painting.
 * No credentials, business payloads, personnel or raw pictures are printed.
 */
window.measureFlowCardReads = async function measureFlowCardReads(code, count = 20) {
  if (!/^(ERPFC:|FC-)/i.test(String(code).trim())) throw new Error('请用明确的内部测试卡号，不接受客户卡/库位码')
  if (!Number.isInteger(count) || count < 1 || count > 50) throw new Error('样本数须为1至50')
  if (window.__flowReadBenchRunning) throw new Error('上一轮测速尚未结束，请勿并行运行')
  window.__flowReadBenchRunning = true
  const samples = []
  try {
    for (let i = 0; i < count; i += 1) {
      const start = performance.now()
      try {
        const response = await fetch(`/api/flow-cards/cards/scan/?code=${encodeURIComponent(String(code).trim())}`, { method: 'GET', credentials: 'same-origin', cache: 'no-store' })
        const raw = await response.text()
        samples.push({ sample: i + 1, ms: +(performance.now() - start).toFixed(1), status: response.status, bytes: new TextEncoder().encode(raw).length, ok: response.ok })
      } catch {
        samples.push({ sample: i + 1, ms: +(performance.now() - start).toFixed(1), status: 'NETWORK_ERROR', bytes: 0, ok: false })
      }
      await new Promise((resolve) => setTimeout(resolve, 200))
    }
    const good = samples.filter((s) => s.ok).map((s) => s.ms).sort((a, b) => a - b)
    const percentile = (p) => good.length ? good[Math.max(0, Math.ceil(good.length * p) - 1)] : null
    const report = { measured_at: new Date().toISOString(), environment: navigator.userAgent, samples: count, successful: good.length,
      error_rate: (count - good.length) / count, p50_ms: percentile(0.5), p95_ms: percentile(0.95), max_bytes: Math.max(...samples.map((s) => s.bytes)),
      meaning: 'GET网络往返；SQL数未知；不含相机识别、详情读取和绘制；不能单独证明扫码2秒达标', details: samples }
    console.table(samples)
    console.info('内部卡只读测速结果（无业务明细）', report)
    return report
  } finally { window.__flowReadBenchRunning = false }
}

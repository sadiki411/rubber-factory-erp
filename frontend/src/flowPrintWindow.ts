import type { PrintJob } from './flowcards'
import worker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

export function inlinePrintPdf(job: PrintJob) {
  const url = new URL(job.pdf_url, window.location.origin)
  if (url.origin !== window.location.origin) throw new Error('打印文件地址不属于 ERP。')
  url.searchParams.set('inline', '1')
  return url.href
}

// Don't offer to print the entire frozen file automatically when it contains
// cards outside the action's scope or cards already confirmed/cancelled.
export function canPrintWholeJob(job: PrintJob, cardId?: string, groupId?: string) {
  return job.file_prepared && job.items.length > 0 && job.items.every(
    i => i.active && (!cardId || i.card_id === cardId) && (!groupId || i.group_id === groupId),
  )
}

export type FlowPrintWindow = { show: (job: PrintJob) => void; close: () => void }

export function supportsDesktopFlowPrint() {
  return !/Android|iPhone|iPad|iPod|DongXiangERP/i.test(navigator.userAgent) &&
    !(navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
}

export async function withFlowPrintWindow(load: () => Promise<PrintJob>, cardId?: string, groupId?: string) {
  const popup = openFlowPrintWindow()
  try {
    const job = await load()
    if (canPrintWholeJob(job,cardId,groupId)) popup?.show(job)
    else popup?.close()
    return job
  } catch(error) {
    popup?.close()
    throw error
  }
}

// Must be called within the click, before saving/generating/fetching a job.
export function openFlowPrintWindow(): FlowPrintWindow | undefined {
  if (!supportsDesktopFlowPrint()) return
  const popup = window.open('', '_blank', 'popup,width=850,height=900')
  if (!popup) return
  popup.opener = null
  const doc = popup.document
  doc.title = '工序卡打印'
  doc.documentElement.lang = 'zh-CN'
  const style = doc.createElement('style')
  style.textContent = `body{margin:0;background:#eef2f5;color:#1c2530;font:16px/1.5 system-ui,sans-serif}
    header{padding:16px;background:white;position:sticky;top:0} a,button{margin-right:18px;font:inherit}
    #pages img{display:block;max-width:100%;height:auto;margin:12px auto;background:white}
    @media print{html,body{margin:0;padding:0;background:white}header{display:none}
    #pages img{margin:0;max-width:none;break-inside:avoid}
    #pages img:not(:last-child){break-after:page}}`
  doc.head.append(style)
  const panel = doc.createElement('header')
  const status = doc.createElement('p')
  status.textContent = '正在准备打印文件…'
  panel.append(status)
  const pages = doc.createElement('main')
  pages.id = 'pages'
  doc.body.append(panel, pages)
  return {
    close: () => popup.close(),
    show: job => {
      if (popup.closed) return
      if (!canPrintWholeJob(job)) { popup.close(); return }
      const pdfUrl = inlinePrintPdf(job)
      const pdfLink = doc.createElement('a')
      pdfLink.href = pdfUrl
      pdfLink.target = '_blank'
      pdfLink.rel = 'noopener'
      pdfLink.textContent = '用浏览器原 PDF 打印'
      panel.append(pdfLink)
      void renderForPrint(pdfUrl, doc, pages, popup).then(ready=>{
        if (!ready || popup.closed) return
        const button = doc.createElement('button')
        button.textContent = '打开打印对话框'
        const print = () => {
          popup.focus()
          popup.print()
        }
        button.onclick = print
        panel.insertBefore(button, pdfLink)
        status.textContent = '按实际大小（100%）打印，关闭页眉页脚。只有确认实际出纸后，才回 ERP 确认打印成功。'
        try { print() } catch { status.textContent = '浏览器未自动打开打印窗口，可点下方按钮，或打开原 PDF 打印。' }
      }).catch(()=>{
        if (popup.closed) return
        pages.replaceChildren()
        status.textContent = '本次无法直接调出打印对话框（文件过大或浏览器不支持）。请用下方原 PDF 打印；尚未登记打印结果。'
      })
    },
  }
}

async function renderForPrint(url: string, doc: Document, pages: HTMLElement, popup: Window) {
  const { getDocument, GlobalWorkerOptions } = await import('pdfjs-dist')
  if (popup.closed) return false
  GlobalWorkerOptions.workerSrc = worker
  // The canvas belongs to the print window; PDF.js must load the embedded
  // Chinese fonts there, not in the opener's document.
  const task = getDocument({ url, ownerDocument: doc, stopAtErrors: true })
  const imageUrls: string[] = []
  const release = () => { imageUrls.splice(0).forEach(url=>URL.revokeObjectURL(url)) }
  popup.addEventListener('pagehide',release,{once:true})
  try {
    const pdf = await task.promise
    // Bound desktop memory. Large print jobs still use the native vector PDF.
    if (pdf.numPages > 16) throw new Error('Too many pages for browser raster printing')
    let width = 0, height = 0
    const printStyle = doc.createElement('style')
    doc.head.append(printStyle)
    for (let index = 1; index <= pdf.numPages; index++) {
      if (popup.closed) { release(); return false }
      const page = await pdf.getPage(index)
      const points = page.getViewport({scale: 1})
      if (!width) {
        ({width,height} = points)
        // Small tolerance for browser subpixel rounding at page boundaries.
        printStyle.textContent = `@page{size:${width}pt ${height}pt;margin:0}
          @media print{#pages img{width:${width - 0.4}pt;height:${height - 0.4}pt}}`
      } else if (Math.abs(width-points.width)>0.01 || Math.abs(height-points.height)>0.01) {
        throw new Error('Mixed page sizes; use native PDF')
      }
      const viewport = page.getViewport({scale: 200 / 72})
      const canvas = doc.createElement('canvas')
      canvas.width = Math.ceil(viewport.width)
      canvas.height = Math.ceil(viewport.height)
      try {
        await page.render({canvas, viewport}).promise
        const blob = await new Promise<Blob|null>(resolve=>canvas.toBlob(resolve,'image/png'))
        if (!blob) throw new Error('Page image unavailable')
        if (popup.closed) { release(); return false }
        const src = URL.createObjectURL(blob)
        imageUrls.push(src)
        const image = doc.createElement('img')
        image.alt = `工序卡打印第 ${index} 页`
        image.src = src
        image.width = Math.round(points.width * 96 / 72)
        await image.decode()
        pages.append(image)
      } finally {
        canvas.width = canvas.height = 0
        page.cleanup()
      }
    }
    return true
  } catch (error) {
    release()
    throw error
  } finally {
    await task.destroy().catch(()=>{})
  }
}

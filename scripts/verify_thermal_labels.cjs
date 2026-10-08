/* Render the REAL shared React label + production CSS in Chromium print
 * media, then assert geometry and PDF pagination. No printer is contacted.
 * Optional QA deps: playwright, pdf-lib; set ERP_QA_NODE_MODULES if bundled.
 * Run: node scripts/verify_thermal_labels.cjs
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')
const { Module } = require('node:module')

const root = path.resolve(__dirname, '..')
const frontendRequire = createRequire(path.join(root, 'frontend/package.json'))
const qaRequire = (name) => process.env.ERP_QA_NODE_MODULES
  ? require(path.join(process.env.ERP_QA_NODE_MODULES, name)) : require(name)
const { chromium } = qaRequire('playwright')
const { PDFDocument } = qaRequire('pdf-lib')
const { buildSync } = frontendRequire('esbuild')
const compiled = buildSync({
  stdin: {
    contents: `import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server';
      import {ThermalLocationLabel} from './components/ThermalLocationLabel';
      export const renderLabels = (kind, labels) => renderToStaticMarkup(<div className={kind+'-label-sheet print-thermal'}>{labels.map((code,i)=><ThermalLocationLabel key={i} kind={kind} code={code} detailUrl={kind==='inventory' ? 'https://erp.qfylagent.org/inventory/locations/'+code : 'https://erp.qfylagent.org/mold-rack/slots/'+(i+1)} />)}</div>);`,
    resolveDir: path.join(root, 'frontend/src'), loader: 'tsx',
  }, bundle: true, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic', write: false,
}).outputFiles[0].text
const rendererFilename = path.join(root, 'frontend/thermal-label-qa.cjs')
const renderer = new Module(rendererFilename)
renderer.filename = rendererFilename
renderer.paths = Module._nodeModulePaths(path.join(root, 'frontend'))
renderer._compile(compiled, rendererFilename)
const css = fs.readFileSync(path.join(root, 'frontend/src/styles.css'), 'utf8')

async function main() {
  const browser = await chromium.launch({ channel: process.env.ERP_QA_BROWSER_CHANNEL || 'msedge', headless: true })
  const page = await browser.newPage()
  await page.emulateMedia({ media: 'print' })
  const outputs = path.join(root, 'output/pdf')
  fs.mkdirSync(outputs, { recursive: true })
  const results = []
  try {
    for (const viewport of [1280, 390]) {
      await page.setViewportSize({ width: viewport, height: 900 })
    for (const kind of ['mold-rack', 'inventory']) {
      for (const count of [1, 2, 4, 6]) {
        const labels = Array.from({ length: count }, (_, i) => kind === 'inventory'
          ? `K01-L01-P${String(i + 1).padStart(2, '0')}`
          : `J01-06-L01-${i < 2 ? 'A' : 'B'}-P${String(i % 2 + 1).padStart(2, '0')}${i === 5 ? '-02' : ''}`)
        const body = `<div id="root"><section class="ant-layout app-layout"><aside class="app-sider">SIDER</aside><section class="ant-layout"><header class="app-header">HEADER</header><main class="app-content"><div class="ant-alert">READ ONLY BANNER</div><div class="page-container ${kind === 'inventory' ? 'inventory-page' : 'mold-rack-page'}"><div>NOT A LABEL</div>${renderer.exports.renderLabels(kind, labels)}</div></main></section></section></div>`
        await page.setContent(`<!doctype html><html><head><style>${css}</style><style>@page { size: 70mm 50mm; margin: 0; }</style></head><body>${body}</body></html>`)
        await page.evaluate(() => document.fonts.ready)
        const geometry = await page.locator('.thermal-label-row').evaluateAll((rows) => rows.map((row) => {
          const r = row.getBoundingClientRect()
          const content = row.querySelector('.thermal-label-content').getBoundingClientRect()
          const text = row.querySelector('.thermal-location-code text')
          const svg = row.querySelector('.thermal-location-code')
          const bbox = text.getBBox()
          const code = svg.getBoundingClientRect()
          const qr = row.querySelector('.ant-qrcode').getBoundingClientRect()
          const qrSvg = (row.querySelector('.ant-qrcode > svg') || row.querySelector('.ant-qrcode')).getBoundingClientRect()
          return { x: r.x, width: r.width, height: r.height,
            y: r.y,
            left: content.x-r.x, right: r.right-content.right, top: content.y-r.y, bottom: r.bottom-content.bottom,
            textLeft: bbox.x, textRight: bbox.x+bbox.width, textCenter: bbox.x+bbox.width/2,
            textTop: bbox.y, textBottom: bbox.y+bbox.height,
            qrBottom: qr.bottom-r.y, codeBottom: code.bottom-r.y,
            qrInnerWidth: qrSvg.width, qrInnerHeight: qrSvg.height,
            qrInnerBottom: qrSvg.bottom-r.y, codeTop: code.top-r.y }
        }))
        const mm = 96 / 25.4
        for (const [index, g] of geometry.entries()) {
          assert.ok(Math.abs(g.x) < 0.1, `${kind}: inherited horizontal page offset`)
          assert.ok(Math.abs(g.y-index*50*mm) < 0.2, `${kind}: inherited vertical page offset`)
          assert.ok(Math.abs(g.width - 70*mm) < 0.1 && Math.abs(g.height - 50*mm) < 0.1)
          assert.ok(Math.abs(g.left-g.right) < 0.1 && Math.abs(g.top-g.bottom) < 0.1, `${kind}: artwork is not centered`)
          assert.ok(g.textLeft >= 0 && g.textRight <= 660 && Math.abs(g.textCenter-330) < 0.1, `${kind}: location code clipped/off-center`)
          assert.ok(g.textTop >= 0 && g.textBottom <= 120, `${kind}: location code vertically clipped`)
          assert.ok(g.qrBottom < 48*mm && g.codeBottom < 49*mm, `${kind}: artwork crosses label boundary`)
          assert.ok(Math.abs(g.qrInnerWidth-30*mm) < 0.1 && Math.abs(g.qrInnerHeight-30*mm) < 0.1, `${kind}: inner QR SVG does not match its physical size`)
          assert.ok(g.qrInnerBottom+1*mm <= g.codeTop, `${kind}: QR SVG overlaps the location code`)
        }
        const pdf = await page.pdf({ preferCSSPageSize: true, printBackground: true, displayHeaderFooter: false })
        const parsed = await PDFDocument.load(pdf)
        assert.equal(parsed.getPageCount(), count, `${kind}: blank or missing PDF pages`)
        for (const p of parsed.getPages()) {
          const { width, height } = p.getSize()
          assert.ok(Math.abs(width - 70*72/25.4) < 1 && Math.abs(height - 50*72/25.4) < 1, 'wrong PDF media size')
        }
        if (count === 4) fs.writeFileSync(path.join(outputs, `${kind}-labels-70x50.pdf`), pdf)
        results.push({ viewport, kind, labels: count, pdf_pages: parsed.getPageCount(), full_codes: labels, geometry })
      }
    }
    }
    const report = path.join(root, 'outputs/thermal-label-qa.json')
    fs.mkdirSync(path.dirname(report), { recursive: true })
    fs.writeFileSync(report, JSON.stringify(results, null, 2))
    console.log(JSON.stringify(results.map(({ viewport, kind, labels, pdf_pages }) => ({ viewport, kind, labels, pdf_pages, centered: true, complete_codes: true })), null, 2))
  } finally { await browser.close() }
}
main().catch((error) => { console.error(error); process.exitCode = 1 })

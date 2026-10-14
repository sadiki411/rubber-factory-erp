/* Isolated browser acceptance; all API requests are intercepted with synthetic
 * data. Refuses remote URLs. No production credentials or business writes.
 * ERP_UI_BASE=http://127.0.0.1:4183 ERP_PLAYWRIGHT_MODULE=<module> node this-file
 */
const { chromium } = require(process.env.ERP_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.ERP_UI_BASE || 'http://127.0.0.1:4183';
assert.ok(/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base), 'Only a local preview is allowed');
const out = path.resolve(__dirname, '../outputs');
fs.mkdirSync(out, { recursive: true });
const pause = () => { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; };
const employee = { id: 1, employee_no: 'DEMO-Q1', name: '示例品检甲', role: 'INSPECTOR', is_active: true };
const candidate = { key: 'weighted:501', source_type: 'WEIGHTED', shipment_batch_id: 501,
  shipment_no: 'DEMO-QS', order_no: 'DEMO-ORDER', item_no: '01', product_name: '示例圈',
  specification: '240', material: 'N7200', single_batch_net_weight_kg: '1.630',
  pieces_per_batch: 11104, total_batches: 2, available_batches: 2, available_batch_numbers: [1, 2],
  returned_batches: 0, rework_count: 0, next_return_no: 1, inspectors: [employee], lines: [] };
const allocations = [{ order_id: 121, order_no: 'DEMO-A', item_no: '89', piece_quantity: 10417, net_weight_kg: '1.529' },
  { order_id: 114, order_no: 'DEMO-B', item_no: '8', piece_quantity: 687, net_weight_kg: '0.101' }];
function percentile(values, p) { return [...values].sort((a, b) => a-b)[Math.ceil(values.length*p)-1]; }
(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.ERP_TEST_BROWSER || 'msedge' });
  const results = [];
  try {
    for (const width of [320, 390, 1280]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const page = await context.newPage();
      const errors = [], writes = [];
      page.on('pageerror', error => errors.push(error.message));
      let mode = 'pending', cardGate = pause(), batchGate = pause(), returnGate = pause();
      let previewGate, scanGate, slowGate, slowSeen, fastSeen, failedCode = '';
      await page.route('**/api/**', async route => {
        const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
        if (request.method() !== 'GET') {
          // Deliberately fail any attempted write: queue blocking checks must
          // prevent POSTs before completed validation, not merely mock success.
          writes.push(pathname);
          return route.fulfill({ status: 418, contentType: 'application/json', body: '{"detail":"Unexpected isolated write"}' });
        }
        let data = { count: 0, results: [] };
        if (pathname === '/api/auth/session/') data = { authenticated: true, user: { id: 1, username: 'isolated-demo', is_staff: true, is_superuser: true, is_read_only: false } };
        else if (pathname === '/api/quality/employees/') data = [employee];
        else if (pathname === '/api/quality/return-reasons/') data = [{ id: 1, name: '拔破', is_active: true }];
        else if (pathname === '/api/quality/process-cards/') {
          if (mode === 'pending') await cardGate.promise;
          if (mode === 'failure') return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"隔离流程卡失败"}' });
          if (mode === 'filters' && url.searchParams.get('q') === 'slow') { slowSeen.release(); await slowGate.promise; data = { count: 1, results: [{ id: 1, card_no: 'STALE-SLOW', status: 'READY', quantity: 1 }] }; }
          if (mode === 'filters' && url.searchParams.get('q') === 'fast') fastSeen.release();
        } else if (pathname === '/api/quality/shipment-batches/') {
          if (mode === 'pending') await batchGate.promise;
          if (mode === 'failure') return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"隔离批次失败"}' });
        } else if (pathname === '/api/quality/rework-cases/') {
          if (mode === 'pending') await returnGate.promise;
          if (mode === 'failure') return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"隔离返工失败"}' });
        } else if (pathname.endsWith('/returnable-batches/') || pathname.endsWith('/returnable/')) data = [candidate];
        else if (pathname.endsWith('/return-allocation-preview/')) {
          if (url.searchParams.get('shipment_unit_no') === '2' && previewGate) await previewGate.promise;
          data = { order_allocations: url.searchParams.get('shipment_unit_no') === '1' ? allocations : [{ order_id: 1, order_no: 'DEMO-UNIT-2', item_no: '2', piece_quantity: 1000, net_weight_kg: '0.147' }] };
        } else if (pathname === '/api/quality/process-cards/scan/') {
          if (scanGate) await scanGate.promise;
          const code = url.searchParams.get('code');
          if (code === failedCode) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"隔离卡查询失败"}' });
          data = { found: true, scanned_card: { id: code, card_no: code, order_id: 1,
            unit_binding: { shipment_batch_id: Number(code.slice(-4)), shipment_unit_no: 1,
              shipment_no: `DEMO-${code}`, piece_quantity: 11104, net_weight_kg: '1.630', inspectors: [employee], order_allocations: allocations } } };
        } else if (pathname.endsWith('/summary/')) data = {};
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
      });
      await page.goto(`${base}/quality`);
      await page.getByText('正在读取流程卡，请稍候…', { exact: true }).waitFor();
      await page.getByRole('tab', { name: '重量出货批次（查询中）' }).waitFor();
      await page.getByRole('tab', { name: '退货返工记录（查询中）' }).waitFor();
      assert.equal(await page.getByText('当前条件下暂无可用流程卡', { exact: true }).count(), 0);
      cardGate.release();
      await page.getByText('当前条件下暂无可用流程卡', { exact: true }).waitFor();
      assert.ok(await page.locator('.quality-workflow-kpi').filter({ hasText: '待处理返工' }).locator('.ant-skeleton').count() > 0);
      batchGate.release(); returnGate.release();
      await page.getByRole('tab', { name: /重量出货批次（0项·当前列表 \/ 接口共0项）/ }).waitFor();
      mode = 'failure';
      await page.goto(`${base}/quality?q=failure`);
      await page.getByText('流程卡读取失败', { exact: true }).waitFor();
      await page.getByRole('tab', { name: '重量出货批次（读取失败）' }).click();
      await page.getByText('重量出货批次读取失败', { exact: true }).waitFor();
      await page.getByRole('tab', { name: '退货返工记录（读取失败）' }).click();
      await page.getByText('退货返工记录读取失败', { exact: true }).waitFor();
      mode = 'filters'; slowGate = pause(); slowSeen = pause(); fastSeen = pause();
      await page.goto(`${base}/quality`);
      const search = page.getByPlaceholder('搜索出货单、订单、产品、规格、材质或品检员');
      await search.fill('slow'); await slowSeen.promise;
      await search.fill('fast'); await fastSeen.promise;
      slowGate.release(); await page.waitForTimeout(300);
      assert.equal(await page.getByText('STALE-SLOW', { exact: true }).count(), 0);
      mode = 'normal';
      await page.goto(`${base}/quality`);
      await page.getByRole('tab', { name: /退货返工记录/ }).click();
      await page.getByRole('button', { name: '登记客户整批退货' }).click();
      await page.waitForTimeout(600);
      await page.screenshot({ path: path.join(out, `a-ui-preview-${width}.png`), fullPage: true });
      await page.getByRole('button', { name: '选择一整批退货' }).click();
      await page.getByText(/合计：11,104 件 \/ 1.630 kg/).waitFor();
      previewGate = pause();
      await page.getByLabel('本次退回的物理批号').click();
      await page.getByText('物理批号 2（本组共2批）', { exact: true }).click();
      await page.getByText('本次整批回冲预览 · 物理批号 1', { exact: true }).waitFor({ state: 'hidden' });
      assert.equal(await page.getByText('10,417 件', { exact: true }).count(), 0);
      previewGate.release(); previewGate = null;
      await page.getByText(/DEMO-UNIT-2 \/ 2/).waitFor();
      await page.getByRole('button', { name: /^取\s*消$/ }).last().click();
      await page.getByRole('dialog', { name: '登记客户整批退货返工' }).waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: /扫描登记退货|扫码登记退货/ }).first().click();
      await page.getByRole('button', { name: '关闭扫码', exact: true }).first().click();
      scanGate = pause(); failedCode = '04-M003-2610130001';
      const samples = [];
      for (let i = 1; i <= 100; i++) {
        const code = `04-M003-261013${String(i).padStart(4,'0')}`;
        await page.getByLabel('手动输入流程卡单号', { exact: true }).fill(code);
        const started = performance.now();
        await page.getByRole('button', { name: /添加流程卡/ }).click();
        await page.getByText(code, { exact: true }).first().waitFor();
        samples.push(performance.now()-started);
        if (i === 20 || i === 50) assert.ok(await page.getByRole('button', { name: /确认登记/ }).isDisabled());
      }
      scanGate.release(); scanGate = null;
      await page.getByText('部分流程卡核对失败', { exact: true }).waitFor();
      assert.ok(await page.getByRole('button', { name: /确认登记/ }).isDisabled());
      assert.equal(writes.length, 0);
      const geometry = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }));
      assert.ok(geometry.content <= geometry.width + 1, JSON.stringify(geometry));
      assert.deepEqual(errors, []);
      await page.screenshot({ path: path.join(out, `a-ui-${width}.png`), fullPage: true });
      results.push({ width, a2_independent_slow_failure_empty_stale_filter: true, a3_unit_switch: true,
        queue_feedback: { samples: samples.length, p50_ms: percentile(samples,.5), p95_ms: percentile(samples,.95), max_ms: Math.max(...samples) },
        pending_blocks_at_20_50: true, off_page_failed_card_blocks: true, unexpected_write_requests: writes.length });
      await context.close();
    }
    console.log(JSON.stringify({ results, environment: 'Edge headless, synthetic intercepted API',
      feedback_measurement: 'manual recognised-code handler + browser automation overhead; not camera or real mobile',
      camera: 'NOT_TESTED', android: 'NOT_TESTED', printer: 'NOT_TESTED' }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });

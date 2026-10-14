const { chromium } = require(process.env.ERP_PLAYWRIGHT_MODULE || 'playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const result = [];
  try {
    for (const width of [320,390,1280]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [], remoteRequests = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => { if (/^https?:/.test(request.url())) remoteRequests.push(request.url()); });
      await page.goto(pathToFileURL(path.resolve(__dirname,'../docs/prototypes/erp-next-stage.html')).href);
      await page.getByLabel('事项类别').selectOption('shortage');
      await page.getByRole('button',{ name:'打开订单完整查看' }).click();
      await page.getByText('6,000 件',{ exact:true }).waitFor();
      await page.getByRole('button',{ name:'查看原出货与整批退回' }).click();
      await page.getByText(/仅退物理批号1，3,000件/).waitFor();
      await page.getByRole('button',{ name:/返回待办/ }).click();
      assert.equal(await page.getByLabel('事项类别').inputValue(),'shortage');
      await page.getByRole('button',{ name:'B2 真实工序' }).click();
      await page.getByLabel('实际处理人员').fill('合成作业甲');
      await page.getByLabel('本次实际处理件数').fill('800');
      await page.getByRole('button',{ name:'预览本次事件与联动' }).click();
      await page.getByText(/未处理余量候选：2200件/).waitFor();
      await page.getByRole('button',{ name:'B3 产品经验' }).click();
      await page.getByLabel(/模拟确认/).check();
      await page.getByText(/演示：此条经验已确认/).waitFor();
      await page.getByRole('button',{ name:'B4 状态与扫码' }).click();
      await page.getByLabel('演示码类型').selectOption('mismatch');
      await page.getByText(/明确提示对象不匹配/).waitFor();
      const geometry = await page.evaluate(()=>({width:innerWidth,content:document.documentElement.scrollWidth}));
      assert.ok(geometry.content<=width+1,JSON.stringify(geometry));
      assert.deepEqual(errors,[]);assert.deepEqual(remoteRequests,[]);
      await page.screenshot({path:path.resolve(__dirname,`../outputs/b-prototype-${width}.png`),fullPage:true});
      result.push({width,interactions_pass:true,network_requests:0,overflow:false});
      await page.close();
    }
    console.log(JSON.stringify({result,business_implementation:false,synthetic_only:true},null,2));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});

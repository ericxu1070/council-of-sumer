const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');

// Optional browser smoke check: install Playwright separately, then run this file
// with its package on NODE_PATH. Uses local fixtures, with no model or API calls.
(async () => {
  const files = new Set(['index.html','app.css','app.js','graph.js','faces.js','sumer-faces.js','city.js','sumer.css']);
  const server = http.createServer(async (req, res) => {
    const file = req.url === '/' ? 'index.html' : req.url.slice(1);
    if (!files.has(file)) { res.writeHead(404).end(); return; }
    try {
      const data = await fs.readFile(path.join(__dirname, '../web', file));
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
      res.end(data);
    } catch { res.writeHead(500).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  const output = process.env.SUMER_UI_SCREENSHOTS || path.join(os.tmpdir(), 'sumer-city-ui');
  await fs.mkdir(output, {recursive:true});
  let browser;
  try {
    browser = await chromium.launch({headless:true, ...(process.env.SUMER_BROWSER ? {executablePath:process.env.SUMER_BROWSER} : {})});
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];
    page.on('pageerror', e=>errors.push(e.message));
    await page.route('**/api/meta', route=>route.fulfill({json:{transport:'inproc',llm:'llm',years:5,advisors:[
      {id:'provider',short:'Provider',color:'#5f8f3a',emoji:'🍞'},
      {id:'farmer',short:'Farmer',color:'#7a4fa3',emoji:'🌾'},
      {id:'merchant',short:'Merchant',color:'#a33a32',emoji:'⚖️'},
      {id:'profiteer',short:'Profiteer',color:'#b5862a',emoji:'💰'},
      {id:'imposter',short:'Imposter',color:'#b2622f',emoji:'🎭'},
    ]}}));
    await page.goto(url);
    await page.waitForFunction(()=>document.querySelectorAll('.cg-node').length===5);
    assert.equal(await page.locator('#city-value-acres').textContent(),'—');
    await page.evaluate(()=>recordState(0,{population:100,grain:2800,acres:1000}));
    assert.equal(await page.locator('#city-value-acres').textContent(),'1,000 acres');
    assert.equal(await page.locator('#city-value-grain').textContent(),'2,800 bushels');
    assert.equal(await page.locator('#city-value-population').textContent(),'100 citizens');
    assert.equal(await page.locator('#city-acres use[opacity="1"]').count(),10);
    assert.equal(await page.locator('#city-grain use[opacity="1"]').count(),14);
    await page.waitForTimeout(800);
    await page.evaluate(()=>{
      render("game",{advisors:S.advisors,years:5,state:{population:100,grain:2800,acres:1000}},false);
      render("year_start",{year:1,state:{population:100,grain:2800,acres:1000}},false);
    });
    const turn={advisor:'provider',speech:'Farmer, I support your plan. Merchant, I cannot support yours.',round:1,year:1,plan_id:'farmer-y1-r1',backed:true,addressed:['farmer','merchant'],accept:['farmer-y1-r1'],support:[{id:'farmer-y1-r1',author:'farmer'},{id:'merchant-y1-r1',author:'merchant'}]};
    await page.evaluate(turn=>renderTurn(turn,false),turn);
    assert.equal(await page.locator('.cg-relation.agree').count(),1);
    assert.equal(await page.locator('.cg-relation.disagree').count(),1);
    assert.equal(await page.evaluate(()=>planAuthor('farmer-y1-r1')),'farmer');
    assert.match(await page.locator('.cg-relation.agree title').textContent(),/Provider backs Farmer/);
    await page.waitForTimeout(200);
    await page.screenshot({path:path.join(output,'desktop.png')});
    await page.waitForTimeout(1250);
    assert.equal(await page.locator('.cg-relation').count(),0);
    await page.evaluate(turn=>renderTurn(turn,true),turn);
    assert.equal(await page.locator('.cg-relation').count(),0);
    await page.evaluate(()=>CouncilGraph.flashRelations([
      {from:'provider',to:'provider',agree:true},
      {from:'provider',to:'missing-advisor',agree:false},
    ]));
    assert.equal(await page.locator('.cg-relation').count(),0);
    await page.evaluate(()=>renderPoll({year:1,round:1,rows:[{id:'farmer-y1-r1',author:'farmer',accept:['provider','farmer']}]},false));
    assert.equal(await page.locator('.cg-relation.agree').count(),1);
    assert.equal(await page.locator('.cg-relation.disagree').count(),3);
    await page.evaluate(()=>CouncilGraph.idle());
    assert.equal(await page.locator('.cg-relation').count(),0);
    assert.deepEqual(await page.evaluate(()=>turnRelations({advisor:'provider',addressed:['imposter'],accept:[]})),[]);
    await page.evaluate(()=>{
      recordState(1,{population:5,grain:0,acres:5100});
      S.runoff={a:{id:'farmer-y1-r1',author:'farmer'},b:{id:'merchant-y1-r1',author:'merchant'},votes:{}};
    });
    assert.equal(await page.locator('#city-population use').first().getAttribute('opacity'),'0.675');
    assert.equal(await page.locator('#city-grain use[opacity="1"]').count(),0);
    assert.match(await page.locator('#city-value-acres').evaluate(el=>el.parentElement.title),/4000/);
    assert.deepEqual(await page.evaluate(()=>turnRelations({advisor:'provider',runoff_vote:'farmer-y1-r1',accept:['farmer-y1-r1']})),[
      {from:'provider',to:'farmer',agree:true},{from:'provider',to:'merchant',agree:false}
    ]);
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.evaluate(turn=>renderTurn(turn,false),turn);
    assert.equal(await page.locator('.cg-relation.agree').evaluate(el=>getComputedStyle(el).animationName),'none');
    await page.waitForTimeout(1250);
    assert.equal(await page.locator('.cg-relation').count(),0);
    await page.evaluate(()=>{S.runoff=null; recordState(1,{population:104,grain:2950,acres:1030});});
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>CouncilGraph.idle());
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    for (const count of [1,21]) {
      await page.evaluate(count=>setAdvisors(Array.from({length:count},(_,i)=>({id:`custom-person-${i}`,short:`Agent ${i+1}`,color:'#5f8f3a'}))),count);
      assert.equal(await page.locator('.cg-node:not(.leave)').count(),count);
    }
    assert.deepEqual(errors,[]);
    console.log('PASS: resource units, partial/zero/overflow totals, original plan ownership, green/red direct links, expiry, replay suppression, runoff votes, reduced motion, mobile overflow, 1/5/21 advisor rosters, no browser errors.');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(e=>{console.error(e);process.exit(1)});

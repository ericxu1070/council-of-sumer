const {chromium} = require('playwright');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');

// Devpost gallery thumbnail (3:2, 1500x1000): the real chamber map and council portraits,
// rendered from web/ with fixture data, under a title band. Needs Playwright on NODE_PATH:
//   NODE_PATH=<dir with playwright> node scripts/devpost_thumbnail.cjs
const WEB = path.join(__dirname, '../web');
const OUT = path.join(__dirname, '../docs/devpost-thumbnail.png');
const ADVISORS = [
  {id:'provider',short:'Provider',color:'#5f8f3a',emoji:'🍞'},
  {id:'farmer',short:'Farmer',color:'#7a4fa3',emoji:'🌾'},
  {id:'merchant',short:'Merchant',color:'#a33a32',emoji:'⚖️'},
  {id:'priest',short:'Priest',color:'#b5862a',emoji:'🏺'},
  {id:'villager',short:'Villager',color:'#b2622f',emoji:'🧑'},
];

(async () => {
  const server = http.createServer(async (req, res) => {
    const file = req.url === '/' ? 'index.html' : req.url.slice(1).split('?')[0];
    try {
      const data = await fs.readFile(path.join(WEB, path.normalize(file).replace(/^(\.\.[/\\])+/, '')));
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
      res.end(data);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true, ...(process.env.SUMER_BROWSER ? {executablePath:process.env.SUMER_BROWSER} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1500,height:1000}, deviceScaleFactor:2});
    await page.route('**/api/meta', r => r.fulfill({json:{transport:'inproc',llm:'llm',years:5,advisors:ADVISORS}}));
    await page.route('**/api/clamor', r => r.fulfill({json:{voice:false,voices:[],phrases:{}}}));
    await page.route('**/api/**', r => r.fulfill({status:404}));
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(() => document.querySelectorAll('.cg-node').length === 5);
    await page.evaluate(() => recordState(0, {population:100, grain:2800, acres:1000}));
    await page.addStyleTag({content:'#toast{display:none!important}'});
    await page.waitForTimeout(900);
    // Pass 1: the live council map, exactly as the chamber draws it.
    const map = await page.locator('section.council').screenshot({type:'png'});
    // Pass 2: frame it at 3:2 under a title band, feathering the map into a blurred copy.
    const mapW = Math.round(780 * map.readUInt32BE(16) / map.readUInt32BE(20));
    await page.setContent(`<!doctype html><html><head><style>
      html,body{margin:0;width:1500px;height:1000px;overflow:hidden;background:#14160f}
      .bg,.map{position:absolute;inset:0;background-image:url(data:image/png;base64,${map.toString('base64')});background-repeat:no-repeat}
      .bg{background-size:cover;background-position:center;filter:blur(18px) brightness(.55);transform:scale(1.08)}
      .map{left:50%;right:auto;width:${mapW}px;transform:translateX(-50%);background-size:auto 780px;background-position:center 215px;
        -webkit-mask-image:linear-gradient(90deg,transparent 0,#000 10%,#000 90%,transparent 100%),linear-gradient(180deg,transparent 215px,#000 330px);
        -webkit-mask-composite:source-in;mask-composite:intersect}
      .band,.foot{position:absolute;left:0;right:0;text-align:center;font-family:Georgia,'Times New Roman',serif;color:#f3e3bd}
      .band{top:0;padding:44px 60px 110px;background:linear-gradient(180deg,rgba(20,22,15,.97) 0%,rgba(20,22,15,.9) 60%,rgba(20,22,15,0) 100%)}
      .kicker{font-size:22px;letter-spacing:.32em;text-transform:uppercase;color:#d8b25c;margin-bottom:12px}
      .title{font-size:116px;line-height:1;font-weight:700;letter-spacing:.02em;text-shadow:0 4px 18px rgba(0,0,0,.6)}
      .sub{font-size:36px;margin-top:16px;color:#e9dcc0}
      .sub b,.foot b{color:#f0c869}
      .foot{bottom:0;padding:70px 60px 34px;font-size:34px;color:#e9dcc0;background:linear-gradient(0deg,rgba(20,22,15,.97) 0%,rgba(20,22,15,.88) 55%,rgba(20,22,15,0) 100%)}
      .foot b{font-size:46px}
    </style></head><body>
      <div class="bg"></div><div class="map"></div>
      <div class="band"><div class="kicker">UCSC AI Hackathon &middot; When AI Agents Disagree</div>
        <div class="title">Council of Sumer</div>
        <div class="sub">Five AI advisors run an ancient city. <b>No king breaks the tie.</b></div></div>
      <div class="foot"><b>73%</b> of reigns survived as a council &nbsp;vs&nbsp; <b>56%</b> for one LLM alone</div>
    </body></html>`);
    await page.waitForTimeout(300);
    await page.screenshot({path:OUT});
    console.log(`Wrote ${OUT}`);
  } finally {
    await browser.close();
    server.close();
  }
})().catch(e => { console.error(e); process.exit(1); });

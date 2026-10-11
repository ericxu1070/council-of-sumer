const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');

// Optional browser smoke check: install Playwright separately, then run this file
// with its package on NODE_PATH. Uses local fixtures, with no model or API calls.
(async () => {
  const files = new Set(['index.html','app.css','app.js','graph.js','faces.js','city.js','city-effects.js','city-effects.css','clamor.js','clamor.css']);
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
      {id:'priest',short:'Priest',color:'#b5862a',emoji:'🏺'},
      {id:'villager',short:'Villager',color:'#b2622f',emoji:'🧑'},
    ]}}));
    await page.route('**/api/clamor',route=>route.fulfill({json:{voice:false,voices:['provider','farmer','merchant','priest','villager'],
      phrases:{plant:['Sow every field!','Plant the seed!'],feed:['Feed the people!'],sell_land:['Sell the land!'],keep_land:['Keep our land!'],store:['Store the grain!'],fallow:['Leave fields fallow!']}}}));
    await page.route('**/api/clamor/**',route=>route.fulfill({status:404}));
    await page.goto(url);
    await page.waitForFunction(()=>document.querySelectorAll('.cg-node').length===5);
    await page.evaluate(() => {
      const check = (ok, message) => { if (!ok) throw new Error(message); };
      const original = setup.seats;
      const names = original.map(seat => seat.name);
      check(new Set(names).size === names.length, 'Default councilors must have unique names');
      setup.seats = Array.from({length:21}, (_, i) => newVoter(i));
      setup.seats.forEach(seat => { seat.preset = 'priest'; });
      const priests = configuredAdvisors();
      check(new Set(priests.map(a => a.short)).size === 21, 'Repeated roles must draw unique names');
      check(priests.every(a => a.title === `${a.short} the Priest`), 'Priests must display their name and role');
      check(priests.every(a => !names.includes(a.short)), 'New seats cannot reuse a previous name');
      check(JSON.stringify(configuredAdvisors()) === JSON.stringify(priests), 'Rendering cannot redraw names');
      const first = setup.seats[0];
      first.preset = 'farmer';
      check(configuredAdvisors()[0].short === priests[0].short, 'Changing roles must preserve the name');
      first.preset = 'custom'; first.custom.short = 'Canal Keeper'; first.custom.mandate = 'Protect canals.';
      check(configuredAdvisors()[0].title === `${first.name} the Canal Keeper`, 'Custom roles must receive names');
      const exhausted = Array.from({length:650}, () => drawCouncilName());
      check(new Set(exhausted).size === exhausted.length, 'The extended pool cannot repeat names');
      setup.seats = original;
      renderVoters();
    });
    await page.waitForTimeout(800);
    await page.screenshot({path:path.join(output,'ready-desktop.png')});
    await page.locator('#setupBtn').click();
    await page.waitForTimeout(350);
    await page.screenshot({path:path.join(output,'setup-desktop.png')});
    await page.locator('#drawerClose').click();
    // The remaining event fixtures use role names as their speech/display labels.
    await page.evaluate(() => setAdvisors(S.meta.advisors));
    const checkSeatsClearResources = async () => {
      const overlaps = await page.evaluate(() => {
        const areas = ['city-acres','city-population','city-grain'].map(id=>document.getElementById(id).getBoundingClientRect());
        return [...document.querySelectorAll('.cg-node:not(.leave) .cg-face, .cg-node:not(.leave) .cg-label')]
          .filter(el=>getComputedStyle(el).opacity!=='0')
          .filter(el=>{
            const r=el.getBoundingClientRect();
            return areas.some(a=>r.left<a.right && r.right>a.left && r.top<a.bottom && r.bottom>a.top);
          }).map(el=>el.closest('.cg-node').title);
      });
      assert.deepEqual(overlaps,[], 'Council portraits and names must clear city resources');
    };
    assert.equal(await page.locator('#city-value-acres').textContent(),'—');
    await page.evaluate(()=>recordState(0,{population:100,grain:2800,acres:1000}));
    assert.equal(await page.locator('#city-value-acres').textContent(),'1,000 acres');
    assert.equal(await page.locator('#city-value-grain').textContent(),'2,800 bushels');
    assert.equal(await page.locator('#city-value-population').textContent(),'100 citizens');
    assert.equal(await page.locator('#city-acres use[opacity="1"]').count(),10);
    assert.equal(await page.locator('#city-grain use[opacity="1"]').count(),14);
    await page.waitForTimeout(800);
    await checkSeatsClearResources();
    assert.equal(await page.locator('.cg-spoke').first().evaluate(el=>getComputedStyle(el).display),'none');
    assert.equal(await page.locator('#cityMap text').allTextContents().then(text=>text.includes('N')),false);
    await page.evaluate(()=>{
      render("game",{advisors:S.advisors,years:5,state:{population:100,grain:2800,acres:1000}},false);
      render("year_start",{year:1,state:{population:100,grain:2800,acres:1000}},false);
    });
    const turn={advisor:'provider',speech:'Farmer, I support your plan. Merchant, I cannot support yours.',round:1,year:1,plan_id:'farmer-y1-r1',backed:true,addressed:['farmer','merchant'],accept:['farmer-y1-r1'],support:[{id:'farmer-y1-r1',author:'farmer',plan:{land_delta:0,feed:2000,plant:800},accept:['provider','farmer']},{id:'merchant-y1-r1',author:'merchant',plain:'Buy 20 acres, feed 1,800 bushels, plant 700 acres',accept:['merchant']}]};
    await page.evaluate(turn=>renderTurn(turn,false),turn);
    assert.equal(await page.locator('.river-plan').count(),2);
    assert.equal(await page.locator('.river-plan').first().isVisible(),true);
    assert.match(await page.locator('.river-plan').first().textContent(),/plant 800 acres/);
    assert.deepEqual(await page.locator('.river-plan').first().locator('.river-supporter').evaluateAll(els=>els.map(el=>el.getAttribute('aria-label'))),['Provider','Farmer']);
    assert.deepEqual(await page.locator('.river-supporter').allTextContents(), ['', '', '']);
    assert.equal(await page.locator('.river-supporter img').count(), 3);
    assert.equal(await page.locator('.cg-conversation').count(),2);
    assert.match(await page.locator('.cg-conversation title').first().textContent(),/Provider talks to Farmer/);
    // Votes change without replacing the plan's original author or request.
    await page.evaluate(turn=>renderTurn({...turn,support:turn.support.map(row=>({...row,accept:row.author==='farmer'?['farmer']:['merchant','provider']}))},false),turn);
    assert.deepEqual(await page.locator('.river-plan').last().locator('.river-supporter').evaluateAll(els=>els.map(el=>el.getAttribute('aria-label'))),['Merchant','Provider']);
    assert.match(await page.locator('.river-plan').first().textContent(),/Farmer's plan/);
    await page.evaluate(turn=>renderTurn(turn,false),turn);
    // A failed advisor turn dismisses the stage card without creating empty content.
    for (const instant of [false, true]) {
      await page.evaluate(instant=>{
        spotlight('year', '<div>Previous year</div>');
        S.stance.farmer='agree';
        render('agent_skipped',{advisor:'farmer',attempts:2},instant);
      },instant);
      assert.equal(await page.locator('#spot').evaluate(el=>el.classList.contains('in')),false);
      assert.equal(await page.evaluate(()=>S.stance.farmer),undefined);
      assert.match(await page.locator('#feed').textContent(),/Farmer skipped this turn/);
      assert.doesNotMatch(await page.locator('#spot').textContent(),/undefined/);
      await page.waitForTimeout(400);
      assert.equal(await page.locator('#spot').evaluate(el=>el.classList.contains('hidden')),true);
      assert.equal(await page.locator('#spot').textContent(),'');
      await page.evaluate(turn=>renderTurn(turn,false),turn);
      assert.match(await page.locator('#feed').textContent(),/Farmer, I support your plan/);
    }
    assert.equal(await page.locator('.cg-relation.agree').count(),1);
    assert.equal(await page.locator('.cg-relation.disagree').count(),1);
    assert.equal(await page.evaluate(()=>planAuthor('farmer-y1-r1')),'farmer');
    assert.match(await page.locator('.cg-relation.agree title').textContent(),/Provider backs Farmer/);
    await page.waitForTimeout(200);
    await checkSeatsClearResources();
    await page.waitForTimeout(2000); // Let the year interstitial clear for visual review.
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
    assert.equal(await page.locator('.cg-conversation').count(),0);
    await page.evaluate(()=>CouncilGraph.speak({speaker:'provider',to:['villager'],text:'How are the city homes?',instant:true}));
    assert.equal(await page.locator('.cg-conversation').count(),1, 'Conversation links do not require a proposal or a vote');
    await page.evaluate(()=>CouncilGraph.speak({speaker:'provider',text:'Council, hear me.',instant:true}));
    assert.equal(await page.locator('.cg-conversation').count(),4, 'Broadcasts connect the speaker to every listener');
    await page.evaluate(()=>CouncilGraph.idle());
    assert.deepEqual(await page.evaluate(()=>turnRelations({advisor:'provider',addressed:['imposter'],accept:[]})),[]);
    await page.evaluate(()=>{
      recordState(1,{population:5,grain:0,acres:5100});
      S.runoff={a:{id:'farmer-y1-r1',author:'farmer'},b:{id:'merchant-y1-r1',author:'merchant'},votes:{}};
    });
    assert.equal(await page.locator('#city-population use').first().getAttribute('opacity'),'0.675');
    assert.equal(await page.locator('#city-grain use[opacity="1"]').count(),0);
    assert.match(await page.locator('#city-overflow-acres').textContent(),/4,000/);
    assert.deepEqual(await page.evaluate(()=>turnRelations({advisor:'provider',runoff_vote:'farmer-y1-r1',accept:['farmer-y1-r1']})),[
      {from:'provider',to:'farmer',agree:true},{from:'provider',to:'merchant',agree:false}
    ]);
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.evaluate(turn=>renderTurn(turn,false),turn);
    assert.equal(await page.locator('.cg-relation.agree').evaluate(el=>getComputedStyle(el).animationName),'none');
    await page.waitForTimeout(1250);
    assert.equal(await page.locator('.cg-relation').count(),0);
    await page.evaluate(()=>{S.runoff=null; recordState(1,{population:104,grain:2950,acres:1030});});
    const scout = {request_id:'preview',rows:[
      {id:'farmer-y1-r1',author:'farmer',plan:{land_delta:0,feed:2000,plant:800},accept:['provider','farmer','villager']},
      {id:'merchant-y1-r1',author:'merchant',plain:'Buy 20 acres, feed 1,800 bushels, plant 700 acres',accept:['merchant']},
    ],options:['plague','rats','good_harvest','bad_harvest','low_price','high_price','skip'].map(id=>({id,label:id}))};
    await page.evaluate(scout=>showScout(scout),scout);
    assert.equal(await page.locator('.scout-plans .spot-plan').count(),1);
    assert.match(await page.locator('.scout-plans').textContent(),/Farmer's plan/);
    assert.match(await page.locator('.scout-plans').textContent(),/plant 800 acres/);
    assert.match(await page.locator('.scout-plan-support').textContent(),/3 of 5/);
    assert.equal(await page.locator('#spot [data-scout]').count(),7);
    assert.equal(await page.locator('.scout-mark').count(),6);
    await page.waitForTimeout(500);
    await page.screenshot({path:path.join(output,'omens-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await page.waitForTimeout(700);
    await page.screenshot({path:path.join(output,'omens-mobile.png'),fullPage:true});
    assert.equal(await page.locator('[data-scout="skip"]').isVisible(),true);
    assert.equal(await page.locator('[data-scout="high_price"]').evaluate(el=>{
      const r=el.getBoundingClientRect();
      return el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));
    }),true, 'Omen choices must remain above the mobile transcript');
    await page.evaluate(scout=>showScout({...scout,phase:'runoff',rows:scout.rows.map(row=>({...row,accept:['farmer','merchant']}))}),scout);
    assert.equal(await page.locator('.scout-plans .spot-plan').count(),2);
    assert.match(await page.locator('.scout-plan-heading').textContent(),/tied/);
    await page.evaluate(()=>showScout({request_id:'empty',rows:[],options:[]}));
    assert.equal(await page.locator('.scout-plans').count(),0);
    await page.evaluate(()=>{hideSpot('scout');S.scoutRequest=null;});
    // Clamoring: off by default; once on, advisors who are not holding the floor shout their stance.
    assert.equal(await page.getAttribute('#clamorToggle','aria-pressed'),'false');
    await page.click('#clamorToggle');
    assert.equal(await page.evaluate(()=>localStorage.getItem('sumer-clamor')),'1');
    await page.evaluate(turn=>{
      S.running=true;
      render('turn',{...turn,advisor:'farmer',addressed:[],proposal:{land_delta:0,feed:2000,plant:1000}},false);
      render('turn',{...turn,proposal:{land_delta:-100,feed:2000,plant:500}},false);
    },turn);
    await page.waitForSelector('.cg-shout',{timeout:4000});
    assert.match(await page.locator('.cg-shout').first().textContent(),/^(Sow every field!|Plant the seed!|Feed the people!)$/);
    assert.equal(await page.locator('.cg-node[title="Provider"] .cg-shout').count(),0);
    await page.click('#clamorToggle');
    await page.evaluate(()=>render('year',{year:1,outcome:'consensus',adopted:'safe',plan:{land_delta:0,feed:2000,plant:1000}},true));
    await page.waitForTimeout(2800);
    assert.equal(await page.locator('.cg-shout').count(),0);
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>CouncilGraph.idle());
    await page.waitForTimeout(700);
    await checkSeatsClearResources();
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.locator('#setupBtn').click();
    await page.waitForTimeout(350);
    await page.screenshot({path:path.join(output,'setup-mobile.png'),fullPage:true});
    assert.equal(await page.locator('#seed').isVisible(),true);
    await page.locator('#drawerClose').click();
    await page.setViewportSize({width:1440,height:1000});
    await page.evaluate(()=>showEscalation({reason:'The council cannot agree on how to divide the grain.', options:[
      {id:'farmer-y1-r1',author:'farmer',plain:'Keep our land, feed 2,000 bushels, plant 800 acres',accept:['farmer','provider']},
      {id:'merchant-y1-r1',author:'merchant',plain:'Buy 20 acres, feed 1,800 bushels, plant 700 acres',accept:['merchant','priest']},
    ]}));
    await page.waitForTimeout(350);
    await page.screenshot({path:path.join(output,'deadlock-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'deadlock-mobile.png'),fullPage:true});
    assert.equal(await page.locator('[data-choice="safe"]').isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.evaluate(()=>closeEscalation());
    await page.setViewportSize({width:320,height:740});
    await page.waitForTimeout(350);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.equal(await page.locator('#setupBtn').evaluate(el=>{
      const r=el.getBoundingClientRect();
      return el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));
    }),true, 'Setup must stay reachable on narrow phones');
    await page.setViewportSize({width:390,height:844});
    for (const count of [1,21]) {
      await page.evaluate(count=>setAdvisors(Array.from({length:count},(_,i)=>({id:`custom-person-${i}`,short:`Agent ${i+1}`,color:'#5f8f3a'}))),count);
      assert.equal(await page.locator('.cg-node:not(.leave)').count(),count);
      await page.waitForTimeout(700);
      await checkSeatsClearResources();
    }
    await page.evaluate(()=>{
      setAdvisors([{id:'farmer',short:'Farmer'},{id:'merchant',short:'Merchant'}]);
      showRunoff({a:{id:'farmer-y1-r1',author:'farmer',plan:{land_delta:0,feed:2000,plant:800}},b:{id:'merchant-y1-r1',author:'merchant',plan:{land_delta:20,feed:1800,plant:700}}},true);
      addRunoffVote('farmer','farmer-y1-r1',true);
      addRunoffVote('merchant','merchant-y1-r1',true);
    });
    assert.deepEqual(await page.locator('.river-plan').first().locator('.river-supporter').evaluateAll(els=>els.map(el=>el.getAttribute('aria-label'))),['Farmer']);
    assert.deepEqual(await page.locator('.river-plan').last().locator('.river-supporter').evaluateAll(els=>els.map(el=>el.getAttribute('aria-label'))),['Merchant']);
    await page.evaluate(()=>render('year_start',{year:2},true));
    assert.equal(await page.locator('.river-plan').count(),0, 'New years clear old plans');
    assert.deepEqual(errors,[]);
    console.log('PASS: city resources, clear council seats, removed map decorations, vote links, omen plan details and ties, mobile omen controls, reduced motion, 1/5/21 advisor rosters, clamor shouts, no browser errors.');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(e=>{console.error(e);process.exit(1)});

// DOM regression: install jsdom@26.1.0 in a temporary test environment and
// expose its node_modules through NODE_PATH. No real account or backend calls.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');

async function boot(page='overview') {
  const root=path.resolve(__dirname,'..');
  const source=fs.readFileSync(process.env.WEBUI_TEST_SOURCE||path.join(root,'webui/src/app.js'),'utf8');
  const html=fs.readFileSync(path.join(root,'webui/src/index.html'),'utf8');
  const dom=new JSDOM(html,{url:'https://example.test/',runScripts:'outside-only'});
  const w=dom.window;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.setInterval=()=>0;
  const cfg={timezone:'Asia/Shanghai',video_share_enabled:true,video_share_daily_limit:2,
    life_enabled:true,life_idle_minutes:20,life_interval_minutes:180,max_videos:3,max_model_calls:40,max_tokens:120000,expression_preferences:[]};
  const modules={sharing:{title:'视频分享',description:'真实经历',fields:['video_share_enabled','video_share_daily_limit']}};
  if(source.includes("life_enabled:'空闲浏览开关'"))modules.life={title:'小小的空闲浏览',description:'空闲发现',fields:['life_enabled','life_idle_minutes','life_interval_minutes']};
  const properties=Object.fromEntries(Object.entries(cfg).map(([key,value])=>[key,{type:typeof value==='boolean'?'boolean':typeof value==='number'?'integer':'string'}]));
  const calls=[];
  w.maibot={ready:Promise.resolve(),page:{id:page,state:{}},navigate:()=>{},
    query:async(name,args)=>{
      calls.push({type:'query',name,args});
      if(name==='snapshot'&&args.section==='settings')return {settings:{...cfg},schema:{properties},modules,paused:false,posting_stopped:true,account:{state:'authenticated'}};
      if(name==='snapshot'&&args.section==='overview')return {budget:[],timezone:cfg.timezone,summaries:[],account:{name:'test'},paused:false};
      if(name==='qr')return {state:'idle'};
      throw Error('Unexpected query '+name+JSON.stringify(args));
    },
    action:async(name,args)=>{
      calls.push({type:'action',name,args});assert.equal(name,'settings_patch');const patch=JSON.parse(args.data);
      if(patch.video_share_daily_limit===0)throw Error('daily limit invalid');
      Object.assign(cfg,patch);return {ok:true};
    }};
  await w.eval('(async()=>{'+source+'\n})()');
  assert.ok(w.document.querySelector('.module-tile'),w.document.querySelector('#message').textContent);
  const click=async node=>{assert.ok(node);node.click();await wait(()=>!node.disabled);};
  const tile=name=>modules[name]&&[...w.document.querySelectorAll('.module-tile')].find(n=>n.querySelector('h3').textContent===modules[name].title);
  const edit=async name=>{await click([...tile(name).querySelectorAll('button')].find(n=>n.textContent.startsWith('打开')));return w.document.querySelector('#details');};
  const save=async()=>click([...w.document.querySelectorAll('#details button')].find(n=>n.textContent==='保存本模块'));
  return {w,dom,cfg,calls,tile,edit,save,click};
}
async function wait(predicate){for(let i=0;i<100;i++){if(predicate())return;await new Promise(r=>setTimeout(r,1));}assert.fail('UI operation did not settle');}

for(const page of ['overview','settings'])test(page+': saved modal values update cards without closing or refreshing',async()=>{
  const a=await boot(page);
  try{
    const card=a.tile('sharing');const dialog=await a.edit('sharing');
    dialog.querySelector('input[type="number"]').value='7';dialog.querySelector('input[type="checkbox"]').checked=false;
    await a.save();assert.equal(a.cfg.video_share_daily_limit,7);assert.equal(dialog.open,true);
    assert.match(card.querySelector('.tile-summary').textContent,/7/);assert.match(card.querySelector('.tile-summary').textContent,/关闭/);
    assert.equal(card.querySelector('input[type="checkbox"]').checked,false);
    assert.equal(a.tile('sharing'),card,'save must not rebuild the page or discard other input');
    await a.click(a.w.document.querySelector('#close'));assert.match(card.querySelector('.tile-summary').textContent,/7/);
    await a.edit('sharing');assert.equal(dialog.querySelector('input[type="number"]').value,'7');
    assert.equal(a.calls.filter(c=>c.type==='action').length,1);
  }finally{a.dom.window.close();}
});

test('tile switch immediately updates its summary',async()=>{
  const a=await boot();try{const card=a.tile('sharing'),control=card.querySelector('input');control.checked=false;control.dispatchEvent(new a.w.Event('change'));await wait(()=>a.w.document.querySelector('#message').textContent.includes('已关闭'));assert.equal(a.cfg.video_share_enabled,false);assert.match(card.querySelector('.tile-summary').textContent,/关闭/);}finally{a.dom.window.close();}
});

test('rejected save preserves draft and leaves card unchanged',async()=>{
  const a=await boot();try{const card=a.tile('sharing'),before=card.querySelector('.tile-summary').textContent,dialog=await a.edit('sharing');dialog.querySelector('input[type="number"]').value='0';await a.save();assert.equal(a.cfg.video_share_daily_limit,2);assert.equal(card.querySelector('.tile-summary').textContent,before);assert.equal(dialog.querySelector('input[type="number"]').value,'0');assert.equal(dialog.open,true);assert.match(a.w.document.querySelector('#message').textContent,/daily limit invalid/);}finally{a.dom.window.close();}
});

test('idle browsing modal updates both time values',async t=>{
  const a=await boot();try{if(!a.tile('life')){t.skip('idle browsing belongs to the integrated SDK 2.20 branch');return;}const card=a.tile('life'),dialog=await a.edit('life'),numbers=dialog.querySelectorAll('input[type="number"]');numbers[0].value='10';numbers[1].value='60';await a.save();assert.match(card.querySelector('.tile-summary').textContent,/空闲 10 分钟.*间隔 60 分钟/);assert.equal(a.cfg.life_idle_minutes,10);assert.equal(a.cfg.life_interval_minutes,60);}finally{a.dom.window.close();}
});

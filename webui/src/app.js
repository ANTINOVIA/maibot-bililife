const $ = s => document.querySelector(s);
const main = $('#main');
const pages = [['overview','总览'],['schedule','每日安排'],['browse','浏览与理解'],['interactions','评论与回复'],['memories','经历记忆'],['interests','兴趣与人格'],['providers','模型'],['settings','设置总览'],['logs','运行记录']];
const states = {resting:'休息中',queued:'待执行',running:'进行中',succeeded:'已完成',failed:'失败',retry:'等待重试',cancelled:'已取消',skipped:'已跳过',expired:'已过期',paused:'已暂停',blocked_login:'等待账号操作',blocked_policy:'模块或规则阻止',blocked_model:'模型不可用',budget_exhausted:'额度已用完',timeout:'超时',unknown:'结果未知，待核对',draft:'待发布',pending:'待发送',sending:'发送中',rejected:'平台拒绝',not_sent:'未发送',understood:'已理解',reading:'读取中',authenticated:'已登录',not_logged_in:'未登录',not_checked:'检查中',restricted:'账号受限',active:'可检索',forgotten:'已遗忘',appended:'已追加上下文',not_appended:'未追加',unsupported:'当前宿主不支持',stale:'历史上下文待更新'};
const kinds = {discover:'发现内容',read_comments:'只读评论',watch:'观看与理解',explore:'继续探索',draft:'整理评论',replies:'检查与回复',memory:'整理记忆',review:'每日复盘',rest:'休息',account:'检查账号',login:'扫码登录',publish:'发布',reconcile:'核对发布结果',plan:'生成日程',adapt:'自主调整日程',model_test:'检查模型连接'};
const colors = {discover:'#8d7ac6',read_comments:'#6a9c82',watch:'#5b9cb5',explore:'#b58ac0',draft:'#ce9a60',replies:'#7aab86',memory:'#8a9ccc',review:'#d08094',rest:'#a9adb4',publish:'#b48a58'};
const roles = {text:'文本与规划',vision:'关键帧视觉',asr:'语音转录 ASR',audio:'音频理解',video:'视频片段理解'};
const labels = {planner_enabled:'自动生成每日计划',adaptive_schedule_enabled:'麦麦自主追加日程',max_dynamic_tasks:'每日自主追加任务上限',owner_uid:'主人 B 站 UID（数字，留空不提醒）',mention_owner:'视频评论时 @ 主人',browsing_enabled:'浏览与理解',comments_enabled:'生成视频评论',replies_enabled:'跟进相关回复',auto_publish_comments:'自动发布评论',auto_publish_replies:'自动发布回复',review_enabled:'每日复盘',persona_guidance:'我的干预与偏好',expression_preferences:'表达偏好（每行一项）',timezone:'业务时区',plan_time:'每日生成时间',review_time:'每日复盘时间',active_start:'活跃时间开始',active_end:'活跃时间结束',allowed_topics:'感兴趣的主题（每行一项）',excluded_topics:'排除主题（每行一项）',allowed_uploaders:'指定 UP 主 UID（每行一项，留空不限）',max_videos:'每日视频浏览量（条）',max_comment_sessions:'每日只读评论次数',max_comments_read:'每次阅读评论数（最多20条）',max_video_seconds:'单视频最长时长（秒）',max_browse_seconds:'每日浏览总时长（秒）',max_comments:'每日评论量（条）',max_replies:'每日回复量（条）',max_per_video:'同视频评论上限',max_thread_rounds:'同一回复链最大轮数',cooldown_seconds:'同视频间隔（秒）',user_cooldown_seconds:'同用户间隔（秒）',reply_interval_seconds:'检查回复间隔（秒）',max_model_calls:'每日模型调用量',max_tokens:'每日 Token 上限',output_tokens:'单次输出 Token 上限',max_download_mb:'单视频下载上限（MiB）',daily_download_mb:'每日下载上限（MiB）',max_storage_mb:'总存储上限（MiB）',max_frames:'关键帧数量',segment_seconds:'媒体片段长度（秒）',retention_days:'记录保留天数',cache_hours:'媒体缓存小时数',task_timeout_seconds:'任务超时（秒）',model_timeout_seconds:'模型超时（秒）',max_retries:'网络失败重试次数',text_model_task:'宿主文本任务角色',vision_model_task:'宿主视觉任务角色',asr_model_task:'宿主 ASR 任务角色',video_understanding_mode:'视频理解方式',enable_asr:'无字幕时使用语音转录',memory_enabled:'保存与召回经历',evolution_enabled:'允许兴趣缓慢变化',recall_limit:'每次召回条数',recall_chars:'召回上下文长度',summary_stream_id:'复盘推送会话（留空不推送）',prompt_overrides:'分阶段提示词补充'};
Object.assign(labels,{schedule_task_minutes:'浏览安排最长时段（分钟）',schedule_gap_minutes:'活动之间自由间隔（分钟）',schedule_brief_minutes:'回复与整理最长时段（分钟）',schedule_rest_minutes:'每次休息至少（分钟）',schedule_rest_max_minutes:'每次休息最长（分钟）',comment_prompt:'评论与回复风格提示词',comment_avoid_phrases:'避免使用的表达（每行一项）'});
const protocols=[['openai_compatible','通用 OpenAI 兼容'],['qwen_compatible','Qwen 兼容'],['dashscope','Qwen · DashScope 原生'],['gemini','Gemini 原生']];
const videoModes={frames:'截取关键帧理解',video:'视频片段理解',audio:'音频片段理解',subtitle:'仅字幕 / 无字幕时 ASR'};
const targets = {schedule:'schedule',browse:'browse',comments:'interactions',replies:'interactions',memory:'memories',persona:'interests',review:'schedule',models:'providers',resources:'logs',prompts:'settings'};
let page='overview', section='', number=1, busy=false, zone='Asia/Shanghai', settingsData=null, requestChain=Promise.resolve();
let pendingJob=null;
let scheduleDay='';

function el(tag,text='',cls='') { const n=document.createElement(tag); n.textContent=text; if(cls)n.className=cls; if(tag==='input'||tag==='textarea')n.classList.add('maibot-input'); if(tag==='select')n.classList.add('maibot-select'); return n; }
function button(text,fn,cls='') { const b=el('button',text,'maibot-button '+cls); if(cls.includes('primary'))b.classList.add('maibot-button-primary'); if(cls.includes('danger'))b.classList.add('maibot-button-danger'); b.type='button'; b.onclick=()=>perform(fn,b); return b; }
function notify(text,error=false) { $('#message').textContent=text; $('#message').className='notice'+(error?' error':''); }
async function perform(fn,b) { if(b)b.disabled=true; try { await fn(); } catch(e) { notify(e.message,true); } finally { if(b)b.disabled=false; } }
async function call(type,name,args={}) {
  // 宿主页面桥接最多允许两个并发请求；串行短请求避免保存时被后台轮询挤掉。
  const task=requestChain.then(()=>maibot[type](name,args)); requestChain=task.catch(()=>{});
  const result=await task; if(result?.ok===false)throw Error(result.error||'操作失败'); return result;
}
function toolbar(...nodes) { const t=el('div','','toolbar');t.append(...nodes);return t; }
function panel(title,description='') { const p=el('section','','maibot-card panel');if(title)p.append(el('h2',title));if(description)p.append(el('p',description,'muted'));return p; }
function badge(state) { return el('span',states[state]||state,'pill '+(/failed|blocked|unknown|restricted|timeout|exhausted|rejected/.test(state)?'bad':/queued|pending|retry|draft|reading|running/.test(state)?'wait':'good')); }
function field(title,input,wide=false) { const n=el('div','','field'+(wide?' wide':'')),l=el('label',title);const id=input.id||'f-'+crypto.randomUUID();input.id=id;l.htmlFor=id;n.append(l,input);return n; }
function input(value='',type='text') { const n=el('input');n.type=type;n.value=value;return n; }
function select(options,value='') { const n=el('select');for(const[v,label]of options){const o=el('option',label);o.value=v;n.append(o);}n.value=value;return n; }
function toggle(title,checked,fn=null) { const label=el('label','','switch'),c=input('','checkbox');c.checked=checked;label.append(c,el('span',title));if(fn)c.onchange=()=>perform(async()=>{try{await fn(c.checked);}catch(e){c.checked=!c.checked;throw e;}});return {node:label,input:c}; }
function expandable(title,data) { const d=el('details');d.append(el('summary',title),el('pre',typeof data==='string'?data:JSON.stringify(data,null,2)));return d; }
function link(url,label=url) { const a=el('a',label,'source');try{const u=new URL(url);if(u.protocol!=='https:')throw Error();a.href=u.href;a.target='_blank';a.rel='noreferrer noopener';}catch{a.removeAttribute('href');}return a; }
function stamp(t,short=false) { return t?new Date(t*1000).toLocaleString('zh-CN',{timeZone:zone,hour12:false,...(short?{hour:'2-digit',minute:'2-digit'}:{})}):'—'; }
function minutes(t) { const parts=new Intl.DateTimeFormat('en-GB',{timeZone:zone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(t*1000));return Number(parts.find(x=>x.type==='hour').value)*60+Number(parts.find(x=>x.type==='minute').value); }
function nav(id,state={}) { return maibot.navigate(id,state); }
function modal(title,...nodes) { $('#detailtitle').textContent=title;$('#detailbody').replaceChildren(...nodes);if(!$('#details').open)$('#details').showModal();return $('#detailbody'); }
async function loadSettings() { if(!settingsData)settingsData=await call('query','snapshot',{section:'settings'});zone=settingsData.settings.timezone;$('#pause').textContent=settingsData.paused?'继续全部任务':'暂停全部任务';$('#stop').textContent=settingsData.posting_stopped?'恢复发布总开关':'停止自动发布';return settingsData; }
async function control(action) { await call('action','control',{action});notify(({pause:'全部任务已暂停',resume:'调度已继续',stop_posting:'已阻止新的发布请求；已发送请求仍会核对',resume_posting:'已恢复发布总开关，仍受各模块开关和额度约束',clear_restriction:'平台暂停已解除',logout:'账号已退出'})[action]||'操作完成');await refresh(); }
async function enqueue(kind,data={}) { const r=await call('action','enqueue',{kind,data:JSON.stringify(data)});pendingJob={id:r.task_id,label:kinds[kind]};notify(`${kinds[kind]}已排队 · ${r.task_id}`);return r; }

async function tomlEditor(module='') {
  const d=await call('query','settings_toml',{module}),t=el('textarea','','code-editor');t.value=d.text;t.spellcheck=false;
  modal(module?'编辑模块 TOML':'统一配置 · TOML',el('p','保存会校验并立即生效；不包含 Cookie 或模型密钥。常用选项也可在对应模块直接调整。','hint'),t,el('p',`服务器文件：${d.path}`,'muted'),toolbar(button('校验并保存',async()=>{await call('action','settings_toml_save',{module,data:t.value});$('#details').close();notify('TOML 已校验并保存');await refresh();},'primary'),button('全选文本',()=>{t.focus();t.select();})));
}

async function moduleForm(name,collapsed=false) {
  const data=await loadSettings(),info=data.modules[name],cfg=data.settings,controls={},nodes={};
  const p=panel(info.title,info.description),head=el('div','','panel-head');head.append(el('span',name==='comments'||name==='replies'?'启用自动发布后，符合规则的草稿会自动排队，无需逐条确认。':'各项设置单独保存，不覆盖其他模块。','muted'),button('TOML 编辑',()=>tomlEditor(name)));p.append(head);
  const grid=el('div','','form-grid');
  for(const key of info.fields) {
    if(key.endsWith('_model_task'))continue;
    const schema=data.schema.properties[key],value=cfg[key];
    if(schema.type==='object'){p.append(button('用 TOML 编辑提示词',()=>tomlEditor(name)));continue;}
    if(schema.type==='boolean') { const c=toggle(labels[key]||key,value);const f=el('div','','field');f.append(c.node);grid.append(f);controls[key]=()=>c.input.checked;nodes[key]=c.input;continue; }
    let n;if(schema.enum){n=select(schema.enum.map(v=>[v,videoModes[v]||v]),value);controls[key]=()=>n.value;}else if(schema.type==='array'){n=el('textarea');n.value=value.join('\n');controls[key]=()=>n.value.split('\n').map(x=>x.trim()).filter(Boolean);}else{n=input(value,schema.type==='integer'?'number':key.endsWith('_time')||key.startsWith('active_')?'time':'text');controls[key]=()=>schema.type==='integer'?Number(n.value):n.value;}
    if(key==='persona_guidance'||key==='comment_prompt'){n=el('textarea');n.value=value;n.maxLength=key==='comment_prompt'?3000:2000;n.rows=key==='comment_prompt'?7:3;n.placeholder=key==='comment_prompt'?'围绕具体内容简短表达，避免助手汇报与空泛夸赞。':'例如：这周多了解天文；表达简洁一些，遇到争论就结束。';controls[key]=()=>n.value;}
    nodes[key]=n;grid.append(field(labels[key]||key,n,['persona_guidance','expression_preferences','comment_prompt','comment_avoid_phrases'].includes(key)));
  }
  p.append(grid);if(name==='persona')p.append(el('p','这是 BiliLife 的人工偏好干预，影响规划与表达。宿主核心人格、身份和执行权限不会被覆盖；每次修改保留历史，可回滚。','hint'));
  if(name==='schedule')p.append(el('p','浏览、回复整理和休息采用不同节奏，休息更长，不必排满一天。休息时暂停新的浏览和发布；可在日程中删除该项提前结束。执行超时单独控制。','hint'));
  if(name==='comments')p.append(el('p','提示词用于新评论与回复生成。避免表达会在生成后与发布前检查；已有草稿保留原文，命中时需修改后发布。留空列表可关闭表达检查。','hint'),button('恢复默认风格提示词',()=>{nodes.comment_prompt.value=data.schema.properties.comment_prompt.default;notify('已填入默认提示词，点击“保存本模块”后生效');}));
  if(name==='browse')p.append(el('p','先选择理解方式，再在“模型”配置对应用途。关键帧使用视觉模型；视频片段直接提交受限片段给视频模型；两条路径互斥，不会因另一模型已配置而自动切换。','hint'));
  const actions=toolbar(button('保存本模块',async()=>{const patch=Object.fromEntries(Object.entries(controls).map(([k,read])=>[k,read()]));await call('action','settings_patch',{module:name,data:JSON.stringify(patch)});settingsData=null;const latest=(await loadSettings()).settings;for(const[k,node]of Object.entries(nodes)){if(node.type==='checkbox')node.checked=latest[k];else node.value=Array.isArray(latest[k])?latest[k].join('\n'):latest[k];}notify(info.title+'已保存');},'primary'));actions.classList.add('form-actions');p.append(actions);
  if(collapsed){const d=el('details');d.append(el('summary','调整'+info.title),p);return d;}return p;
}

function moduleSummary(name,c) {
  return ({schedule:`${c.plan_time} 生成 · 浏览≤${c.schedule_task_minutes}分钟 · 休息${c.schedule_rest_minutes}—${c.schedule_rest_max_minutes}分钟`,browse:`每天最多 ${c.max_videos} 条 · ${Math.round(c.max_browse_seconds/60)} 分钟`,comments:`每日 ${c.max_comments} 条 · ${c.auto_publish_comments?'自动发布已开':'保留草稿，手动发布'}`,replies:`每日 ${c.max_replies} 条 · 每 ${Math.round(c.reply_interval_seconds/60)} 分钟检查`,memory:`每次最多召回 ${c.recall_limit} 条 · ${c.recall_chars} 字`,persona:`${c.evolution_enabled?'允许兴趣缓慢调整':'兴趣自动变化已关'} · ${c.expression_preferences.length} 项表达偏好`,review:`${c.review_time} 复盘 · ${c.summary_stream_id?'推送到指定会话':'不向会话推送'}`,models:`每日 ${c.max_model_calls} 次 · ${c.max_tokens.toLocaleString()} Token`,resources:`${c.max_storage_mb} MiB · 记录保留 ${c.retention_days} 天`,prompts:'运行阶段的管理员补充，支持 TOML'})[name];
}
async function moduleTiles(all=false) {
  const d=await loadSettings(),g=el('div','','grid');
  const switches={schedule:'planner_enabled',browse:'browsing_enabled',comments:'comments_enabled',replies:'replies_enabled',memory:'memory_enabled',persona:'evolution_enabled',review:'review_enabled'};
  for(const [name,info]of Object.entries(d.modules)){
    if(!all&&['resources','prompts','models'].includes(name))continue;
    const p=panel(),h=el('div','','row-head');h.append(el('h3',info.title));
    if(switches[name])h.append(toggle('启用',d.settings[switches[name]],async checked=>{await call('action','settings_patch',{module:name,data:JSON.stringify({[switches[name]]:checked})});settingsData=null;notify(info.title+(checked?'已启用':'已关闭'));}).node);
    p.classList.add('module-tile');p.append(h,el('p',moduleSummary(name,d.settings),'tile-summary'),el('p',info.description,'muted'),button('打开'+info.title,async()=>{if(['resources','prompts','models','review'].includes(name)){if(name==='models')await nav('providers');else if(name==='review')await nav('schedule',{section:'summaries'});else await openModule(name);}else await nav(targets[name],name==='comments'||name==='replies'?{module:name}:{});},'link'));g.append(p);
  }return g;
}
async function openModule(name){const f=await moduleForm(name);modal(settingsData.modules[name].title,f);}

function metric(title,value,total=null,note='') { const p=panel(title),m=el('div',String(value),'metric');if(total!==null)m.append(el('small','/ '+total));p.append(m);if(note)p.append(el('p',note,'muted'));if(total!==null){const bar=el('div','','meter'),fill=el('span');fill.style.width=Math.min(100,total>0?100*value/total:0)+'%';bar.append(fill);p.append(bar);}return p; }
async function overview(){
  const d=await call('query','snapshot',{section:'overview'}),s=(await loadSettings()).settings;zone=d.timezone;
  const budget=Object.fromEntries(d.budget.map(x=>[x.resource,x.used]));
  const stats=el('div','','grid stats');stats.append(metric('今日浏览',budget.videos||0,s.max_videos),metric('模型调用',budget.model_calls||0,s.max_model_calls),metric('今日 Token',budget.tokens||0,s.max_tokens),metric('当前安排',d.paused?'已暂停':d.rest_until?'休息至 '+stamp(d.rest_until,true):d.current_task?kinds[d.current_task.type]:'自由时间',null,d.account.name||'请先扫码登录'));main.append(stats);
  if(d.blocked||d.plan_error||d.config_error)main.append(el('div',[d.blocked,d.plan_error,d.config_error].filter(Boolean).join('\n'),'notice error'));
  main.append(toolbar(button(d.paused?'继续生活':'暂停任务',()=>control(d.paused?'resume':'pause')),button('查看每日安排',()=>nav('schedule')),button('恢复发布总开关',()=>control('resume_posting'))));
  const heading=el('div','','section-title');heading.append(el('h2','我的生活模块'),el('span','开关与额度可以分别调整','muted'));main.append(heading,await moduleTiles());
  const p=panel('最近复盘');if(!d.summaries.length)p.append(el('p','今天尚未形成复盘。','muted'));for(const x of d.summaries.slice(0,2)){p.append(el('h3',x.day),el('p',x.text));}main.append(p);
}

function svgNode(tag,attrs={},text='') { const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));n.textContent=text;return n; }
function point(minute,radius) { const a=minute/1440*Math.PI*2-Math.PI/2;return [200+Math.cos(a)*radius,200+Math.sin(a)*radius]; }
function arcPath(start,end,r) { const a=point(start,r),b=point(end,r);return `M ${a[0]} ${a[1]} A ${r} ${r} 0 ${end-start>720?1:0} 1 ${b[0]} ${b[1]}`; }
function taskTitle(t) { return t.payload?.goal||t.payload?.query||t.payload?.reason||kinds[t.type]||t.type; }
const lifeTypes=new Set(['discover','watch','explore','read_comments','draft','replies','memory','review','rest','publish']);
function clock(data) {
  const svg=svgNode('svg',{viewBox:'0 0 400 400',class:'day-clock',role:'img','aria-label':`${data.date} 二十四小时日程钟面，彩环表示可执行时段，旁边列表提供全部操作`});
  for(let h=0;h<24;h++){const a=point(h*60,170),b=point(h*60,h%3?174:178);svg.append(svgNode('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],class:'clock-tick'}));if(h%3===0){const p=point(h*60,188);svg.append(svgNode('text',{x:p[0],y:p[1]+4,'text-anchor':'middle'},String(h).padStart(2,'0')));}}
  const ends=Array(6).fill(-1),arcs=[];let tracks=1;
  for(const t of data.items.filter(x=>lifeTypes.has(x.type)&&!['cancelled','skipped','expired'].includes(x.state))){const start=minutes(t.earliest),end0=minutes(t.deadline),end=Math.max(start+4,Math.min(1439,end0<start?1439:end0));let lane=ends.findIndex(x=>x<=start);if(lane<0)lane=5;ends[lane]=end;tracks=Math.max(tracks,lane+1);arcs.push({t,start,end,lane});}
  for(let i=0;i<tracks;i++)svg.append(svgNode('circle',{cx:200,cy:200,r:155-i*12,class:'clock-track'}));
  for(const{t,start,end,lane}of arcs){const path=svgNode('path',{d:arcPath(start,end,155-lane*12),class:'clock-arc',stroke:colors[t.type]||'#9696a6',opacity:t.state==='succeeded'&&!(t.type==='rest'&&t.earliest<=data.now&&t.deadline>data.now)?.4:1,tabindex:0,role:'button','aria-label':`${stamp(t.earliest,true)} ${kinds[t.type]} ${taskTitle(t)}`});path.append(svgNode('title',{},`${stamp(t.earliest,true)}—${stamp(t.deadline,true)} · ${taskTitle(t)} · ${states[t.state]||t.state}`));path.onclick=()=>perform(()=>showDetail('schedule',t.id));path.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();path.onclick();}};svg.append(path);}
  const now=minutes(data.now),p=point(now,167),inner=point(now,76);if(data.is_today)svg.append(svgNode('line',{x1:inner[0],y1:inner[1],x2:p[0],y2:p[1],class:'clock-hand'}),svgNode('circle',{cx:p[0],cy:p[1],r:5,class:'clock-dot'}));svg.append(svgNode('text',{x:200,y:184,'text-anchor':'middle',class:'clock-sub'},data.date),svgNode('text',{x:200,y:216,'text-anchor':'middle',class:'clock-day'},data.is_today?stamp(data.now,true):'日程'),svgNode('text',{x:200,y:239,'text-anchor':'middle',class:'clock-sub'},data.timezone));return svg;
}
async function schedule(){
  const data=await call('query','snapshot',{section:'schedule_board'+(scheduleDay?':'+scheduleDay:'')});zone=data.timezone;scheduleDay=data.date;
  const p=panel(),head=el('div','','panel-head'),date=input(data.date,'date'),regen=button('重新生成今日日程',regenerate);regen.disabled=!data.is_today;date.setAttribute('aria-label','日程日期');date.style.width='auto';date.onchange=()=>perform(async()=>{scheduleDay=date.value;await refresh();});head.append(toolbar(el('h2','每日安排'),date),toolbar(regen,button('插入日程',insertDialog,'primary')));p.append(head);
  p.append(el('p',data.plan?`${data.plan.payload.theme} · 第 ${data.plan.version} 版`:'还没有今日主计划，可以生成或直接插入日程。','muted'));
  const split=el('div','','split'),wrap=el('div','','clock-wrap');wrap.append(clock(data));const agenda=el('div','','agenda');
  const items=data.items.filter(x=>lifeTypes.has(x.type));
  if(!items.length)agenda.append(el('div','今天还没有浏览安排。','empty'));
  for(const t of items){const item=el('article','','agenda-item'),content=el('div'),h=el('div','','row-head');h.append(el('h3',kinds[t.type]),badge(t.type==='rest'&&t.earliest<=data.now&&t.deadline>data.now&&!['cancelled','skipped','failed','expired'].includes(t.state)?'resting':t.state),...(t.payload?.origin==='agent'?[el('span','麦麦自主追加','pill')]:[]));content.append(h,el('p',taskTitle(t)),el('p',t.type==='rest'?`休息至 ${stamp(t.deadline,true)} · 期间暂停新的浏览和发布`:`${stamp(t.earliest,true)}—${stamp(t.deadline,true)} · 这段时间里择机进行`,'muted'));item.append(el('time',stamp(t.earliest,true)),content,toolbar(button('详情',()=>showDetail('schedule',t.id)),button('删除',async()=>{await call('action','schedule_change',{action:'delete',data:JSON.stringify({task_id:t.id,reason:'管理员在日程面板删除'})});notify('已移出日程；未执行动作已取消，历史与发布记录保留');await refresh();})));agenda.append(item);}split.append(wrap,agenda);p.append(split);
  const legend=el('div','','legend');for(const [kind,color]of Object.entries(colors)){const l=el('span'),dot=el('i','','dot');dot.style.background=color;l.append(dot,document.createTextNode(kinds[kind]));legend.append(l);}p.append(legend,el('p','一圈为24小时；彩环是任务可执行时段，浅色为已完成。重叠时段分层展示，任务按顺序执行。','muted'));main.append(p);
  if(data.deleted.length){const d=el('details');d.append(el('summary',`已删除 ${data.deleted.length} 项 · 可以恢复显示`));for(const t of data.deleted){const r=el('div','','record');r.append(el('p',kinds[t.type]+' · '+t.reason),button('恢复显示',async()=>{await call('action','schedule_change',{action:'restore',data:JSON.stringify({task_id:t.id})});notify('已恢复显示；已取消的任务不会重新执行');await refresh();}));d.append(r);}main.append(d);}
  if(data.adaptations?.length){const a=panel('麦麦的日程决定');for(const r of data.adaptations)a.append(el('p',`${r.video_id} · ${r.state} · ${r.payload.reason||'正在评估'} · 追加 ${(r.payload.inserted||[]).length} 项`,'muted'));main.append(a);}
  main.append(tabs([['schedule','日程设置'],['summaries','每日复盘'],['plans','历史计划']],section||'schedule'));
  if(section==='summaries'){main.append(toolbar(button('现在复盘',()=>enqueue('review'))),await moduleForm('review'));await records('summaries');}else if(section==='plans')await records('plans');else main.append(await moduleForm('schedule'));
}
function tabs(options,value){const n=el('div','','tabs');for(const[id,title]of options)n.append(button(title,async()=>{section=id;number=1;await refresh();},id===value?'on':''));return n;}
function regenerate(){const reason=input('调整今天的浏览节奏和兴趣安排');modal('重新生成今日安排',el('p','保留已执行记录，取消旧计划中尚未完成的任务。手动插入的独立日程会保留。','hint'),field('重新安排的原因',reason),button('生成新版本',async()=>{const r=await call('action','schedule_change',{action:'regenerate',data:JSON.stringify({reason:reason.value})});pendingJob={id:r.task_id,label:'重新生成日程'};$('#details').close();notify('新日程正在生成，完成后自动更新');},'primary'));}
async function insertDialog(){
  const cfg=(await loadSettings()).settings,board=await call('query','snapshot',{section:'schedule_board'});
  const type=select(['discover','explore','read_comments','replies','memory','review','rest'].map(k=>[k,kinds[k]]),'discover'),q=input(''),day=input(scheduleDay&&scheduleDay>=board.date?scheduleDay:board.date,'date'),start=input(cfg.active_start,'time'),end=input(cfg.active_start,'time'),duration=input(Math.min(300,cfg.task_timeout_seconds),'number'),reason=input('手动安排');q.placeholder='允许主题；留空由麦麦自主选题';duration.min='10';duration.max=String(cfg.task_timeout_seconds);
  const toMinutes=v=>Number(v.split(':')[0])*60+Number(v.split(':')[1]),toTime=v=>String(Math.floor(v/60)).padStart(2,'0')+':'+String(v%60).padStart(2,'0');
  const activeStart=toMinutes(cfg.active_start),activeEnd=toMinutes(cfg.active_end),current=minutes(board.now)+1;
  if(day.value===board.date){if(activeStart<=activeEnd&&current>=activeEnd){const next=new Date(board.date+'T12:00:00Z');next.setUTCDate(next.getUTCDate()+1);day.value=next.toISOString().slice(0,10);}else if((activeStart<=activeEnd&&current>=activeStart)||(activeStart>activeEnd&&(current>=activeStart||current<activeEnd)))start.value=toTime(Math.min(current,1438));}
  const setShortEnd=()=>{const from=toMinutes(start.value),limit=type.value==='rest'?1439:activeStart<=activeEnd?activeEnd:from<activeEnd?activeEnd:1439;end.value=toTime(Math.min(from+(type.value==='rest'?cfg.schedule_rest_minutes:['replies','memory','review','draft'].includes(type.value)?cfg.schedule_brief_minutes:cfg.schedule_task_minutes),limit,1439));};setShortEnd();start.onchange=setShortEnd;type.onchange=setShortEnd;
  const grid=el('div','','form-grid');for(const[k,n]of [['安排内容',type],['视频/主题/记录',q],['日期',day],['开始时间',start],['最晚完成',end],['执行时限（秒）',duration],['安排原因',reason]])grid.append(field(k,n));
  modal('插入一项日程',grid,el('p',`时区 ${zone}，时间和内容由后端验证。插入后会持久保存，重启不会丢失。`,'muted'),button('保存到日程',async()=>{const kind=type.value,payload=['watch','discover','explore','read_comments'].includes(kind)?{query:q.value}:kind==='draft'?{video_id:q.value}:kind==='rest'?{reason:reason.value}:{};const r=await call('action','schedule_change',{action:'insert',data:JSON.stringify({kind,payload,date:day.value,start:start.value,end:end.value,duration:Number(duration.value),reason:reason.value})});$('#details').close();notify('日程已保存 · '+r.task_id);await refresh();},'primary'));
}

async function browse(){main.append(toolbar(button('自主找视频',()=>enqueue('discover',{}),'primary'),button('只读评论',()=>enqueue('read_comments',{}))),el('p','可观看视频，也可只阅读评论区。只读评论不下载媒体、不占视频条数，不会据此发布视频评论。','hint'),await moduleForm('browse'),tabs([['browse','视频经历'],['comment_reads','只读评论经历']],section||'browse'));await records(section==='comment_reads'?'comment_reads':'browse');}
async function interactions(){
  const which=section||'comments',config=(await loadSettings()).settings,stats=await call('query','snapshot',{section:'interaction_stats'}),counts={comment:0,reply:0,unknown:0};for(const x of stats.items){if(['pending','sending','unknown','succeeded'].includes(x.state))counts[x.kind]+=x.count;if(x.state==='unknown')counts.unknown+=x.count;}const grid=el('div','','grid stats');grid.append(metric('今日评论额度',counts.comment,config.max_comments),metric('今日回复额度',counts.reply,config.max_replies),metric('待核对发布',counts.unknown,null,'包括结果未知操作，不会盲目重发'));main.append(grid,tabs([['comments','视频评论'],['replies','回复跟进'],['operations','发布记录'],['threads','相关回复']],which));
  if(which==='comments'||which==='replies'){main.append(await moduleForm(which));if(which==='replies')main.append(toolbar(button('现在检查回复',()=>enqueue('replies'))));await records('interactions',which==='replies'?'reply':'comment');}else{if(which==='operations')main.append(toolbar(button('核对未知结果',()=>enqueue('reconcile'))));await records(which);}
}
async function memories(){main.append(await moduleForm('memory'));const p=panel('回忆一段经历'),q=input('');q.placeholder='输入内容、视频名或感兴趣的问题';p.append(toolbar(q,button('检索记忆',async()=>{const r=await call('query','memory_search',{query:q.value}),list=el('div');if(!r.items.length)list.append(el('div','没有找到相关经历。','empty'));for(const x of r.items){const row=el('div','','record');row.append(el('p',x.text),link(x.source),button('查看与纠错',()=>showDetail('memories',x.id)));list.append(row);}modal('记忆检索',list);})));main.append(p);await records('memories');}
async function interests(){
  main.append(await moduleForm('persona'));const p=panel('我的兴趣','可以直接调整权重、增加主题并冻结某项。人工干预不受自动变化步长限制，保存原因并支持回滚。');p.append(button('新增兴趣',()=>interestEditor(),'primary'));
  const data=await call('query','snapshot',{section:'interests'});for(const r of data.items){const row=el('div','','record'),h=el('div','','row-head');h.append(el('h3',r.topic),el('span',r.frozen?'已冻结':'允许缓慢变化','pill'));const meter=el('div','','meter'),bar=el('span');bar.style.width=(r.weight*100)+'%';meter.append(bar);row.append(h,el('p',`兴趣权重 ${Math.round(r.weight*100)}%`),meter,toolbar(button('干预与调整',()=>interestEditor(r)),button(r.frozen?'解冻':'冻结',async()=>{await call('action','interest_action',{record_id:r.topic,action:r.frozen?'unfreeze':'freeze'});await refresh();})));p.append(row);}main.append(p,tabs([['changes','兴趣变化'],['persona_history','人格干预历史']],section||'changes'));await records(section==='persona_history'?'persona_history':'changes');
}
function interestEditor(existing={}){
  const topic=input(existing.topic||''),weight=input(existing.weight??.5,'number'),slider=input(existing.weight??.5,'range'),reason=input(''),frozen=toggle('冻结自动变化',!!existing.frozen);weight.min='0';weight.max='1';weight.step='.01';weight.setAttribute('aria-label','兴趣权重数值');slider.min='0';slider.max='1';slider.step='.01';slider.setAttribute('aria-label','兴趣权重滑块');slider.oninput=()=>weight.value=slider.value;weight.oninput=()=>slider.value=weight.value;const value=el('div','','inline-value');value.append(slider,weight);reason.placeholder='例如：这周想多了解这个方向';
  modal(existing.topic?'调整兴趣':'新增兴趣',field('主题',topic),field('权重 0—1',value),frozen.node,field('干预原因',reason),el('p','新增主题也会加入允许浏览范围；发布仍取决于评论/回复开关、额度和证据检查。','hint'),button('保存干预',async()=>{await call('action','interest_save',{data:JSON.stringify({topic:topic.value,weight:Number(weight.value),frozen:frozen.input.checked,reason:reason.value})});$('#details').close();notify('兴趣已更新并保留变更记录');await refresh();},'primary'));
}

async function providers(){
  const d=await call('query','model_providers'),p=panel('给每件事选择模型','直接选择 MaiBot 已配置的模型，无需先添加继承条目。也可以使用下方独立配置的模型。');p.append(button('TOML 配置模型选择',()=>modelRoutingToml(d)));
  const controls={},mediaOptions={};
  for(const[role,title]of Object.entries(roles)){
    const row=el('div','','model-row'),heading=el('div');heading.append(el('h3',title),el('p',role==='asr'?'只生成转录文字':role==='video'?'受限视频片段，非无限视频流':role==='audio'?'将真实音频片段交给模型':'','muted'));
    const options=[['disabled','关闭此能力'],...d.catalog.models.filter(x=>x.model_type!=='embedding').map(x=>['host:'+x.name,'MaiBot · '+x.name+' / '+x.api_provider]),...d.providers.filter(x=>x.source==='custom'&&x.capabilities.includes(role)).map(x=>[x.id,'独立 · '+x.name])];
    const current=d.routes[role]||'disabled';if(!options.some(x=>x[0]===current))options.push([current,'原模型不可用，请重新选择']);const chooser=select(options,current);chooser.setAttribute('aria-label',title+'模型');controls[role]=chooser;
    const selection=el('div');selection.append(field('模型',chooser));
    if(role==='audio'||role==='video'){
      const opts=d.host_options?.[role]||{},protocol=select(protocols,opts.protocol||'openai_compatible'),audio=toggle('片段包含音轨（需模型支持音画联合）',!!opts.video_has_audio),fps=input(opts.video_fps??1,'number'),frames=input(opts.video_max_frames??16,'number'),thinking=toggle('关闭 Qwen 可切换思考模式',opts.qwen_disable_thinking!==false);
      fps.min='0.1';fps.max='10';fps.step='0.1';frames.min='1';frames.max='64';
      const hostSettings=el('div');selection.append(hostSettings);hostSettings.append(field('MaiBot 模型的直接媒体协议',protocol));if(role==='video')hostSettings.append(audio.node);
      const extra=el('div'),frameField=field('DashScope 最大抽帧数',frames);if(role==='video')extra.append(field('Qwen 视频 FPS',fps),frameField);extra.append(thinking.node);hostSettings.append(extra);const hint=el('p','独立条目的协议与 Qwen 参数请在下方编辑该模型。','hint');selection.append(hint);const updateSource=()=>{hostSettings.hidden=!chooser.value.startsWith('host:');hint.hidden=chooser.value.startsWith('host:')||chooser.value==='disabled';};chooser.onchange=updateSource;updateSource();
      const update=()=>{extra.hidden=!['qwen_compatible','dashscope'].includes(protocol.value);frameField.hidden=protocol.value!=='dashscope';};protocol.onchange=update;update();
      mediaOptions[role]={protocol,audio:audio.input,fps,frames,thinking:thinking.input};
    }
    const status=el('p','','muted');status.id='model-status-'+role;status.textContent=modelTestText(d.tests[role]);heading.append(status);
    row.append(heading,selection,toolbar(button('保存',()=>saveRoutes()),button('保存并检查',async()=>{await saveRoutes();const r=await call('action','model_test',{role});pendingJob={id:r.task_id,label:title+'连接检查'};status.textContent='等待连接检查，不占生活额度';notify('已提交连接检查，延迟与错误码显示在本用途下方');})));p.append(row);
  }
  async function saveRoutes(){const routes=Object.fromEntries(Object.entries(controls).map(([k,n])=>[k,n.value]));const host_options=Object.fromEntries(Object.entries(mediaOptions).map(([k,v])=>[k,{protocol:v.protocol.value,video_has_audio:k==='video'&&v.audio.checked,video_fps:Number(v.fps.value),video_max_frames:Number(v.frames.value),qwen_disable_thinking:v.thinking.checked}]));await call('action','model_routes',{data:JSON.stringify({routes,host_options})});notify('模型选择已保存');}
  p.append(el('p','连接检查使用最小合成输入，独立于生活额度，显示请求往返延迟；服务商仍可能计费。媒体检查只证明接口响应，不代表真实视频理解质量。','hint'));main.append(p);
  const custom=panel('独立模型提供商','仅在想使用 MaiBot 之外的提供商或模型时填写。');custom.append(toolbar(button('新增独立模型',()=>providerEditor(),'primary'),button('TOML 输入',providerToml)));
  const own=d.providers.filter(x=>x.source==='custom');if(!own.length)custom.append(el('p','还没有独立模型；上面的 MaiBot 模型可直接选择。','muted'));for(const x of own){const row=el('div','','record');row.append(el('h3',x.name),el('p',`${x.model} · ${protocols.find(p=>p[0]===x.protocol)?.[1]||x.protocol} · ${x.capabilities.map(k=>roles[k]).join(' / ')}`),el('p',x.key_configured?'密钥已保存，不回显':'尚未保存密钥','muted'),toolbar(button('编辑',()=>providerEditor(x)),button('删除未使用条目',async()=>{await call('action','provider_remove',{provider_id:x.id});await refresh();})));custom.append(row);}main.append(custom,await moduleForm('models'));
  const details=el('details'),table=el('table'),head=el('tr');for(const label of ['宿主模型','实际模型标识','提供商'])head.append(el('th',label));table.append(head);for(const m of d.catalog.models){const r=el('tr');r.append(el('td',m.name),el('td',m.model_identifier),el('td',m.api_provider));table.append(r);}details.append(el('summary',`${d.catalog.status} · ${d.catalog.models.length} 个模型`),table);main.append(details);
}
function modelTestText(t){if(!t)return '尚未检查连接';if(t.state==='queued')return '等待连接检查，不占生活额度';if(t.state==='running')return '正在连接…';if(t.state==='succeeded')return `已连通 · 延迟 ${t.latency_ms??'—'} ms · ${stamp(t.updated)}`;return `连接失败 · ${t.error_code||'MODEL_ERROR'}${t.http_status?' / HTTP '+t.http_status:''} · ${t.error||'请查看模型配置'}${t.latency_ms!=null?' · '+t.latency_ms+' ms':''}`;}
function modelRoutingToml(data){const text=el('textarea','','code-editor');text.spellcheck=false;text.value='[routes]\n'+Object.entries(data.routes).map(([k,v])=>k+' = '+JSON.stringify(v)).join('\n')+'\n\n'+['audio','video'].map(role=>'[host_options.'+role+']\nprotocol = '+JSON.stringify(data.host_options?.[role]?.protocol||'openai_compatible')+'\nvideo_has_audio = '+String(!!data.host_options?.[role]?.video_has_audio)+'\nvideo_fps = '+String(data.host_options?.[role]?.video_fps??1)+'\nvideo_max_frames = '+String(data.host_options?.[role]?.video_max_frames??16)+'\nqwen_disable_thinking = '+String(data.host_options?.[role]?.qwen_disable_thinking!==false)).join('\n\n')+'\n';modal('模型选择 · TOML',el('p','host: 后面填写上方 MaiBot 模型目录里的名称；disabled 表示关闭。此文档不包含密钥。','hint'),text,button('保存模型选择',async()=>{await call('action','model_routes',{data:text.value});$('#details').close();notify('模型选择已保存');await refresh();},'primary'));}
function providerEditor(existing={}){
  const id=existing.id||'custom_'+Date.now().toString(36),name=input(existing.name||''),url=input(existing.base_url||''),model=input(existing.model||''),key=input('','password'),protocol=select(protocols,existing.protocol||'openai_compatible');url.placeholder='https://提供商地址/v1';model.placeholder='实际模型标识';key.autocomplete='new-password';key.placeholder=existing.key_configured?'已保存，留空保留':'仅提交至后端保存';
  const caps=el('div','','toolbar'),checks={};for(const[role,title]of Object.entries(roles)){const c=toggle(title,(existing.capabilities||['text']).includes(role));caps.append(c.node);checks[role]=c.input;}
  const fps=input(existing.video_fps??1,'number'),frames=input(existing.video_max_frames??16,'number'),thinking=toggle('关闭 Qwen 可切换思考模式',existing.qwen_disable_thinking!==false),qwenFields=el('div');fps.min='0.1';fps.max='10';fps.step='0.1';frames.min='1';frames.max='64';const nativeFrames=field('DashScope 视频最大抽帧数',frames);qwenFields.append(field('Qwen 视频 FPS',fps),nativeFrames,thinking.node);const updateQwen=()=>{qwenFields.hidden=!['qwen_compatible','dashscope'].includes(protocol.value);nativeFrames.hidden=protocol.value!=='dashscope';};protocol.onchange=updateQwen;updateQwen();
  const audio=toggle('视频模型同时读取真实音轨',!!existing.video_has_audio),clear=toggle('清除旧密钥',false),grid=el('div','','form-grid');for(const[label,node]of [['显示名称',name],['Base URL',url],['模型标识',model],['协议',protocol],['API Key',key]])grid.append(field(label,node));
  modal(existing.id?'编辑独立模型':'新增独立模型',grid,qwenFields,el('p','声明此模型支持的用途（仍须真实检查）','muted'),caps,audio.node,clear.node,el('p','修改地址需要重新填写密钥，避免将旧密钥发给其他服务。','hint'),button('保存模型',async()=>{await call('action','provider_save',{data:JSON.stringify({id,name:name.value.trim(),source:'custom',protocol:protocol.value,base_url:url.value.trim(),model:model.value.trim(),capabilities:Object.keys(checks).filter(k=>checks[k].checked),video_has_audio:audio.input.checked,video_fps:Number(fps.value),video_max_frames:Number(frames.value),qwen_disable_thinking:thinking.input.checked,api_key:key.value,clear_key:clear.input.checked})});key.value='';$('#details').close();notify('独立模型已保存，可在上方用途下拉框选择');await refresh();},'primary'));
}
function providerToml(){const t=el('textarea','','code-editor');t.spellcheck=false;t.value='# API Key 仅提交后端，不进入配置导出\nid = "my_audio"\nname = "我的音频模型"\nsource = "custom"\nprotocol = "openai_compatible"\nbase_url = "https://api.example.com/v1"\nmodel = "实际模型标识"\ncapabilities = ["audio"]\nvideo_has_audio = false\napi_key = ""\n';modal('用 TOML 添加独立模型',t,button('校验并保存模型',async()=>{await call('action','provider_save',{data:t.value});t.value='';$('#details').close();notify('独立模型已保存');await refresh();},'primary'));}

async function accountPanel(){
  const d=await loadSettings(),p=panel('B 站账号'),a=el('div','','account'),avatar=el('div',(d.account.name||'B').slice(0,1),'avatar'),text=el('div');
  text.append(el('h3',d.account.name||'尚未登录'),el('p',d.account.uid?'UID '+d.account.uid:'独立扫码，凭据只保存在服务器插件私有目录','muted'));
  a.append(avatar,text,badge(d.account.state));
  p.append(a,toolbar(button('扫码登录',()=>enqueue('login')),button('检查账号',()=>enqueue('account')),button('解除平台暂停',()=>control('clear_restriction')),button('退出账号',()=>control('logout'))));
  const qr=el('div');qr.id='qr-panel';p.append(qr);await updateQR(qr);return p;
}
async function updateQR(panel=$('#qr-panel')){if(!panel)return;const qr=await call('query','qr');panel.replaceChildren();if(qr.image){const img=el('img','','qr');img.src=qr.image;img.alt='B站登录二维码';panel.append(img,el('p','二维码有效至 '+stamp(qr.expires),'muted'));}else if(qr.state!=='idle')panel.append(el('p',qr.state==='authenticated'?'扫码登录成功':qr.state==='waiting'?'二维码准备中':qr.state==='expired_or_failed'?'二维码已过期或失败，请重新生成':qr.state,'muted'));}
async function settings(){main.append(await accountPanel());const title=el('div','','section-title');title.append(el('h2','所有模块的设置总览'),button('统一 TOML 编辑 / 导出',()=>tomlEditor()));main.append(title,await moduleTiles(true),el('p','常用选项在对应模块中设置；TOML 与表单共用后端校验和持久化。','hint'));}
async function logs(){main.append(toolbar(button('存储与保留设置',()=>openModule('resources')),button('在线备份',async()=>{const r=await call('action','backup');notify('在线备份完成：'+r.path+'；账号和模型密钥不包含在导出中。');}),button('脱敏导出',async()=>{const r=await call('query','export_logs'),text=el('textarea','','code-editor');text.value=JSON.stringify(r,null,2);text.readOnly=true;modal('脱敏运行记录',text,button('全选文本',()=>{text.focus();text.select();}));})),tabs([['logs','执行审计'],['models','模型调用']],section||'logs'));await records(section==='models'?'models':'logs');}

async function records(which,kind=''){
  const data=await call('query','snapshot',{section:kind?(kind==='reply'?'reply_drafts':'comment_drafts'):which,page:number}),p=panel(({browse:'视频经历',comment_reads:'只读评论经历',interactions:'评论与回复草稿',operations:'发布操作',threads:'收到的相关回复',memories:'我的经历记忆',changes:'兴趣变化记录',persona_history:'人格干预历史',summaries:'每日复盘',plans:'计划版本',logs:'审计记录',models:'模型调用'})[which]||'记录');
  const rows=kind?data.items.filter(x=>x.kind===kind):data.items;
  if(!rows.length)p.append(el('div','这里还没有相关记录。','empty'));
  for(const r of rows){const row=el('article','','record'),head=el('div','','row-head'),id=r.id||r.day;head.append(el('h3',r.title||r.day||r.topic||kinds[r.type]||r.kind||r.stage||'经历'));if(r.state||r.status)head.append(badge(r.state||r.status));row.append(head);
    if(r.created||r.updated)row.append(el('p',stamp(r.updated||r.created),'meta'));for(const key of ['text','reason','coverage','error'])if(r[key])row.append(el('p',String(r[key]).slice(0,650),key==='error'?'notice error':''));
    if(which==='memories')row.append(el('p',`上下文：${states[r.context_status]} · 宿主长期写入：${states[r.host_status]}`,'muted'));
    if(which==='changes')row.append(el('p',`${Math.round(r.old*100)}% → ${Math.round(r.new*100)}%${r.rolled_back?' · 已回滚':''}`));
    if(which==='plans')row.append(el('p',`第 ${r.version} 版 · ${r.active?'当前有效':'历史记录'}`));
    if(which==='models')row.append(el('p',`${r.model||'未返回模型名'} · 提示词 ${r.prompt_version} · ${r.duration.toFixed(1)} 秒`),expandable('调用统计',r.usage));
    if(which==='logs')row.append(expandable('查看脱敏内容',r.data));
    const actions=el('div','','record-actions');if(!['logs','models'].includes(which))actions.append(button('查看详情',()=>showDetail(which,String(id))));
    if(which==='changes'&&!r.rolled_back)actions.append(button('回滚',async()=>{await call('action','interest_action',{record_id:r.id,action:'rollback'});await refresh();}));
    if(which==='persona_history'&&!r.rolled_back)actions.append(button('回滚此干预',async()=>{await call('action','interest_action',{record_id:r.id,action:'rollback_guidance'});notify('已回滚人格干预');await refresh();}));
    if(which==='interactions'&&r.status==='draft')actions.append(button('编辑与发布',()=>showDetail(which,r.id),'primary'));
    row.append(actions);p.append(row);
  }
  const paging=toolbar(button('上一页',async()=>{number=Math.max(1,number-1);await refresh();}),el('span',`${data.page} / ${data.pages} 页 · ${data.total} 条`,'muted'),button('下一页',async()=>{number=Math.min(data.pages,number+1);await refresh();}));paging.classList.add('pagination');p.append(paging);main.append(p);
}
async function showDetail(which,id){
  const r=await call('query','detail',{section:which,record_id:id}),body=modal(({browse:'浏览与理解',comment_reads:'只读评论',interactions:'编辑草稿',memories:'经历记忆',schedule:'日程详情',summaries:'每日复盘',plans:'计划版本',persona_history:'人格干预历史'})[which]||'记录详情');
  if(which==='comment_reads'){body.append(el('h3',r.metadata.title),link(r.metadata.url),badge(r.state),el('p','仅阅读评论区，未观看视频','hint'),el('p',r.analysis.summary||r.reason),el('p',r.analysis.feeling||''),expandable('评论原文与理解依据',{comments:r.comments,analysis:r.analysis}));
  }else if(which==='browse'){
    body.append(el('h3',r.payload.title),link(r.payload.url),badge(r.state),el('p',r.evidence.coverage||r.reason,'hint'),el('p',r.analysis.summary||'尚无理解结果'),el('p',r.analysis.feeling||''));
    if(r.evidence.limitations?.length)body.append(el('p',r.evidence.limitations.join('；'),'muted'));
    body.append(expandable('证据、覆盖范围与主张区分',{evidence:r.evidence,analysis:r.analysis,memory:r.memory_status}),toolbar(button('生成评论草稿',()=>enqueue('draft',{video_id:r.id})),button('保存记忆',()=>enqueue('memory',{video_id:r.id})),button('填写人工草稿',()=>manualDraft(r.id))));
  }else if(which==='interactions'){
    const t=el('textarea');t.value=r.text;t.maxLength=350;body.append(el('p',r.video_id),badge(r.status),el('p','依据：'+r.quote,'hint'),el('p',r.reason,'muted'),field('草稿正文',t),expandable('回复线程',{root:r.root,parent:r.parent,target_uid:r.target_uid}));
    if(r.status==='draft')body.append(toolbar(button('保存草稿',async()=>{await call('action','draft_save',{draft_id:r.id,text:t.value});notify('草稿已保存');}),button('发布这一条',async()=>{await call('action','draft_save',{draft_id:r.id,text:t.value});const result=await call('action','publish',{draft_id:r.id});pendingJob={id:result.task_id,label:'发布'};$('#details').close();notify('已提交发布操作，请在发布记录中核对结果');},'primary')));
  }else if(which==='memories'){
    const t=el('textarea');t.value=r.text;t.maxLength=1200;body.append(link(r.source),el('p',`可检索：${states[r.status]} · 上下文：${states[r.context_status]} · 长期写入：${states[r.host_status]}`,'hint'),field('记忆正文',t),toolbar(button('纠错保存',async()=>{await call('action','memory_edit',{memory_id:r.id,text:t.value,action:'correct'});notify('记忆已纠正；历史已追加的上下文不会自动撤回');$('#details').close();await refresh();},'primary'),button('遗忘这段经历',async()=>{await call('action','memory_edit',{memory_id:r.id,action:'forget'});$('#details').close();await refresh();})));
  }else if(which==='schedule'){
    body.append(el('h3',kinds[r.type]),badge(r.state),el('p',stamp(r.earliest)+' → '+stamp(r.deadline)),el('p',taskTitle(r)),el('p',r.payload.reason||'','muted'));if(r.error)body.append(el('p',r.error,'notice error'));
    const actions=toolbar();for(const[action,title]of [['skip','跳过'],['cancel','取消'],['resume','重新排队']])actions.append(button(title,async()=>{await call('action','task_action',{task_id:r.id,action});notify('日程状态已更新');$('#details').close();await refresh();}));body.append(actions,expandable('执行结果与尝试',{result:r.result,attempts:r.attempts_log,dependencies:r.dependencies}));
  }else if(which==='summaries'){
    body.append(el('h3',r.day),el('p',r.text));const d=r.payload.review||{};for(const[key,title]of [['learned','学到的内容'],['unresolved','还想弄清的问题'],['tomorrow','明天的建议']]){if(d[key]?.length){body.append(el('h3',title));const list=el('ul');for(const text of d[key])list.append(el('li',text));body.append(list);}}if(r.payload.degraded)body.append(el('p','此复盘已保守降级：'+r.payload.validation_error,'hint'));body.append(expandable('实际记录与消耗',r.payload.records));
  }else if(which==='plans'){
    body.append(el('h3',r.payload.theme),el('p',`版本 ${r.version} · ${r.reason}`));for(const t of r.payload.tasks||[])body.append(el('p',`${kinds[t.type]} · ${t.goal} · ${t.reason}`));body.append(expandable('结构化计划',r.payload));
  }else body.append(expandable('完整记录',r));
}
function manualDraft(video_id){const t=el('textarea');t.maxLength=350;modal('填写人工评论草稿',field('正文',t),el('p','保存不会发布；发布时仍会检查证据、账号、额度、间隔和去重。','hint'),button('保存草稿',async()=>{const r=await call('action','manual_draft',{video_id,text:t.value});notify('人工草稿已保存 · '+r.draft_id);$('#details').close();},'primary'));}

async function refresh(){if(busy)return;busy=true;settingsData=null;$('#refresh').disabled=true;main.setAttribute('aria-busy','true');try{main.replaceChildren();const views={overview,schedule,browse,interactions,memories,interests,providers,settings,logs};await (views[page]||overview)();}catch(e){notify(e.message,true);main.append(el('div','读取失败，请刷新重试。不会用示例数据代替真实状态。','empty'));}finally{busy=false;$('#refresh').disabled=false;main.removeAttribute('aria-busy');}}
let polling=false;
async function poll(){if(polling||busy||$('#details').open)return;polling=true;try{
  if(pendingJob){const task=await call('query','detail',{section:'schedule',record_id:pendingJob.id});if(!['queued','running','retry'].includes(task.state)){const label=pendingJob.label;pendingJob=null;notify(label+'：'+(states[task.state]||task.state)+(task.error?' · '+task.error:''),!['succeeded','cancelled'].includes(task.state));if(page==='schedule'||page==='overview')await refresh();}}
  if(page==='providers'){const d=await call('query','model_providers');for(const role of Object.keys(roles)){const n=$('#model-status-'+role);if(n)n.textContent=modelTestText(d.tests[role]);}}
  if(page==='settings')await updateQR();
}catch(e){notify(e.message,true);}finally{polling=false;}}

$('#pause').onclick=()=>perform(async()=>{const d=await call('query','snapshot',{section:'overview'});await control(d.paused?'resume':'pause');});
$('#stop').onclick=()=>perform(async()=>{const d=await call('query','snapshot',{section:'overview'});await control(d.posting_stopped?'resume_posting':'stop_posting');});
$('#refresh').onclick=()=>perform(refresh);
$('#close').onclick=()=>$('#details').close();
try{await maibot.ready;page=maibot.page.id;const state=maibot.page.state||{};section=state.section||state.module||'';for(const[id,title]of pages){const b=button(title,()=>nav(id));if(id===page)b.setAttribute('aria-current','page');$('#nav').append(b);}notify('已连接 MaiBot · 配置与记录实时保存');await refresh();setInterval(poll,5000);}catch(e){notify('连接 MaiBot 失败：'+e.message,true);}

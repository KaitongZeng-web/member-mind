'use strict';
let chatSettings=window.initialChatSettings||null,chatTurns=[],chatText='',chatSending=false,chatError='',chatPoll=null;
let chatPollFailures=0,chatLoadGeneration=0,chatConnectionLost=false;
const chatKeyEditing=new Set(),chatKeyDrafts={deepseek:'',qcc:''};
let chatSession=sessionStorage.getItem('qcc-chat-session')||crypto.randomUUID();
let chatServices=new Set(['qcc-company','qcc-risk','qcc-ipr','qcc-operation','qcc-history','qcc-executive']);
const chatReviews=new Map();
function chatReviewState(turnId){if(!chatReviews.has(turnId))chatReviews.set(turnId,{selected:new Set(),reviewed:false,reviewer:'核验员01'});return chatReviews.get(turnId);}
nav.splice(1,0,['chat','✧','聊天导入']);
views.chat=chatPage;
const overviewBeforeChat=overview;
overview=()=>overviewBeforeChat().replace('data-act="go-data"','data-act="go-chat"').replace('<div class="stats">',
  `<div class="chat-entry"><div><span class="eyebrow">自然语言 · 企查查 MCP · DEEPSEEK</span><h2>说出企业线索，整理成可入库的资料</h2><p>描述要查询的企业和资料需求，查看来源与字段，决定哪些资料加入工作空间。</p></div><div class="actions">${act('打开聊天导入','go-chat',true)}${act('CSV 文件导入','go-data')}</div></div><div class="stats">`);
views.overview=overview;
const goBeforeChat=go;
go=function(id){goBeforeChat(id);if(id==='chat')loadChat().catch(chatConnectionError);};
function chatConnectionError(error){chatError=error.message;chatConnectionLost=!!error.connectionLost;if(page==='chat')render();}

function chatKeyControl(provider,label){
  const saved=chatSettings?.[provider+'_configured'],editing=chatKeyEditing.has(provider);
  if(saved&&!editing)return `<div class="saved-credential"><div><b>${E(label)}</b><span>已保存，刷新后继续使用</span><strong>${E(chatSettings[provider+'_key_hint']||'••••••••')}</strong></div><button class="btn" data-chat-change-key="${provider}">更换密钥</button></div>`;
  return `<div class="credential-editor">${field(label+(saved?'新密钥':'密钥'),'chat-'+provider+'-key',chatKeyDrafts[provider],'password')}${saved?`<button class="btn" data-chat-cancel-key="${provider}">取消更换</button>`:''}</div>`;
}

function chatConnectionSettings(){
  if(!chatSettings)return '<p class="sub" role="status">正在读取已保存的连接配置…</p>';
  const editing=chatKeyEditing.size||!chatSettings.deepseek_configured||!chatSettings.qcc_configured;
  return chatKeyControl('deepseek','DeepSeek')+chatKeyControl('qcc','企查查')+
    `<input type="hidden" id="chat-model" value="${E(chatSettings.model||'deepseek-flash')}"><p class="sub">密钥在本机持久保存，刷新页面或重启服务后仍可使用。为保护密钥，仅显示掩码；更换后点击保存即可。</p>`+
    (editing?act('保存密钥与连接设置','chat-save-settings',true):'<p class="credential-saved-note">连接配置已保存，无需重复填写。</p>');
}

function chatPage(){
  const settings=chatSettings;
  return heading('聊天导入企业资料','说出企业线索，选择匹配企业，核对资料后添加到工作空间。',act('新建对话','chat-new'))+
    `<div class="chat-layout"><div class="chat-main">`+
    card('资料助理',`<div class="chat-transcript" aria-live="polite">${chatTurns.length?chatTurns.map(chatTurnView).join(''):
      `<div class="chat-welcome"><span class="chat-spark">✧</span><h2>想了解一家企业，还是寻找一类企业？</h2><p>可以输入企业名称，也可以描述地区与主营业务；系统会扩展线索，再查询资料核对。</p><div class="chat-examples">${[
        '深圳无人机公司，优先推荐主营业务相关、行业较知名的企业。',
        '我想导入一家企业，先帮我核实企业全称、经营状态和资质，缺失资料标为待核实。'
      ].map((t,i)=>`<button class="btn chat-example" data-chat-example="${i}">${E(t)}</button>`).join('')}</div></div>`}</div>
      ${chatError?`<div class="warning" role="alert">${E(chatError)}${chatConnectionLost?`<div class="actions">${act('重新连接','chat-reconnect')}</div>`:''}</div>`:''}
      <label class="field chat-composer">描述要导入的企业资料<textarea id="chat-text" maxlength="6000" placeholder="例如：深圳无人机公司，优先推荐主营业务相关、行业较知名的企业。" ${chatSending?'disabled':''}>${E(chatText)}</textarea></label>
      <div class="chat-send"><p class="sub">可输入企业名称，也可描述地区与业务，例如“深圳无人机公司”。问题和查询结果由 DeepSeek 分析，每轮最多 18 次资料查询，产生相应服务用量。</p><button class="btn primary" data-act="chat-send" ${chatSending?'disabled':''}>${chatSending?'正在查询与整理…':'发送并生成待审资料'}</button></div>`)+
    `</div><div class="chat-side">`+
    card('连接与模型',chatConnectionSettings())+
    card('本轮查询范围',`<div class="chat-services">${(settings?.services||[]).map(x=>`<label><input type="checkbox" data-chat-service="${E(x.id)}" ${chatServices.has(x.id)?'checked':''} ${chatSending||x.id==='qcc-company'?'disabled':''}> ${E(x.name)}</label>`).join('')}</div><p class="sub">企业工商用于核实主体，其他服务按需选择。发送消息后才查询企业。</p>${act('查看数据连接','go-mcp')}`)+
    card('资料入库原则','<p>查询结果先留在待审区，核对并勾选后才写入企业档案。</p><p>已有资料的差异进入核对队列，会员、续费和联系情况须有业务依据。</p>'+act('查询与入库说明','chat-prompts'))+
    card('最近对话',(settings?.sessions||[]).map(x=>`<button class="chat-history" data-chat-session="${E(x.id)}">${E(x.text)}<small>${E(new Date(x.created_at).toLocaleString('zh-CN'))}</small></button>`).join('')||'<p class="sub">尚无历史对话</p>')+
    `</div></div>`;
}

function chatTurnView(turn){
  const running=['pending','running'].includes(turn.status);
  return `<article class="chat-turn"><div class="chat-bubble user"><small>你</small><p>${E(turn.text)}</p></div>
    <div class="chat-bubble assistant"><small>资料助理</small><p>${E(running?'正在查询并整理资料，请稍候…':window.businessText?.(turn.status==='failed'?turn.error:turn.reply)||(turn.status==='failed'?turn.error:turn.reply))}</p>
    ${turn.search_intent?`<div class="search-understanding"><b>本次检索理解</b><p>${E(turn.search_intent.summary)}</p><small>先扩展企业线索，再核对工商注册地区和主营业务；本轮结果不是完整行业名录。</small></div>`:''}
    ${(turn.drafts||[]).length?`<div class="chat-candidates"><h3>匹配企业 · ${turn.drafts.length} 家</h3><p class="sub">点击企业名称前的方框选择；展开字段可核对原文。</p>${turn.drafts.map(d=>chatDraftView(d,turn.id)).join('')}
      ${chatReviewBar(turn)}<div class="actions"><button class="btn" data-chat-export="${E(turn.id)}" data-format="table">导出企业资料表</button></div></div>`:''}
    </div>
    ${turn.search_intent?.excluded?.length?`<details class="chat-trace"><summary>未纳入本轮匹配的企业</summary>${turn.search_intent.excluded.map(x=>`<p><b>${E(x.company)}</b>：${E(x.reason)}</p>`).join('')}</details>`:''}
    ${turn.trace.length?`<details class="chat-trace" ${running?'open':''}><summary>查询过程</summary>${turn.trace.map(x=>`<div class="list-item"><b>${E(window.businessSourceLabel?.(x.server_id)||'企业资料查询')}</b> ${tag({success:'已完成',failed:'需要检查',pending:'查询中',unknown:'结果待确认'}[x.status]||'待确认')}<p class="sub">${E(window.businessText?.(x.message||'正在查询')||x.message||'正在查询')}</p>${x.arguments?.searchKey?`<p>查询企业：${E(x.arguments.searchKey)}</p>`:''}</div>`).join('')}</details>`:''}
    ${turn.sources.length?`<details class="chat-sources"><summary>来源资料 · ${turn.sources.length} 份</summary>${turn.sources.map(x=>`<div class="evidence"><b>${E(x.kind==='operator'?'操作员提供的说明':window.businessSourceLabel?.(x.server_id)||'企查查公开资料')}</b>${window.businessSourceHtml?.(x.text)||'<p>请查看企业卡片中的字段依据。</p>'}</div>`).join('')}</details>`:''}
    </article>`;
}

function chatDraftView(draft,turnId){
  const done=draft.status==='imported';
  const state=chatReviewState(turnId);
  return `<div class="chat-draft"><div class="chat-draft-head"><label><input type="checkbox" data-chat-draft="${E(draft.id)}" data-chat-turn="${E(turnId)}" ${state.selected.has(draft.id)?'checked':''} ${done||!draft.importable?'disabled':''}> <b>${E(draft.fields.companyName||'未确认企业主体')}</b></label>${tag(done?'已入库':draft.importable?'可勾选入库':'资料不足')}</div>
    <div class="chat-draft-summary"><span><small>信用代码</small>${E(draft.fields.creditCode)}</span><span><small>经营状态</small>${E(draft.fields.operation)}</span>${draft.fields.region?`<span><small>注册地区</small>${E(draft.fields.region)}</span>`:''}${draft.fields.industry?`<span><small>行业</small>${E(draft.fields.industry)}</span>`:''}${draft.match?`<span><small>匹配依据</small>相关结果第 ${E(draft.match.rank)} 位 · ${E(draft.match.basis)}</span>`:''}</div>
    ${draft.fields.mainBusiness?`<p class="chat-business-line"><b>主营业务：</b>${E(draft.fields.mainBusiness)}</p>`:''}
    ${draft.match?.explanation?`<div class="match-explanation"><b>为什么推荐</b><p>${E(draft.match.explanation)}</p>${draft.match.business_evidence?`<p class="sub">业务依据：${E(draft.match.business_evidence.quote)}</p>`:''}${draft.match.influence_evidence?`<p class="sub">规模与行业影响力参考：${E(draft.match.influence_evidence.quote)}</p>`:''}</div>`:''}
    ${Object.keys(draft.match_details||{}).length?`<p class="chat-match-details">${Object.entries(draft.match_details).map(([k,v])=>`${E(k)}：${E(Array.isArray(v)?v.join('、'):v)}`).join(' · ')}</p>`:''}
    <details class="chat-draft-fields"><summary>查看企业资料与来源说明</summary>${tbl(['资料内容','候选值','来源说明'],Object.entries(D.fields).filter(([key])=>!['id','source'].includes(key)).map(([key,label])=>[
      E(label),E(draft.fields[key]??draftFieldState(draft,key).label),
      draft.citations[key]?`<span class="sub">${E(draft.citations[key].kind==='operator'?'操作员业务说明':window.businessSourceLabel?.(draft.citations[key].server_id)||'企查查公开资料')}</span><p>${E(window.businessText?.(draft.citations[key].quote)||draft.citations[key].quote)}</p>`:E(draftFieldState(draft,key).reason)]))}
    ${draft.notes.length?`<div class="warning">${draft.notes.map(n=>E(window.businessText?.(n)||n)).join('<br>')}</div>`:''}
    ${draft.retrieved_at?`<p class="sub">公开资料获取时间：${E(new Date(draft.retrieved_at).toLocaleString('zh-CN'))}</p>`:''}<p class="sub">会员、续费和实际联系情况由内部业务记录确认；公开信息不足会单独注明，不统一当作“待核实”。</p></details>
    ${!done?`<div class="chat-enrich-action"><button class="btn" data-chat-enrich="${E(draft.id)}">补全公开资料</button><small>查询工商登记与主营业务，不自动入库。</small></div>`:''}
    ${done?`<p class="sub">已由 ${E(draft.reviewed_by)} 确认并添加到企业档案。</p>`:''}</div>`;
}

function draftFieldState(draft,key){
  if(draft.field_states?.[key])return draft.field_states[key];
  if(['member','renewal','reachable'].includes(key))return {label:'需内部确认',reason:'需会员台账、续费凭证或实际联系记录，公开查询不能推断。'};
  if(key==='risk')return {label:'需人工核验',reason:'需要独立的风险核验依据。'};
  if(['qualification','qualificationUntil'].includes(key))return {label:'需专项核验',reason:'需要明确的资质认证资料。'};
  if(key==='sourceDate')return {label:'资料日期未提供',reason:'获取时间与资料日期分开保存。'};
  return {label:'尚未补查',reason:'当前来源没有提供该项，可补全公开资料后再核对。'};
}

function chatReviewBar(turn){
  const state=chatReviewState(turn.id),eligible=turn.drafts.filter(d=>d.status==='pending'&&d.importable);
  const count=eligible.filter(d=>state.selected.has(d.id)).length;
  if(!eligible.length)return '<p class="sub">本条回复的资料均已处理，没有待入库项。</p>';
  return `<div class="chat-review-bar" data-chat-review="${E(turn.id)}"><div><b>本条回复已选 ${count} 家企业</b><p class="sub">仅添加本条回复中勾选的企业；已有档案的差异进入核对队列。</p></div>
    <div class="actions"><button class="btn" data-chat-select-all="${E(turn.id)}">全选本次候选</button><button class="btn" data-chat-unselect="${E(turn.id)}">暂不添加</button></div>
    <label class="field">确认角色<input data-chat-reviewer="${E(turn.id)}" value="${E(state.reviewer)}"></label><label><input type="checkbox" data-chat-reviewed="${E(turn.id)}" ${state.reviewed?'checked':''}> 我已核对所选企业主体与字段来源</label>
    <button class="btn primary" data-chat-import="${E(turn.id)}" ${!count||!state.reviewed||chatSending?'disabled':''}>将所选 ${count} 家加入数据库</button></div>`;
}

async function loadChat(){
  clearTimeout(chatPoll);
  const generation=++chatLoadGeneration,session=chatSession;
  const [settings,history]=await Promise.all([api('/api/chat/settings'),api('/api/chat/sessions/'+session)]);
  if(generation!==chatLoadGeneration||session!==chatSession)return;
  chatSettings=settings;chatTurns=history.turns;chatError='';chatConnectionLost=false;chatPollFailures=0;
  const active=chatTurns.find(t=>['pending','running'].includes(t.status));
  chatSending=!!active;
  if(page==='chat')render();
  if(active)pollChat(active.id,session);
}
async function pollChat(id,session=chatSession){
  clearTimeout(chatPoll);
  if(session!==chatSession)return;
  try{
    const turn=await api('/api/chat/turns/'+id);
    if(session!==chatSession)return;
    const index=chatTurns.findIndex(t=>t.id===id);
    if(index<0)chatTurns.push(turn);else chatTurns[index]=turn;
    chatSending=['pending','running'].includes(turn.status);chatError='';chatConnectionLost=false;chatPollFailures=0;
    if(page==='chat')render();
    if(chatSending)chatPoll=setTimeout(()=>pollChat(id,session),1600);
    else {chatSettings=await api('/api/chat/settings');if(page==='chat')render();}
  }catch(e){
    if(session!==chatSession)return;
    chatConnectionError(e);
    // Polling reads may reconnect; never resubmit the company query automatically.
    if(e.connectionLost&&chatSending&&++chatPollFailures<=6)chatPoll=setTimeout(()=>pollChat(id,session),Math.min(1500*2**chatPollFailures,15000));
  }
}
Object.assign(handlers,{
  'go-chat':()=>go('chat'),
  'chat-reconnect':()=>operate(async()=>{try{await loadChat();toast('已重新连接，查询记录已同步');}catch(e){chatConnectionError(e);}}),
  'chat-save-settings':()=>operate(async()=>{
    await api('/api/chat/settings',{deepseek_key:val('chat-deepseek-key'),qcc_key:val('chat-qcc-key'),model:val('chat-model')});
    chatSettings=await api('/api/chat/settings');chatKeyEditing.clear();chatKeyDrafts.deepseek=chatKeyDrafts.qcc='';chatError='';render();toast('密钥已持久保存，刷新或重启后仍可使用');
  }),
  'chat-send':()=>operate(async()=>{
    if(chatSending)throw Error('当前查询尚未结束');
    chatText=val('chat-text');
    if(!chatText.trim())throw Error('请先描述要导入的企业资料');
    chatError='';
    sessionStorage.setItem('qcc-chat-session',chatSession);
    let turn;
    try{turn=await api('/api/chat/turns',{session_id:chatSession,request_id:crypto.randomUUID(),text:chatText,services:[...chatServices]});}
    catch(e){if(e.connectionLost)chatSending=true;chatConnectionError(e);throw e;}
    chatTurns.push(turn);chatText='';chatSending=true;chatPollFailures=0;chatConnectionLost=false;render();pollChat(turn.id);
  }),
  'chat-new':()=>{if(chatSending)return toast('请等待当前查询结束');chatSession=crypto.randomUUID();sessionStorage.setItem('qcc-chat-session',chatSession);chatTurns=[];chatText='';chatError='';chatReviews.clear();render();},
  'chat-prompts':()=>operate(async()=>{const p=await api('/api/chat/prompts');showModal('聊天导入提示词',`<h3>查询与 MCP 调用</h3><pre class="code">${E(p.query)}</pre><h3>字段结构化与引用约束</h3><pre class="code">${E(p.structure)}</pre><details><summary>数据库字段与枚举契约</summary><pre class="code">${E(JSON.stringify({fields:p.fields,enums:p.enums},null,2))}</pre></details>`);})
});
async function importChatTurn(turnId){return operate(async()=>{
  const state=chatReviewState(turnId),turn=chatTurns.find(t=>t.id===turnId);
  const ids=(turn?.drafts||[]).filter(d=>d.status==='pending'&&d.importable&&state.selected.has(d.id)).map(d=>d.id);
  if(!state.reviewed||!ids.length)throw Error('请在这条回复中勾选企业，并确认已经核对来源');
  const out=await command('chat_import_apply',{draft_ids:ids,confirmed:true,who:state.reviewer});
  state.selected.clear();state.reviewed=false;await loadChat();render();toast('已确认 '+out.entity_ids.length+' 家企业'+(out.conflicts?'；'+out.conflicts+' 个差异字段待核对':''));
});}
document.addEventListener('input',e=>{if(e.target.id==='chat-text')chatText=e.target.value;if(e.target.dataset.chatReviewer)chatReviewState(e.target.dataset.chatReviewer).reviewer=e.target.value;if(e.target.id==='chat-deepseek-key')chatKeyDrafts.deepseek=e.target.value;if(e.target.id==='chat-qcc-key')chatKeyDrafts.qcc=e.target.value;});
document.addEventListener('change',e=>{
  const t=e.target;
  if(t.dataset.chatService){if(t.checked)chatServices.add(t.dataset.chatService);else chatServices.delete(t.dataset.chatService);}
  if(t.dataset.chatDraft){const state=chatReviewState(t.dataset.chatTurn);if(t.checked)state.selected.add(t.dataset.chatDraft);else state.selected.delete(t.dataset.chatDraft);state.reviewed=false;render();}
  if(t.dataset.chatReviewed){const state=chatReviewState(t.dataset.chatReviewed);state.reviewed=t.checked;const button=document.querySelector(`[data-chat-import="${t.dataset.chatReviewed}"]`);if(button)button.disabled=!state.selected.size||!state.reviewed||chatSending;}
});
document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.chatEnrich){if(chatSending)return toast('当前查询尚未完成，请稍候');const draft=chatTurns.flatMap(t=>t.drafts||[]).find(d=>d.id===b.dataset.chatEnrich);if(draft){chatText='补全'+draft.fields.companyName+'的工商资料和企业简介，整理行业、注册地区和主营业务。';$('#chat-text').value=chatText;handlers['chat-send']();}}
  if(['deepseek','qcc'].includes(b.dataset.chatChangeKey)){chatKeyEditing.add(b.dataset.chatChangeKey);render();}
  if(['deepseek','qcc'].includes(b.dataset.chatCancelKey)){chatKeyEditing.delete(b.dataset.chatCancelKey);chatKeyDrafts[b.dataset.chatCancelKey]='';render();}
  if(b.dataset.chatExport){const a=document.createElement('a');a.href='/api/chat/turns/'+b.dataset.chatExport+'/download?format='+b.dataset.format;a.download='企业资料表.csv';a.click();}
  if(b.dataset.chatImport)importChatTurn(b.dataset.chatImport);
  if(b.dataset.chatSelectAll){const state=chatReviewState(b.dataset.chatSelectAll),turn=chatTurns.find(t=>t.id===b.dataset.chatSelectAll);(turn?.drafts||[]).filter(d=>d.status==='pending'&&d.importable).forEach(d=>state.selected.add(d.id));state.reviewed=false;render();}
  if(b.dataset.chatUnselect){const state=chatReviewState(b.dataset.chatUnselect);state.selected.clear();state.reviewed=false;render();toast('本条回复未添加新的企业，候选仍保留。');}
  if(b.dataset.chatSession){if(chatSending)return toast('请等待当前查询结束');chatSession=b.dataset.chatSession;sessionStorage.setItem('qcc-chat-session',chatSession);chatReviews.clear();chatError='';loadChat().catch(chatConnectionError);}
  if(b.dataset.chatExample!==undefined){chatText=['深圳无人机公司，优先推荐主营业务相关、行业较知名的企业。','我想导入一家企业，先帮我核实企业全称、经营状态和资质，缺失资料标为待核实。'][Number(b.dataset.chatExample)];render();$('#chat-text').focus();}
});
render();

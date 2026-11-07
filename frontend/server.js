'use strict';
let busy=false,mcpData={servers:[],calls:[]},mcpTools=[],mcpEditing=null,mcpInvocation=null;
async function api(path,payload,refreshToken=true){
  let response,data;
  try{
    response=await fetch(path,{method:payload===undefined?'GET':'POST',headers:payload===undefined?{}:{'Content-Type':'application/json','X-Workspace-Token':window.workspaceToken},body:payload===undefined?undefined:JSON.stringify(payload),cache:'no-store'});
    data=await response.json();
  }catch(cause){
    const error=Error('暂时无法连接本地服务。请保持启动窗口运行，可点击“重新连接”查看查询进度。');
    error.connectionLost=true;throw error;
  }
  // The server rejects expired page credentials before executing any command.
  // Refresh only that explicit rejection; never replay an uncertain network write.
  if(response.status===403&&data.detail==='刷新页面获取当前请求凭证'&&payload!==undefined&&refreshToken){
    const current=await api('/api/workspace');window.workspaceToken=current.csrf_token;
    return api(path,payload,false);
  }
  if(!response.ok){
    if(data.workspace){s=data.workspace;updatePending=meetingConfirmed=null;if($('#modal').open)$('#modal').close();render();}
    const error=Error(typeof data.detail==='string'?data.detail:'服务暂时无法处理请求，请稍后重试');
    error.status=response.status;error.connectionLost=response.status>=500;throw error;
  }
  return data;
}
async function command(name,args={}){const data=await api('/api/commands',{request_id:crypto.randomUUID(),expected_revision:s._revision,command:name,args});s=data.workspace;return data.output;}
async function operate(fn){if(busy)return toast('上一项请求尚未结束');busy=true;document.body.classList.add('api-busy');try{await fn();}catch(e){toast(e.message||'服务器请求失败，未伪造成功');}finally{busy=false;document.body.classList.remove('api-busy');}}
async function loadWorkspace(){const data=await api('/api/workspace');s=data.workspace;window.workspaceToken=data.csrf_token;render();}
const originalOverview=overview;overview=()=>originalOverview()+card('服务器工作空间',`<p>Python / FastAPI · SQLite修订 ${s._revision} · 数据在服务器数据库保存，刷新、重启后仍保留。</p><div class="actions">${act('刷新服务器数据','server-refresh')}${act('备份整个工作空间JSON','backup')}${act('进入MCP配置','go-mcp')}</div><p class="sub">同一后端地址的不同浏览器读取同一工作空间。写入采用修订号与幂等请求；冲突时重新读取后由你确认操作。</p>`);views.overview=overview;
const originalRules=rules;rules=()=>originalRules().replace('存储：当前浏览器 localStorage','存储：Python / SQLite')+card('后端与MCP状态','<p>业务规则由Python执行；SQLite持久化。聊天导入已集成 DeepSeek 与六类企查查MCP；填写模型密钥后按需查询。原有简报仍为规则模板，客户通知为模拟，自动提醒未接入。</p>');views.rules=rules;
nav.push(['mcp','⇄','MCP接口配置']);views.mcp=mcpPage;
const oldGo=go;go=function(id){oldGo(id);if(id==='mcp')operate(async()=>{mcpData=await api('/api/mcp/servers');render();});};
function mcpPage(){const cfg=mcpEditing||{id:'',name:'',transport:'streamable_http',url:'',command:'',args:[],token_env:'',timeout:30,allowed_tools:[],enabled:false};return heading('MCP接口服务配置','配置保存在SQLite；保存不建立连接，后续整合时主动测试。通用密钥使用服务器环境变量；企查查密钥可在聊天导入中保存到本机后端。',act('新建配置','mcp-new'))+card('服务器列表',tbl(['名称','传输','启用状态','操作'],mcpData.servers.map(c=>[E(c.name),E(c.transport),tag(c.enabled?'已启用，尚需测试':'已禁用'),`<button class="btn" data-mcp-edit="${c.id}">编辑</button> <button class="btn" data-mcp-discover="${c.id}">测试连接与发现工具</button>`])))+card('连接参数',`<div class="form-grid">${field('服务器名称','m-name',cfg.name)}${select('传输方式','m-transport',['streamable_http','stdio'],cfg.transport)}${field('MCP端点URL（HTTP模式）','m-url',cfg.url)}${field('Bearer鉴权环境变量名（非密钥）','m-token',cfg.token_env)}${field('stdio启动程序（如@python）','m-command',cfg.command)}${field('超时秒数','m-timeout',cfg.timeout,'number')}</div>${textarea('stdio参数JSON数组（@project/表示项目相对路径）','m-args',JSON.stringify(cfg.args))}${textarea('允许调用的工具名称JSON数组（默认[]禁止调用）','m-allow',JSON.stringify(cfg.allowed_tools))}<label><input type="checkbox" id="m-enabled" ${cfg.enabled?'checked':''}> 启用（仍不会自动连接）</label><p class="sub">远程服务使用HTTPS；stdio运行你配置的本机程序，不使用shell拼接。OAuth交互登录尚未实现。仅填环境变量名，实际值在启动服务前设置。</p><div class="actions">${act('校验并保存配置','mcp-save',true)}${act('填入本地只读联调示例（不连接）','mcp-example')}</div>`)+(mcpTools.length?card('已发现工具（实际MCP返回）',mcpTools.map(t=>`<div class="list-item"><b>${E(t.name)}</b><p>${E(t.description)}</p><details><summary>参数JSON Schema</summary><pre class="code">${E(JSON.stringify(t.inputSchema,null,2))}</pre></details><button class="btn" data-mcp-tool="${E(t.name)}">填写参数并预览调用</button></div>`).join('')):'')+card('MCP连接与调用记录',mcpData.calls.map(c=>`<div class="list-item"><b>${E(c.server_id)} / ${E(c.tool)} ${tag(c.status)}</b><p class="sub">${E(c.started_at)} / ${E(c.id)}</p><p>${E(c.message)}</p>${c.result?`<details><summary>实际返回内容（未自动更新企业事实）</summary><pre class="code">${E(JSON.stringify(c.result,null,2))}</pre></details>`:''}</div>`).join('')||empty('尚无调用记录；未连接不表示连接失败。'));}
function inputMeeting(){return {name:val('m-name'),theme:val('m-theme'),date:val('m-date'),location:val('m-location'),history:$('#m-history').checked};}
async function serverNotice(confirm){if(!meetingDraft)throw Error('先匹配');if(JSON.stringify(inputMeeting())!==JSON.stringify(meetingDraft.info))throw Error('会议信息已修改，重新匹配');const ids=[...document.querySelectorAll('[data-recipient]:checked')].map(x=>x.dataset.recipient),body=val('m-body');if(!ids.length||!body)throw Error('名单与正文必填');if(confirm&&(!meetingDraft.info.date||!meetingDraft.info.location))throw Error('时间地点不完整，不能发送');return {source_request:meetingDraft.result.request_id,ids,body};}
// All business mutations below are sent to the server. No workspace PUT endpoint exists.
Object.assign(handlers,{
'server-refresh':()=>operate(loadWorkspace),'go-mcp':()=>go('mcp'),
preflight:()=>operate(async()=>{pendingImport=await api('/api/import/preview',{text:C.csv(inputRows),mapping});render();}),
'apply-import':()=>operate(async()=>{if(!pendingImport)throw Error('先预检');await command('import_apply',{batch_id:pendingImport.batch_id});pendingImport=inputRows=null;render();toast('已写入SQLite');}),
candidate:()=>operate(async()=>{await command('candidate',{id:val('e-company'),field:val('e-field'),value:val('e-value'),source:val('e-source'),date:val('e-date'),note:val('e-excerpt')});render();}),
'resolve-new':()=>serverResolve(true),'resolve-old':()=>serverResolve(false),
parse:()=>operate(async()=>{queryText=val('query');filterTree=null;filterResult=null;try{filterTree=(await api('/api/parse',{text:queryText})).tree;render();}catch(e){render();throw e;}}),
filter:()=>operate(async()=>{if(!filterTree)throw Error('先解析');filterResult=await command('filter',{text:queryText});render();}),
brief:()=>operate(async()=>{briefResult=await command('brief',{id:val('b-company'),purpose:val('b-purpose')});render();}),
'activation-result':()=>operate(async()=>openResult(await command('activation'))),
'company-result':()=>operate(async()=>openResult(await command('companies'))),
match:()=>operate(async()=>{meetingDraft=await command('meeting_match',{info:inputMeeting()});render();}),
'meeting-result':()=>operate(async()=>{const args=await serverNotice(false);openResult(await command('meeting_export',args));}),
'confirm-notice':()=>operate(async()=>{meetingConfirmed={...await serverNotice(true),request_key:crypto.randomUUID()};showModal('确认模拟通知',`<p>企业：${meetingConfirmed.ids.map(E).join('、')}</p><div class="report">${E(meetingConfirmed.body)}</div><p>渠道：本地模拟。服务器将再次检查资料版本与名单。</p>${act('确认此名单与正文，建立待发送批次','notice-create',true)}`);}),
'notice-create':()=>operate(async()=>{if(!meetingConfirmed)throw Error('确认已失效');await command('notice_create',{...meetingConfirmed,confirmed:true});meetingConfirmed=meetingDraft=null;$('#modal').close();render();}),
'update-apply':()=>operate(async()=>{const u=updatePending;if(!u)throw Error('先预览');await command('update',{id:u.id,field:u.k,value:u.v,note:u.note,source:u.source,date:u.date,who:u.who});updatePending=null;$('#modal').close();render();}),
'rules-save':()=>operate(async()=>{await command('rules',{rules:JSON.parse(val('rule-json'))});render();}),
'pack-create':()=>operate(async()=>{const id=val('p-entity'),source=s.results.find(r=>r.type==='拜访简报'&&r.payload.entity_id===id);const p=await command('pack_create',{id,goal:val('p-goal'),owner:val('p-new-owner'),due:val('p-new-due'),source_request:source?.request_id});selectedPack=p.pack_id;$('#modal').close();go('actions');}),
'pack-refresh':()=>operate(async()=>{await command('pack_refresh',{pack_id:selectedPack});render();}),
'pack-save':()=>operate(async()=>{await command('pack_save',{pack_id:selectedPack,owner:val('p-owner'),due:val('p-due'),appointment:val('p-appointment'),proof:val('p-proof'),when:val('p-when')});render();}),
'pack-finish-save':()=>operate(async()=>{await command('pack_finish',{pack_id:selectedPack,status:val('p-outcome'),proof:val('p-outcome-proof'),trigger:val('p-trigger')});$('#modal').close();render();}),
'visit-save':()=>operate(async()=>{await command('visit_create',{pack_id:selectedPack,actual_at:val('v-date'),original:val('v-original'),needs:val('v-needs'),promises:val('v-promises'),open:val('v-open')});render();}),
'visit-verify':()=>operate(async()=>{await command('visit_verify',{visit_id:visitToVerify,reviewer:val('v-reviewer'),note:val('v-verification')});$('#modal').close();render();}),
'task-create':()=>operate(async()=>{await command('task_create',{visit_id:taskVisit,deliverable:val('t-delivery'),owner:val('t-owner'),due:val('t-due'),dependency:val('t-dependency'),next_trigger:val('t-next')});$('#modal').close();render();}),
'task-complete':()=>operate(async()=>{await command('task_complete',{task_id:taskToComplete,owner:val('t-final-owner'),due:val('t-final-due'),proof:val('t-proof')});$('#modal').close();render();}),
backup:()=>{location.href='/api/backup';},
reset:()=>showModal('恢复服务器初始模拟数据',`<p>此操作会重置SQLite中的全部业务记录，所有访问此服务器的浏览器都会看到变化。请先备份。</p>${act('确认恢复服务器数据','reset-confirm',true)}`),
'reset-confirm':()=>operate(async()=>{await command('reset',{confirmed:true});filterTree=filterResult=briefResult=meetingDraft=selectedResult=null;selectedPack=null;$('#modal').close();render();}),
'download-result':()=>{if(selectedResult)location.href='/api/results/'+selectedResult.request_id+'/download';},
'download-readable':()=>{if(selectedResult)location.href='/api/results/'+selectedResult.request_id+'/download?format=readable';},
'mcp-new':()=>{mcpEditing=null;mcpTools=[];render();},
'mcp-example':()=>{mcpEditing={id:'',name:'本地只读模拟MCP（可选联调）',transport:'stdio',url:'',command:'@python',args:['@project/sample_mcp_server.py'],token_env:'',timeout:30,allowed_tools:['get_demo_profile'],enabled:false};render();},
'mcp-save':()=>operate(async()=>{mcpEditing=await api('/api/mcp/servers',{id:mcpEditing?.id||'',name:val('m-name'),transport:val('m-transport'),url:val('m-url'),command:val('m-command'),args:JSON.parse(val('m-args')),token_env:val('m-token'),timeout:Number(val('m-timeout')),allowed_tools:JSON.parse(val('m-allow')),enabled:$('#m-enabled').checked});mcpData=await api('/api/mcp/servers');render();toast('MCP配置已保存，没有自动连接');}),
'mcp-call-preview':()=>{const args=JSON.parse(val('mcp-arguments'));mcpInvocation.arguments=args;showModal('确认调用MCP工具',`<p>服务器：${E(mcpInvocation.server)}；工具：${E(mcpInvocation.tool)}</p><pre class="code">${E(JSON.stringify(args,null,2))}</pre><p class="warning">参数将发往此配置服务器。工具可能产生外部操作，调用结果不会自动改写企业事实。</p>${act('确认执行此工具调用','mcp-call',true)}`);},
'mcp-call':()=>operate(async()=>{await api('/api/mcp/servers/'+mcpInvocation.server+'/call',{request_id:crypto.randomUUID(),tool:mcpInvocation.tool,arguments:mcpInvocation.arguments,confirmed:true});mcpData=await api('/api/mcp/servers');$('#modal').close();render();}),
});
async function serverResolve(use_new){return operate(async()=>{await command('resolve',{issue_id:resolveId,use_new,note:val('resolve-note'),who:val('resolve-who')});$('#modal').close();render();});}
// Capture inline actions from the original view before their former local mutation handler.
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const kind=b.dataset.send?'notice_send':b.dataset.queryItem?'notice_query':b.dataset.retryItem?'notice_retry':null;if(kind){e.stopImmediatePropagation();e.preventDefault();return operate(async()=>{const raw=b.dataset.send||b.dataset.queryItem||b.dataset.retryItem,[batch_id,id]=raw.split('|');await command(kind,{batch_id,id});render();});}if(b.dataset.mcpEdit){e.stopImmediatePropagation();mcpEditing=mcpData.servers.find(c=>c.id===b.dataset.mcpEdit);mcpTools=[];render();}if(b.dataset.mcpDiscover){e.stopImmediatePropagation();operate(async()=>{const r=await api('/api/mcp/servers/'+b.dataset.mcpDiscover+'/discover',{request_id:crypto.randomUUID()});mcpData=await api('/api/mcp/servers');mcpTools=r.result?.tools||[];mcpInvocation={server:b.dataset.mcpDiscover};render();if(r.status!=='success')toast(r.message);});}if(b.dataset.mcpTool){e.stopImmediatePropagation();mcpInvocation={server:mcpInvocation.server,tool:b.dataset.mcpTool};showModal('填写MCP工具参数',textarea('参数JSON','mcp-arguments','{}')+act('预览调用参数','mcp-call-preview',true));}},true);
render();

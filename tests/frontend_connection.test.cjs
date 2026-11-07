const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function page(){
  const timers=[];
  const context=vm.createContext({
    window:{workspaceToken:'before-restart'},crypto:{randomUUID:()=> 'test-session'},
    sessionStorage:{getItem:()=>null,setItem:()=>{}},
    overview:()=>'',rules:()=>'',go:()=>{},views:{},nav:[],handlers:{},
    render:()=>{},toast:()=>{},page:'chat',
    document:{addEventListener:()=>{},body:{classList:{add:()=>{},remove:()=>{}}}},
    setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length;},clearTimeout:()=>{},
    val:()=> '深圳无人机',console,
  });
  for(const source of ['server.js','chat.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../frontend',source),'utf8'),context);
  return {context,timers,run:code=>vm.runInContext(code,context)};
}
function response(status,data){return {status,ok:status<400,json:async()=>data};}

test('a disconnected write is reported in Chinese and never replayed',async()=>{
  const p=page();let count=0;
  p.context.fetch=async()=>{count++;throw TypeError('Failed to fetch');};
  await assert.rejects(p.run("api('/api/chat/turns',{request_id:'one'})"),error=>error.connectionLost&&!error.message.includes('Failed to fetch'));
  assert.equal(count,1);
});

test('only explicit expired credential rejection refreshes and replays the same request',async()=>{
  const p=page(),calls=[];
  p.context.fetch=async(url,options)=>{
    calls.push({url,options});
    return calls.length===1?response(403,{detail:'刷新页面获取当前请求凭证'}):calls.length===2?response(200,{csrf_token:'after-restart'}):response(200,{id:'accepted'});
  };
  await p.run("api('/api/chat/turns',{request_id:'same-id'})");
  assert.deepEqual(calls.map(x=>x.url),['/api/chat/turns','/api/workspace','/api/chat/turns']);
  assert.equal(calls[0].options.body,calls[2].options.body);
  assert.equal(calls[2].options.headers['X-Workspace-Token'],'after-restart');
});

test('server failures never automatically replay writes',async()=>{
  const p=page();let count=0;
  p.context.fetch=async()=>{count++;return response(503,{detail:'服务正在恢复'});};
  await assert.rejects(p.run("api('/api/chat/turns',{})"));
  assert.equal(count,1);
});

test('uncertain send keeps text and prevents a second query until reconnect',async()=>{
  const p=page();
  p.context.fetch=async()=>{throw TypeError('Failed to fetch');};
  await p.run("handlers['chat-send']()");
  assert.equal(p.run('chatSending'),true);
  assert.equal(p.run('chatText'),'深圳无人机');
  assert.equal(p.run('chatConnectionLost'),true);
});

test('polling reconnects with reads and clears the stale error after recovery',async()=>{
  const p=page(),calls=[];
  p.run("chatSending=true;chatTurns=[{id:'existing'}]");
  p.context.fetch=async(url,options)=>{
    calls.push(options.method);
    if(calls.length===1)throw TypeError('Failed to fetch');
    return response(200,url.includes('/turns/')?{id:'existing',status:'failed',error:'服务已重启'}:{sessions:[]});
  };
  await p.run("pollChat('existing')");
  assert.equal(p.run('chatSending'),true);
  assert.equal(p.timers.length,1);
  await p.timers.shift().fn();
  assert.equal(p.run('chatSending'),false);
  assert.equal(p.run('chatError'),'');
  assert.equal(p.run('chatTurns[0].error'),'服务已重启');
  assert.ok(calls.every(method=>method==='GET'));
});

test('reconnect reloads saved history and releases an unaccepted send',async()=>{
  const p=page();p.run("chatSending=true;chatError='旧断线提示';chatConnectionLost=true");
  p.context.fetch=async(url)=>response(200,url.includes('/sessions/')?{turns:[]}:{sessions:[]});
  await p.run('loadChat()');
  assert.equal(p.run('chatSending'),false);
  assert.equal(p.run('chatConnectionLost'),false);
  assert.equal(p.run('chatError'),'');
});

test('poll recovery is bounded and a previous session cannot overwrite current history',async()=>{
  const p=page();p.run('chatSending=true');
  p.context.fetch=async()=>{throw TypeError('Failed to fetch');};
  await p.run("pollChat('existing')");
  let retries=0;
  while(p.timers.length){await p.timers.shift().fn();retries++;assert.ok(retries<=6);}
  assert.equal(retries,6);
  p.run("chatSession='new-session';chatError='current'");
  await p.run("pollChat('old','test-session')");
  assert.equal(p.run('chatError'),'current');
});

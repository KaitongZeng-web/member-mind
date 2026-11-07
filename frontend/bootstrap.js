(async()=>{
  try{
    document.querySelector('main').style.visibility='hidden';
    const [response,settings]=await Promise.all([
      fetch('/api/workspace',{cache:'no-store'}),
      fetch('/api/chat/settings',{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null)
    ]);
    if(!response.ok)throw Error('Python API启动失败');
    const info=await response.json();
    window.initialWorkspace=info.workspace;
    window.workspaceToken=info.csrf_token;
    window.runtimeMeta=info;
    window.initialChatSettings=settings;
    for(const src of ['core.js','domain.js','app.js','server.js','chat.js','workspace.js']){
      await new Promise((resolve,reject)=>{
        const script=document.createElement('script');
        script.src=src;script.onload=resolve;script.onerror=reject;
        document.body.appendChild(script);
      });
    }
    document.querySelector('main').style.visibility='visible';
  }catch(e){
    document.querySelector('main').style.visibility='visible';
    document.querySelector('#content').textContent='暂时无法读取工作空间，请检查本地服务是否运行，然后刷新页面。';
  }
})();

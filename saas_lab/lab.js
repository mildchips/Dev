'use strict';
let csrf='', memberships=[], generation=0;
const el=id=>document.getElementById(id);
const tenant=()=>el('tenant').value;
const base=()=>'/api/tenants/'+encodeURIComponent(tenant());
async function api(path,method='GET',body){
 const response=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','x-aipo-csrf':csrf},body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json(); if(!response.ok)throw new Error(data.detail||'Запрос отклонён'); return data;
}
function bind(id,fn){el(id).addEventListener(el(id).tagName==='FORM'?'submit':'click',async event=>{event.preventDefault();el('status').textContent='';try{await fn();}catch(e){el('status').textContent=String(e.message);}});}
async function refresh(){
 const me=await api('/api/me');memberships=me.memberships;el('scope').textContent='ID пользователя: '+me.user_id;
 el('tenant').replaceChildren();for(const m of memberships){const o=document.createElement('option');o.value=m.tenant;o.textContent=m.tenant+' · '+m.role;el('tenant').append(o);}
 el('login').hidden=true;el('workspace').hidden=false;await loadStores();
}
async function loadStores(){
 const g=++generation,t=tenant();el('stores').replaceChildren();el('invite-store').replaceChildren();el('results').textContent='';
 const m=memberships.find(x=>x.tenant===t),owner=m?.role==='owner';el('owner-tools').hidden=!owner;el('invite-tools').hidden=!owner;
 if(!t)return;
 const stores=await api('/api/tenants/'+encodeURIComponent(t)+'/stores');if(g!==generation)return;
 for(const s of stores){
  const row=document.createElement('div');row.className='store';const label=document.createElement('p');label.textContent=s.label+' · '+s.id;row.append(label);
  if(m.role!=='auditor'){const button=document.createElement('button');button.textContent='Создать тестовую задачу';button.onclick=async()=>{try{if(t!==tenant())return;const job=await api('/api/tenants/'+encodeURIComponent(t)+'/jobs','POST',{store:s.id,idempotency_key:crypto.randomUUID()});if(g!==generation)return;el('results').textContent=JSON.stringify(job,null,2)+'\nВыполните python -m saas_lab worker-once, затем откройте статус.';const status=document.createElement('button');status.textContent='Проверить задачу';status.onclick=async()=>{try{const result=await api('/api/tenants/'+encodeURIComponent(t)+'/jobs/'+encodeURIComponent(job.id));if(g===generation)el('results').textContent=JSON.stringify(result,null,2);}catch(e){el('status').textContent=e.message;}};row.append(status);}catch(e){el('status').textContent=e.message;}};row.append(button);}
  el('stores').append(row);const o=document.createElement('option');o.value=s.id;o.textContent=s.label;el('invite-store').append(o);
 }
}
bind('login',async()=>{const pair=await api('/api/login','POST',{login:el('login-name').value,password:el('password').value});el('password').value='';csrf=pair.csrf;await refresh();});
bind('logout',async()=>{await api('/api/logout','POST');csrf='';generation++;el('workspace').hidden=true;el('login').hidden=false;el('stores').replaceChildren();el('results').textContent='';el('new-invite').textContent='';});
bind('store',async()=>{await api(base()+'/stores','POST',{label:el('store-label').value});await loadStores();});
bind('invite',async()=>{const result=await api(base()+'/invites','POST',{login:el('invite-login').value,role:el('invite-role').value,stores:[el('invite-store').value]});el('new-invite').textContent='Одноразовый код: '+result.token;});
bind('accept',async()=>{await api('/api/accept','POST',{token:el('invite-token').value});el('invite-token').value='';await refresh();});
bind('revoke',async()=>{await api(base()+'/members/'+encodeURIComponent(el('member-id').value),'DELETE');el('status').textContent='Доступ отозван. Задачи пользователя отменены.';});
bind('stop',async()=>{await api(base()+'/stop','POST');el('status').textContent='Задачи организации остановлены.';});
bind('resume',async()=>{await api(base()+'/resume','POST');el('status').textContent='Поставленные на паузу задачи возвращены в очередь.';});
bind('audit',async()=>{const g=generation,result=await api(base()+'/audit');if(g===generation)el('results').textContent=JSON.stringify(result,null,2);});
el('tenant').onchange=()=>{el('new-invite').textContent='';loadStores().catch(e=>el('status').textContent=e.message);};

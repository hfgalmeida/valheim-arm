"use strict";
const $=id=>document.getElementById(id);
let csrf="",events=null,samples=[],currentConfig=null;
const labels={server_name:"Nome do servidor",world_name:"Nome do mundo",server_password:"Senha do jogo (vazio mantém)",server_port:"Porta (Compose)",public:"Listagem pública",crossplay:"Crossplay",preset:"Preset",modifier_combat:"Combate",modifier_deathpenalty:"Penalidade de morte",modifier_resources:"Recursos",modifier_raids:"Incursões",modifier_portals:"Portais",key_nobuildcost:"Construção sem custo",key_playerevents:"Eventos por jogador",key_passivemobs:"Inimigos passivos",key_nomap:"Sem mapa",save_interval:"Salvar a cada (segundos)",native_backups:"Backups nativos",timezone:"Fuso horário",restart_at:"Reinício diário (HH:MM ou off)",backup_at:"Backup diário (HH:MM ou off)",auto_update:"Atualizar jogo automaticamente",retain_daily:"Retenção diária",retain_weekly:"Retenção semanal",bepinex:"Habilitar BepInEx",bepinex_url:"URL HTTPS do BepInExPack Linux",bepinex_sha256:"SHA256 do pacote BepInEx"};
function notice(text){$("notice").textContent=text;}
async function api(path,method="GET",body){const headers={"Content-Type":"application/json"};if(csrf)headers["X-CSRF-Token"]=csrf;const r=await fetch("/api/v1"+path,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});const v=await r.json();if(!r.ok){if(r.status===401)signedOut();throw new Error(typeof v.detail==="string"?v.detail:JSON.stringify(v.detail));}return v;}
function signedOut(){if(events)events.close();events=null;csrf="";$("dashboard").hidden=true;$("logout").hidden=true;$("login").hidden=false;}
function bytes(v){return v>=1073741824?(v/1073741824).toFixed(1)+" GiB":v>=1048576?(v/1048576).toFixed(1)+" MiB":v>=1024?(v/1024).toFixed(1)+" KiB":Math.round(v)+" B";}
function chart(id,values,ceiling){const c=$(id),x=c.getContext("2d"),w=c.width,h=c.height;x.clearRect(0,0,w,h);x.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue("--line").trim();x.beginPath();x.moveTo(0,h-1);x.lineTo(w,h-1);x.stroke();if(values.length<2)return;const max=ceiling||Math.max(...values,1);x.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();x.lineWidth=3;x.beginPath();values.forEach((v,i)=>{const a=i/(values.length-1)*w,b=h-6-Math.min(v/max,1)*(h-12);i?x.lineTo(a,b):x.moveTo(a,b);});x.stroke();}
function cloudStatus(c){
  const last=c.last_success?" · Último envio: "+new Date(c.last_success*1000).toLocaleString()+" ("+c.last_file+")":"";
  $("cloud-status").textContent=(c.enabled?(c.uploading?"Enviando":"Ativado")+(c.provider?" · "+c.provider:"")+" · Pendentes: "+c.pending:"Envio desativado")+last+(c.error?" · "+c.error+". Nova tentativa automática em cinco minutos.":"");
}
async function loadCloud(){const c=await api("/cloud");$("cloud-enabled").value=String(c.enabled);$("cloud-folder").value=c.folder;$("cloud-config").value="";$("cloud-file").value="";cloudStatus(c);}
function update(s){
  if(s.cloud)cloudStatus(s.cloud);
  $("crossplay-info").hidden=!s.crossplay;
  $("join-code").textContent=s.join_code||(s.running?"Aguardando código do servidor…":"Disponível ao iniciar o servidor");
  $("status").textContent="Estado: "+s.state+(s.recovery_error?" — "+s.recovery_error:"");$("players").textContent=s.players.count===null?"?":s.players.count;$("player-reason").textContent=s.players.reason;
  $("versions").textContent="Build instalado: "+(s.installed_build||"—")+" · Disponível: "+(s.available_build||"—");$("pending").textContent=s.pending_config?"Alterações aguardam reinício":"";
  $("logs").textContent=s.logs.map(l=>new Date(l.time*1000).toLocaleTimeString()+"  "+l.message).join("\n");$("jobs").replaceChildren();
  [...s.jobs].reverse().slice(0,15).forEach(j=>{const r=document.createElement("div");r.className="job";r.textContent=j.action+" · "+j.state+(j.error?" — "+j.error:"")+(j.result?" — "+j.result:"");$("jobs").append(r);});
  if(s.metric&&(!samples.length||samples.at(-1).time!==s.metric.time))samples.push(s.metric);samples=samples.slice(-720);const m=samples.at(-1);if(!m)return;
  $("cpu").textContent=m.cpu_percent.toFixed(1)+"%";$("memory").textContent=bytes(m.memory_bytes)+" / "+bytes(m.memory_limit);$("network").textContent=bytes(m.network_in_bps)+" / "+bytes(m.network_out_bps)+"/s";$("disk").textContent=bytes(m.disk_used)+" / "+bytes(m.disk_total);
  $("metric-scope").textContent="Medição: "+m.scope+". Disco: sistema de arquivos do volume. Histórico: até uma hora; atualização a cada 5 segundos.";
  chart("cpu-chart",samples.map(x=>x.cpu_percent),Math.max(200,...samples.map(x=>x.cpu_percent)));chart("memory-chart",samples.map(x=>x.memory_bytes),m.memory_limit);chart("network-chart",samples.map(x=>x.network_in_bps+x.network_out_bps));chart("disk-chart",samples.map(x=>x.disk_used),m.disk_total);
}
async function loadConfig(){
  currentConfig=await api("/config");$("settings").replaceChildren();$("server-name").textContent=currentConfig.values.server_name;
  for(const [key,value]of Object.entries(currentConfig.values)){
    const heading={server_name:"Mundo e acesso",preset:"Regras de jogo",save_interval:"Salvamento",timezone:"Rotina automática",bepinex:"Mods"}[key];
    if(heading){const title=document.createElement("h3");title.className="settings-heading";title.textContent=heading;$("settings").append(title);}
    const row=document.createElement("div");row.className="setting";const label=document.createElement("label");label.textContent=labels[key]||key;
    let input;const options=key.startsWith("modifier_")?currentConfig.modifiers[key.slice(9)]:key==="preset"?["","normal","casual","easy","hard","hardcore","immersive","hammer"]:typeof value==="boolean"?["true","false"]:null;
    if(options){input=document.createElement("select");options.forEach(v=>{const o=document.createElement("option");o.value=v;o.textContent=v===""?"Padrão do mundo/preset":v==="true"?"Sim":v==="false"?"Não":v;input.append(o);});input.value=String(value);}
    else{input=document.createElement("input");input.type=key==="server_password"?"password":typeof value==="number"?"number":"text";input.value=value;if(key==="server_password")input.autocomplete="new-password";}
    input.id="setting-"+key;input.disabled=key==="server_port";label.append(input);row.append(label);const source=document.createElement("small");source.textContent="Origem: "+currentConfig.sources[key];row.append(source);
    if(currentConfig.sources[key]==="painel"&&key!=="server_port"){const b=document.createElement("button");b.type="button";b.className="reset";b.textContent="Usar Compose";b.onclick=()=>guard(async()=>{const world=key==="world_name";if(world&&!confirm("Voltar ao mundo do Compose? O save atual será preservado."))return;await api("/config","PATCH",{reset:[key],confirm_world:world});await loadConfig();notice("Ajuste removido. Reinicie com segurança para aplicar ao jogo.");});row.append(b);}$("settings").append(row);
  }
}
async function loadUsers(){const d=await api("/users/"+$("user-kind").value);$("user-entries").value=d.entries.join("\n");}
async function loadBackups(){const list=await api("/backups");$("backup-list").replaceChildren();if(!list.length){$("backup-list").textContent="Nenhum backup criado ainda.";return;}list.forEach(b=>{const row=document.createElement("div");row.className="backup";const a=document.createElement("a");a.href="/api/v1/backups/"+encodeURIComponent(b.name);a.textContent=b.name+" · "+bytes(b.bytes);row.append(a);const button=document.createElement("button");button.textContent="Restaurar";button.onclick=()=>guard(async()=>{if(!confirm("Restaurar "+b.name+"? Pare o jogo primeiro. Será criado um backup do estado atual."))return;await api("/actions","POST",{action:"restore",backup:b.name,confirm:true});notice("Restauração enviada. Acompanhe na aba Atividade.");});row.append(button);$("backup-list").append(row);});}
async function signedIn(){$("login").hidden=true;$("dashboard").hidden=false;$("logout").hidden=false;await Promise.all([loadConfig(),loadUsers(),loadBackups(),loadCloud()]);const state=await api("/state");samples=state.metrics;update(state);if(events)events.close();events=new EventSource("/api/v1/events");events.onmessage=e=>update(JSON.parse(e.data));events.onerror=()=>notice("Conexão interrompida; tentando reconectar ao supervisor…");}
async function guard(fn){try{await fn();}catch(e){notice(e.message);}}
$("login-form").onsubmit=e=>{e.preventDefault();guard(async()=>{const r=await api("/login","POST",{password:$("password").value});$("password").value="";csrf=r.csrf;notice("");await signedIn();});};
$("logout").onclick=()=>guard(async()=>{await api("/logout","POST",{});signedOut();});
$("actions").onclick=e=>{const action=e.target.dataset.action;if(!action)return;guard(async()=>{if(action==="stop"&&!confirm("Parar o servidor agora? Jogadores conectados serão desconectados. O jogo terá tempo para salvar."))return;await api("/actions","POST",{action});notice("Operação enviada. Acompanhe o resultado na aba Atividade.");});};
$("config-form").onsubmit=e=>{e.preventDefault();guard(async()=>{const changes={};for(const [key,old]of Object.entries(currentConfig.values)){if(key==="server_port")continue;const value=$("setting-"+key).value;if(key==="server_password"&&!value)continue;const parsed=typeof old==="boolean"?value==="true":typeof old==="number"?Number(value):value;if(parsed!==old)changes[key]=parsed;}const world="world_name"in changes;if(world&&!confirm("Selecionar/criar o mundo “"+changes.world_name+"”? O mundo anterior será preservado."))return;await api("/config","PATCH",{changes,confirm_world:world});await loadConfig();notice("Configuração salva. Alterações do jogo serão aplicadas na próxima inicialização.");});};
$("user-kind").onchange=()=>guard(loadUsers);$("users-form").onsubmit=e=>{e.preventDefault();guard(async()=>{const kind=$("user-kind").value;if(kind==="permitted"&&$("user-entries").value.trim()&&!confirm("Restringir o acesso somente aos IDs permitidos?"))return;await api("/users/"+kind,"PUT",{entries:$("user-entries").value.split("\n")});notice("Lista salva. O jogo recarrega as listas; se necessário, reinicie com segurança.");});};
$("refresh-backups").onclick=()=>guard(loadBackups);guard(async()=>{try{csrf=(await api("/session")).csrf;await signedIn();}catch(e){signedOut();}});

$("cloud-form").onsubmit=e=>{e.preventDefault();guard(async()=>{await api("/cloud","PUT",{enabled:$("cloud-enabled").value==="true",folder:$("cloud-folder").value,config:$("cloud-config").value});await loadCloud();notice("Envio na nuvem salvo. Novos backups serão enviados automaticamente quando ativado.");});};

$("cloud-file").onchange=()=>guard(async()=>{const f=$("cloud-file").files[0];if(!f)return;if(f.size>65536)throw new Error("Arquivo de conexão muito grande (máximo 64 KiB)");$("cloud-config").value=await f.text();});


const tabs=[...document.querySelectorAll('[role="tab"]')];
function selectTab(tab,focus=false){
  tabs.forEach(item=>{
    const selected=item===tab;
    item.setAttribute("aria-selected",String(selected));
    item.tabIndex=selected?0:-1;
    $(item.getAttribute("aria-controls")).hidden=!selected;
  });
  if(focus)tab.focus();
}
tabs.forEach((tab,index)=>{
  tab.onclick=()=>selectTab(tab);
  tab.onkeydown=event=>{
    let next;
    if(event.key==="ArrowRight")next=(index+1)%tabs.length;
    else if(event.key==="ArrowLeft")next=(index+tabs.length-1)%tabs.length;
    else if(event.key==="Home")next=0;
    else if(event.key==="End")next=tabs.length-1;
    else return;
    event.preventDefault();
    selectTab(tabs[next],true);
  };
});

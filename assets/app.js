function esc(x){return String(x).replace(/[&<>"']/g,
 c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function md(x){return esc(x).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');}
function bar(p,cls){p=parseInt(p);if(isNaN(p))return'';
 return `<div class="bar ${cls||(p>=85?'hot':'')}"><i style="width:${Math.min(p,100)}%"></i></div>`;}
async function refresh(){
 try{
  const [s,g]=await Promise.all([
    (await fetch('api/status')).json(), (await fetch('api/goal')).json()]);
  const b=document.getElementById('badge');
  b.className='badge '+s.tone; b.textContent=s.headline;
  const avatar=document.getElementById('avatar');
  avatar.src=s.scene+'.svg'; avatar.alt=s.headline;

  const nextUp=g.next_up||[];
  const taskLabel=t=>t?md(t.section+' — '+t.text):'<span class="muted">–</span>';
  document.getElementById('curTaskText').innerHTML=taskLabel(nextUp[0]);
  document.getElementById('nextTaskText').innerHTML=taskLabel(nextUp[1]);

  const planPct=g.plan_total?Math.round(100*g.plan_done/g.plan_total):0;
  const cards=[
   ['Overall progress', g.plan_done+' / '+g.plan_total+bar(planPct,'ok')],
   ['Session budget', (s.session_pct!==''?esc(s.session_pct)+'%':'–')+bar(s.session_pct)],
   ['Weekly budget', (s.week_pct!==''?esc(s.week_pct)+'%':'–')+bar(s.week_pct)],
  ];
  document.getElementById('grid').innerHTML=cards.map(
   ([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

  const statusCards=[
   ['Next run', esc(s.next_run||'– none scheduled –')],
   ['Blockers', md(g.blockers||'–')],
   ['Last note', esc(s.note||'–')],
  ];
  document.getElementById('statusGrid').innerHTML=statusCards.map(
   ([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

  const dp=g.dod_total?Math.round(100*g.dod_done/g.dod_total):0;
  document.getElementById('donut').style.background=
   `conic-gradient(#5ee08a ${dp*3.6}deg, #232833 0deg)`;
  document.getElementById('donutPct').textContent=dp+'%';
  document.getElementById('dodList').innerHTML=(g.dod||[]).map(
   i=>`<li class="${i.done?'on':''}"><span>${i.done?'✔':'○'}</span><span>${md(i.text)}</span></li>`).join('');

  document.getElementById('planTotal').textContent='· '+planPct+'%';
  let nextFound=false;
  document.getElementById('plan').innerHTML=(g.plan||[]).map(sec=>{
   const pct=sec.total?Math.round(100*sec.done/sec.total):0;
   const hasNext=!nextFound&&sec.items.some(i=>!i.done);
   const items=sec.items.map(i=>{
     let cls=i.done?'on':'';
     if(!i.done&&!nextFound){cls='nxt';nextFound=true;}
     return `<div class="${cls}"><span>${i.done?'✔':(cls==='nxt'?'➤':'○')}</span><span>${md(i.text)}</span></div>`;
   }).join('');
   return `<details ${hasNext?'open':''}><summary><span class="name">${esc(sec.name)}</span>
     <span class="cnt">${sec.done}/${sec.total}</span>${bar(pct,'ok')}</summary>
     <div class="items">${items}</div></details>`;
  }).join('');

  document.getElementById('goalLog').innerHTML=(g.goal_log||[]).slice().reverse().map(
   l=>`<div>${md(l)}</div>`).join('')||'<div class="muted">no iterations yet</div>';
  const logEl=document.getElementById('log');
  logEl.textContent=await (await fetch('api/log')).text();
  logEl.scrollTop=logEl.scrollHeight;
  document.getElementById('foot').textContent=
   'Status updated: '+(s.updated||'never')+' · Server: '+s.server_time;
 }catch(e){
  document.getElementById('badge').textContent='Monitor unreachable';
  const avatar=document.getElementById('avatar');
  avatar.src='searching.svg'; avatar.alt='Monitor unreachable';
 }
}
const ROUTES=['/','/plan','/logs'];
function route(){
 let r=(location.hash||'#/').slice(1);
 if(!ROUTES.includes(r))r='/';
 document.getElementById('page-home').style.display=r==='/'?'':'none';
 document.getElementById('page-plan').style.display=r==='/plan'?'':'none';
 document.getElementById('page-logs').style.display=r==='/logs'?'':'none';
 document.querySelectorAll('#nav a').forEach(
  a=>a.classList.toggle('active',a.dataset.route===r));
}
window.addEventListener('hashchange', route);
route();

refresh(); setInterval(refresh, 5000);

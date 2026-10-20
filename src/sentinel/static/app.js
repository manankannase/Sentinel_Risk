"use strict";
const $ = (id) => document.getElementById(id);
const state = {token: "", principal: null, page: "overview", transactions: [], offset: 0, cursors: [null], nextCursor: null, busy: false, generation: 0, pending: null};
const requests = new Set();
const money = (n, currency="INR") => new Intl.NumberFormat("en-IN", {style: "currency", currency, maximumFractionDigits: 2}).format(n / 100);
const percent = (n) => n == null ? "Unavailable" : `${(Number(n) * 100).toFixed(1)}%`;
function el(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
function svg(tag, attrs={}) { const n=document.createElementNS("http://www.w3.org/2000/svg",tag); for(const [k,v] of Object.entries(attrs)) n.setAttribute(k,String(v)); return n; }
function note(message) { $("notice").textContent=message; $("notice").hidden=!message; }
async function api(path, options={}) {
  if(window.sentinelDemo) return window.sentinelDemo(path, options);
  const controller = new AbortController(); requests.add(controller);
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {...options, signal: controller.signal, headers: {"Authorization": `Bearer ${state.token}`, ...(options.body?{"Content-Type":"application/json"}:{}), ...options.headers}});
    const data = await response.json();
    if(!response.ok) {
      const error = new Error(typeof data.detail === "string" ? data.detail : "Request could not be processed");
      error.status=response.status;
      if(response.status===401 && state.principal) logout();
      throw error;
    }
    return {data, latency:response.headers.get("X-Process-Time-Ms")};
  } finally { clearTimeout(timeout); requests.delete(controller); }
}
function logout() {
  state.generation++; state.token=""; state.principal=null; state.transactions=[]; state.pending=null; state.offset=0; state.cursors=[null]; state.nextCursor=null; state.busy=false;
  for(const c of requests) c.abort(); requests.clear();
  $("api-key").value=""; $("workspace").hidden=true; $("login").hidden=false;
  $("login-error").textContent=""; $("detail").close();
  for(const id of ["recent-table","transactions-table","model-stats","importance","health-list","protocol","drift","detail-content"]) $(id).replaceChildren();
  $("api-key").focus();
}
$("logout").addEventListener("click",logout);
$("login-form").addEventListener("submit",async e=>{
  e.preventDefault(); $("login-button").disabled=true; $("login-error").textContent="";
  state.token=$("api-key").value.trim();
  try {
    const {data}=await api("/api/v1/me"); state.principal=data; $("api-key").value="";
    $("identity-name").textContent=data.tenant; $("identity-role").textContent=`${data.name} · ${data.role}`;
    $("login").hidden=true; $("workspace").hidden=false;
    document.querySelectorAll("[data-page]").forEach(n=>n.hidden=data.role==="scorer"&&n.dataset.page!=="score");
    document.querySelectorAll('[data-go="score"], [data-page="score"]').forEach(n=>n.hidden=data.role==="analyst");
    go(data.role==="scorer"?"score":"overview");
  } catch(error) { state.token=""; $("login-error").textContent=error.message; }
  finally { $("login-button").disabled=false; }
});
function go(page) {
  state.generation++; for(const c of requests)c.abort(); requests.clear(); state.busy=false;
  state.page=page;
  document.querySelectorAll(".page").forEach(n=>n.hidden=n.id!==`page-${page}`);
  document.querySelectorAll("[data-page]").forEach(n=>{const active=n.dataset.page===page;n.classList.toggle("active",active); if(active)n.setAttribute("aria-current","page");else n.removeAttribute("aria-current");});
  $("breadcrumb").textContent={overview:"Overview",transactions:"Transactions",model:"Model & health",score:"Score transaction"}[page];
  note(""); refresh();
}
document.querySelectorAll("[data-page],[data-go]").forEach(n=>n.addEventListener("click",()=>go(n.dataset.page||n.dataset.go)));
$("refresh").addEventListener("click",()=>refresh());
function badge(decision) {return el("span",decision,"badge "+decision.toLowerCase());}
function table(container, rows) {
  const target=$(container); target.replaceChildren();
  if(!rows.length){target.append(el("p","No decisions yet. Score a transaction or run the replay script to start.","empty"));return;}
  const t=el("table"), head=el("thead"), tr=el("tr");
  for(const text of ["Transaction / customer","Merchant","Amount","Risk","Decision","Event time"])tr.append(el("th",text));
  head.append(tr);t.append(head);const body=el("tbody");
  for(const r of rows){const row=el("tr"), first=el("td"), button=el("button",r.customer_id,"row-button");button.addEventListener("click",()=>details(r));first.append(button,el("div",r.transaction_id.slice(0,8)+"…","small muted"));row.append(first,el("td",r.merchant_id),el("td",money(r.amount_minor,r.currency)),el("td",percent(r.score)));const status=el("td");status.append(badge(r.decision));row.append(status,el("td",new Date(r.occurred_at).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})));body.append(row);}
  t.append(body);target.append(t);
}
function activity(series) {
  const box=$("activity-chart");box.replaceChildren();
  if(!series.length){box.append(el("p","Hourly activity appears as transactions arrive.","empty"));return;}
  const chart=svg("svg",{viewBox:"0 0 620 180",role:"img","aria-label":"Hourly transaction counts"});
  const peak=Math.max(...series.map(s=>s.total),1);const width=540/series.length;
  [0,.5,1].forEach(f=>{const y=145-f*125;chart.append(svg("line",{x1:35,x2:605,y1:y,y2:y,stroke:"#e8ede7","stroke-dasharray":"3 4"}));const label=svg("text",{x:20,y:y+3,"font-size":9,fill:"#8a968b","text-anchor":"end"});label.textContent=String(Math.round(peak*f));chart.append(label);});
  series.forEach((s,i)=>{const height=s.total/peak*120,x=45+i*width;const bar=svg("rect",{x,y:145-height,width:Math.max(4,width*.6),height,rx:3,fill:i===series.length-1?"#267951":"#bdd4bc"});const title=svg("title");title.textContent=`${s.hour}:00 UTC · ${s.total} transactions`;bar.append(title);chart.append(bar);if(i%Math.max(1,Math.ceil(series.length/7))===0){const label=svg("text",{x,y:170,"font-size":9,fill:"#879386"});label.textContent=s.hour.slice(11)+":00";chart.append(label);}});box.append(chart);
}
function decisionMix(data) {
  const chart=svg("svg",{viewBox:"0 0 140 140",role:"img","aria-label":"Decision distribution"});
  const values=[data.approved,data.review,data.declined], colors=["#347d56","#d9a85a","#c7736a"], names=["Approved","Review","Declined"];
  chart.append(svg("circle",{cx:70,cy:70,r:53,fill:"none",stroke:"#eff2ed","stroke-width":13}));let offset=0;
  values.forEach((value,i)=>{const length=value/Math.max(data.total,1)*333;chart.append(svg("circle",{cx:70,cy:70,r:53,fill:"none",stroke:colors[i],"stroke-width":13,"stroke-dasharray":`${length} ${333-length}`,"stroke-dashoffset":-offset,transform:"rotate(-90 70 70)"}));offset+=length;});
  const center=svg("text",{x:70,y:72,"text-anchor":"middle","font-size":23,fill:"#263b2c"});center.textContent=data.total.toLocaleString();chart.append(center);const sub=svg("text",{x:70,y:90,"text-anchor":"middle","font-size":8,fill:"#889384"});sub.textContent="DECISIONS";chart.append(sub);$("decision-chart").replaceChildren(chart);
  $("decision-legend").replaceChildren();values.forEach((value,i)=>{const r=el("div",undefined,"legend-row");r.append(badge(names[i]==="Approved"?"APPROVE":names[i].toUpperCase()),el("strong",`${value.toLocaleString()} · ${percent(value/Math.max(data.total,1))}`));$("decision-legend").append(r);});
}
async function refresh() {
  if(!state.principal||state.busy)return;
  state.busy=true;const generation=state.generation;$("refresh").disabled=true;
  try {
    if(state.page==="overview"){
      const [o,t]=await Promise.all([api("/api/v1/overview"),api("/api/v1/transactions?limit=6")]);
      if(generation!==state.generation)return;
      $("stat-total").textContent=o.data.total.toLocaleString();$("stat-volume").textContent=money(o.data.volume_minor);$("stat-review").textContent=o.data.review.toLocaleString();$("stat-risk").textContent=percent(o.data.average_risk);activity(o.data.series);decisionMix(o.data);table("recent-table",t.data.items);
    } else if(state.page==="transactions") {
      const filter=$("decision-filter").value;
      const {data}=await api(`/api/v1/transactions?limit=50${state.cursors[state.offset/50]?"&cursor="+encodeURIComponent(state.cursors[state.offset/50]):""}${filter?"&decision="+encodeURIComponent(filter):""}`);
      if(generation!==state.generation)return;state.transactions=data.items;state.nextCursor=data.next_cursor;renderFiltered();$("previous").disabled=state.offset===0;$("next").disabled=!state.nextCursor;$("page-count").textContent=`${data.items.length?state.offset+1:0}–${state.offset+data.items.length} · 50 per page`;
    } else if(state.page==="model") {
      const [m,h]=await Promise.all([api("/api/v1/model"),api("/api/v1/health")]);if(generation!==state.generation)return;renderModel(m.data,h.data);
    }
    if(generation!==state.generation)return;
    $("connection").textContent="Connected";$("last-sync").textContent="Updated "+new Date().toLocaleTimeString();
  } catch(error){if(error.name!=="AbortError"&&state.principal){note(error.message);$("connection").textContent="Needs attention";}}
  finally {if(generation===state.generation){state.busy=false;$("refresh").disabled=false;}}
}
function renderFiltered(){const q=$("search").value.toLowerCase();table("transactions-table",state.transactions.filter(r=>[r.transaction_id,r.customer_id,r.merchant_id].some(s=>s.toLowerCase().includes(q))));}
$("search").addEventListener("input",renderFiltered);
$("decision-filter").addEventListener("change",()=>{state.offset=0;state.cursors=[null];refresh();});
$("previous").addEventListener("click",()=>{state.offset=Math.max(0,state.offset-50);refresh();});
$("next").addEventListener("click",()=>{if(!state.nextCursor)return;state.offset+=50;state.cursors[state.offset/50]=state.nextCursor;refresh();});
function renderModel(data,health){
  const {manifest,metrics,monitoring}=data;
  $("model-stats").replaceChildren();
  for(const [title,value,sub] of [["Test average precision",percent(metrics.test.average_precision),`PR summary · ${manifest.data_source}`],["Recall at review threshold",percent(metrics.test.recall),"Threshold chosen on validation"],["False-positive rate",percent(metrics.test.fpr),"Observed on test split"],["Training rows",metrics.split_counts.train.toLocaleString(),`${metrics.purged_delayed_labels} delayed labels purged`]]){const card=el("article");card.append(el("span",title),el("strong",value),el("small",sub));$("model-stats").append(card);}
  $("importance").replaceChildren();const features=Object.entries(metrics.feature_importance).sort((a,b)=>b[1]-a[1]);const max=Math.max(...features.map(x=>x[1]),.001);
  for(const [name,value] of features.slice(0,8)){const r=el("div",undefined,"feature-row"),track=el("div",undefined,"bar-track"),bar=svg("svg",{viewBox:"0 0 100 8",preserveAspectRatio:"none"});bar.append(svg("rect",{x:0,y:0,width:value/max*100,height:8,rx:1,fill:"#76a384"}));track.append(bar);r.append(el("span",name.replaceAll("_"," ")),track,el("span",percent(value)));$("importance").append(r);}
  $("health-list").replaceChildren();for(const [k,v] of Object.entries(health)){const row=el("div",undefined,"health-row");row.append(el("span",k.replaceAll("_"," ")),el("strong",String(v)));$("health-list").append(row);}
  const dl=el("dl");for(const [k,v] of [["Source",manifest.data_source],["Model",manifest.model_version],["Train / calibration / validation / test",Object.values(metrics.split_counts).join(" / ")],["Review / decline thresholds",`${percent(manifest.review_threshold)} / ${percent(manifest.decline_threshold)}`],["Logistic baseline AP",percent(metrics.baseline.average_precision)],["Brier score",metrics.test.brier_score.toFixed(4)]])dl.append(el("dt",k),el("dd",v));
  const limitations=el("ul");for(const line of metrics.limitations)limitations.append(el("li",line));$("protocol").replaceChildren(dl,limitations);
  $("drift").replaceChildren(el("p",`${monitoring.sample_size} recent feature snapshots · ${monitoring.labels_received} investigation labels.`),el("p",monitoring.method));
  if(monitoring.sample_size<100)$("drift").append(el("p","At least 100 predictions are required for the exploratory feature watch."));
  else if(!monitoring.mean_shift_warnings.length)$("drift").append(el("p","No mean-shift warnings reported. This does not establish absence of drift."));
  for(const w of monitoring.population_stability||[]){if(w.warning)$("drift").append(el("p",`${w.feature}: PSI ${w.psi} (descriptive warning)`));}
  for(const w of monitoring.mean_shift_warnings)$("drift").append(el("p",`${w.feature}: ${w.mean_shift_std} training standard deviations`));
}
function details(row){
  const box=$("detail-content");box.replaceChildren(badge(row.decision),el("div",percent(row.score),"score-number"));
  const grid=el("div",undefined,"detail-grid");for(const [key,value] of [["Transaction",row.transaction_id],["Customer",row.customer_id],["Amount",money(row.amount_minor,row.currency)],["Model",row.model_version],["Occurred at",new Date(row.occurred_at).toISOString()],["Recorded at",new Date(row.created_at).toISOString()]]){const cell=el("div");cell.append(el("small",key),el("span",value));grid.append(cell);}box.append(grid,el("h2","Observed risk signals"));
  const list=el("ul",undefined,"signal-list");row.reasons.forEach(s=>list.append(el("li",s)));box.append(list,el("p","These signals are rules, not causal model explanations.","small muted"));
  const accordion=el("details"),summary=el("summary","Inspect scoring features"),features=el("div",undefined,"detail-features");for(const [k,v] of Object.entries(row.features)){const r=el("div");r.append(el("span",k),el("strong",Number(v).toFixed(3)));features.append(r);}accordion.append(summary,features);box.append(accordion);
  if(row.label!==null&&row.label!==undefined)box.append(el("p",`Investigation outcome: ${row.label?"Confirmed fraud":"Legitimate"}`,"notice"));
  else if(["admin","analyst"].includes(state.principal.role)){const actions=el("div",undefined,"detail-actions");for(const [label,text] of [[0,"Mark legitimate"],[1,"Confirm fraud"]]){const b=el("button",text,"secondary");b.addEventListener("click",async()=>{if(!confirm(`Record “${text}”? Feedback is immutable in this demonstration.`))return;b.disabled=true;try{await api(`/api/v1/transactions/${row.transaction_id}/feedback`,{method:"POST",body:JSON.stringify({label})});row.label=label;details(row);}catch(e){box.append(el("p",e.message,"error"));b.disabled=false;}});actions.append(b);}box.append(actions);}
  if(!$("detail").open)$("detail").showModal();
}
$("close-detail").addEventListener("click",()=>$("detail").close());
$("sample").addEventListener("click",()=>{const f=$("score-form");f.elements.customer_id.value="customer_"+crypto.randomUUID().slice(0,8);f.elements.merchant_id.value="merchant_042";f.elements.device_id.value="device_demo";f.elements.amount.value="149.50";f.elements.country.value="IN";state.pending=null;});
$("score-form").addEventListener("submit",async e=>{
  e.preventDefault();const f=e.target;const amount=f.elements.amount.value;
  if(!/^\d+(\.\d{1,2})?$/.test(amount)){note("Use a positive INR amount with at most two decimal places.");return;}
  const [whole,fraction=""]=amount.split(".");const amount_minor=Number(whole)*100+Number(fraction.padEnd(2,"0"));
  const body={customer_id:f.elements.customer_id.value,merchant_id:f.elements.merchant_id.value,device_id:f.elements.device_id.value,amount_minor,currency:"INR",country:f.elements.country.value,channel:f.elements.channel.value};
  const signature=JSON.stringify(body);if(!state.pending||state.pending.signature!==signature)state.pending={signature,body:{...body,transaction_id:crypto.randomUUID(),occurred_at:new Date().toISOString()}};
  $("score-submit").disabled=true;note("");const generation=state.generation;
  try{const {data,latency}=await api("/api/v1/score",{method:"POST",body:JSON.stringify(state.pending.body)});if(generation!==state.generation)return;state.pending=null;const box=$("score-result");box.replaceChildren(badge(data.decision),el("div",percent(data.score),"score-number"),el("p",window.sentinelDemo?"Synthetic model score · computed in this tab":`Risk score · ${latency||"—"} ms API processing`));const list=el("ul");data.reasons.forEach(s=>list.append(el("li",s)));const b=el("button","Inspect full decision ↗","text-button");b.addEventListener("click",()=>details(data));box.append(list,el("p",`Model ${data.model_version}`,"small muted"),b);}
  catch(error){if(generation===state.generation)note(error.name==="AbortError"?"Request timed out. Retry keeps the same transaction ID.":error.message);}
  finally{$("score-submit").disabled=false;}
});
setInterval(()=>{if(!document.hidden&&state.principal&&state.page!=="score")refresh();},20000);

/* Standalone demo adapter: only synthetic assets fetched; all decisions stay in tab memory. */
(function() {
  "use strict";
  let bundle, events=[], decisions=[];
  const initial = Promise.all(['model.json','samples.json'].map(name=>fetch('./'+name).then(r=>{if(!r.ok)throw new Error('Demo assets unavailable');return r.json();})))
    .then(([model,samples])=>{bundle=model; const last=Date.parse(samples.at(-1).occurred_at);
      const days=Math.floor((Date.now()-last)/86400000);
      for(const tx of samples) score({...tx,occurred_at:new Date(Date.parse(tx.occurred_at)+days*86400000).toISOString()});
    });
  // Avoid an unhandled rejection before the visitor opens the workspace.
  initial.catch(()=>{});
  function score(tx) {
    const existing=events.find(e=>e.transaction_id===tx.transaction_id);
    if(existing){if(JSON.stringify(existing)!==JSON.stringify(tx))throw new Error('Transaction ID conflict');return decisions.find(d=>d.transaction_id===tx.transaction_id);}
    if(decisions.length>=2000)throw new Error('Demo limited to 2,000 transactions per tab. Reload to reset.');
    const features=SentinelDemoModel.features(tx,events), risk=SentinelDemoModel.predict(bundle,features);
    const m=bundle.manifest, model_decision=risk>=m.decline_threshold?'DECLINE':risk>=m.review_threshold?'REVIEW':'APPROVE';
    const reasons=[];
    if(features.cold_start)reasons.push('No prior INR history: manual review required');
    const late=events.some(h=>h.customer_id===tx.customer_id&&Date.parse(h.occurred_at)>=Date.parse(tx.occurred_at));
    if(late)reasons.push('Late or same-time event: history may be incomplete');
    const decision=reasons.length?'REVIEW':model_decision;
    if(features.amount_ratio>4)reasons.push('Amount exceeds recent customer average');
    if(features.count_1h>=3)reasons.push('Elevated transaction frequency');
    if(features.new_device)reasons.push('Device unseen in previous 24 hours');
    if(features.new_country)reasons.push('Country unseen in previous 24 hours');
    if(!reasons.length)reasons.push('No rule-based risk signals; decision uses model score');
    const result={...tx,score:Math.round(risk*1e6)/1e6,decision,model_decision,features,reasons,label:null,
      model_version:m.model_version,created_at:tx.occurred_at,mode:'browser simulation'};
    events.push(tx);decisions.push(result);return result;
  }
  window.sentinelDemo=async function(path, options={}) {
    await initial;
    const url=new URL(path,location.origin), route=url.pathname;let data;
    if(route==='/api/v1/me')data={name:'Public demo',tenant:'Synthetic INR workspace',role:'admin'};
    else if(route==='/api/v1/score')data=score(JSON.parse(options.body));
    else if(route==='/api/v1/transactions'){
      const filtered=decisions.filter(d=>!url.searchParams.get('decision')||d.decision===url.searchParams.get('decision')).slice().reverse();
      const offset=Number(url.searchParams.get('cursor')||url.searchParams.get('offset')||0), limit=Number(url.searchParams.get('limit')||50);
      data={items:filtered.slice(offset,offset+limit),offset,limit,next_cursor:offset+limit<filtered.length?String(offset+limit):null};
    } else if(route.endsWith('/feedback')){
      const row=decisions.find(d=>d.transaction_id===route.split('/')[4]), label=JSON.parse(options.body).label;
      if(!row)throw new Error('Transaction not found');
      if(row.label!==null&&row.label!==label)throw new Error('Conflicting feedback; demo reset required');
      row.label=label;data={transaction_id:row.transaction_id,label};
    } else if(route==='/api/v1/overview'){
      const buckets={};for(const row of decisions){const hour=row.occurred_at.slice(0,13);if(Date.parse(row.occurred_at)<Date.now()-86400000)continue;
        const b=buckets[hour]||(buckets[hour]={hour,total:0,risk:0});b.total++;b.risk+=row.score;}
      data={total:decisions.length,approved:decisions.filter(d=>d.decision==='APPROVE').length,
        review:decisions.filter(d=>d.decision==='REVIEW').length,declined:decisions.filter(d=>d.decision==='DECLINE').length,
        volume_minor:decisions.reduce((s,d)=>s+d.amount_minor,0),average_risk:decisions.reduce((s,d)=>s+d.score,0)/Math.max(decisions.length,1),
        series:Object.values(buckets).sort((a,b)=>a.hour.localeCompare(b.hour)).map(b=>({...b,risk:b.risk/b.total}))};
    } else if(route==='/api/v1/model')data={manifest:bundle.manifest,metrics:bundle.metrics,
      monitoring:{sample_size:decisions.length,labels_received:decisions.filter(d=>d.label!==null).length,mean_shift_warnings:[],
        method:'Public browser simulation. Drift service and backend monitoring are not running here.'}};
    else if(route==='/api/v1/health')data={runtime:'Browser only',model:'Synthetic INR XGBoost',storage:'Tab memory; resets on reload',streaming:'Not connected'};
    else throw new Error('This operation requires the backend deployment');
    return {data:structuredClone(data),latency:null};
  };
})();

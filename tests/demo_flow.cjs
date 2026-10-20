// Exercise the actual public adapter without browser automation or network dependencies.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context={URL,Date,Math,Number,JSON,Promise,structuredClone,location:{origin:'https://demo.example'},
  fetch:async path=>({ok:true,json:async()=>JSON.parse(fs.readFileSync('dist/demo/'+path.replace('./','')))})};
context.window=context;context.globalThis=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('demo/engine.js','utf8'),context);
vm.runInContext(fs.readFileSync('demo/api.js','utf8'),context);
(async()=>{
  const api=context.sentinelDemo;
  assert.equal((await api('/api/v1/me')).data.role,'admin');
  assert.equal((await api('/api/v1/overview')).data.total,400);
  const first=(await api('/api/v1/transactions?limit=50')).data;
  const second=(await api('/api/v1/transactions?limit=50&cursor='+first.next_cursor)).data;
  assert.equal(new Set([...first.items,...second.items].map(r=>r.transaction_id)).size,100);
  const tx={transaction_id:'00000000-0000-0000-0000-000000000001',customer_id:'demo-new',merchant_id:'payee',device_id:'device',
    currency:'INR',amount_minor:12345,country:'IN',channel:'upi',occurred_at:new Date().toISOString()};
  const options={body:JSON.stringify(tx)};
  const scored=(await api('/api/v1/score',options)).data;
  assert.equal(scored.decision,'REVIEW');assert.equal(scored.currency,'INR');
  assert.deepEqual((await api('/api/v1/score',options)).data,scored);
  const feedback='/api/v1/transactions/'+tx.transaction_id+'/feedback';
  assert.equal((await api(feedback,{body:'{"label":1}'})).data.label,1);
  await assert.rejects(api(feedback,{body:'{"label":0}'}),/Conflicting/);
  assert.equal((await api('/api/v1/model')).data.manifest.currency,'INR');
  assert.equal((await api('/api/v1/health')).data.runtime,'Browser only');
  console.log('Public demo login, analytics, pagination, INR scoring, retries and feedback passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});

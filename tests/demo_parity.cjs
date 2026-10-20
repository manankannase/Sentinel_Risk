const fs=require('node:fs');
const assert=require('node:assert/strict');
const {features,predict}=require('../demo/engine.js');
const model=JSON.parse(fs.readFileSync('dist/demo/model.json'));
const rows=JSON.parse(fs.readFileSync('dist/demo-parity.json'));
let maximum=0;
for(const row of rows){
  const actual=features(row.transaction,row.history);
  for(const key of model.manifest.feature_names)assert.ok(Math.abs(actual[key]-row.features[key])<1e-9,`feature mismatch ${key}`);
  const delta=Math.abs(predict(model,actual)-row.score);maximum=Math.max(maximum,delta);
  assert.ok(delta<2e-5,`prediction mismatch: ${delta}`);
}
console.log(`${rows.length} browser/Python feature and prediction parity checks passed; maximum score delta ${maximum}`);

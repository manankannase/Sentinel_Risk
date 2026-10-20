/* Browser inference for the exported, synthetic INR model. No network or storage access. */
(function(root) {
  "use strict";
  function features(tx, history) {
    if(tx.currency !== "INR") throw new Error("INR required");
    const t = Date.parse(tx.occurred_at);
    const past = history.filter(h => h.customer_id === tx.customer_id && h.currency === "INR" && Date.parse(h.occurred_at) < t && Date.parse(h.occurred_at) >= t-86400000);
    const amount = tx.amount_minor / 100, total = past.reduce((sum,h)=>sum+h.amount_minor/100,0);
    const local = new Date(t+19800000), angle = 2*Math.PI*(local.getUTCHours()+local.getUTCMinutes()/60)/24;
    return {
      log_amount: Math.log1p(amount), amount_ratio: Math.min(amount/Math.max(past.length ? total/past.length : 2000,1),1000),
      count_1h: past.filter(h=>Date.parse(h.occurred_at)>=t-3600000).length, count_24h: past.length,
      log_sum_24h: Math.log1p(total), new_device: Number(!!past.length && !past.some(h=>h.device_id===tx.device_id)),
      new_country: Number(!!past.length && !past.some(h=>h.country===tx.country)), hour_sin: Math.sin(angle), hour_cos: Math.cos(angle),
      is_ecommerce: Number(tx.channel==='ecommerce'), is_atm: Number(tx.channel==='atm'),
      count_5m: past.filter(h=>Date.parse(h.occurred_at)>=t-300000).length, cold_start: Number(!past.length),
      new_payee: Number(!!past.length && !past.some(h=>h.merchant_id===tx.merchant_id)),
      is_upi: Number(tx.channel==='upi'), is_transfer: Number(['imps','neft','rtgs'].includes(tx.channel))
    };
  }
  function predict(model, values) {
    let margin = Math.fround(model.base_margin);
    for(const tree of model.trees) {
      let node = tree;
      while(node.leaf === undefined) {
        const next = Math.fround(values[node.split]) < Math.fround(node.split_condition) ? node.yes : node.no;
        node = node.children.find(child=>child.nodeid===next);
      }
      margin = Math.fround(margin + Math.fround(node.leaf));
    }
    const raw = Math.max(1e-7,Math.min(1-1e-7,Math.fround(1/(1+Math.exp(-margin)))));
    const c = model.manifest.calibration;
    return 1/(1+Math.exp(-Math.max(-40,Math.min(40,c.slope*Math.log(raw/(1-raw))+c.intercept))));
  }
  root.SentinelDemoModel = {features, predict};
  if(typeof module !== 'undefined') module.exports = root.SentinelDemoModel;
})(typeof globalThis === 'undefined' ? window : globalThis);

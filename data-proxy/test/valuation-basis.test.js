import assert from 'node:assert/strict';
import test from 'node:test';
import {addCompositeValuationScore} from '../src/composite-valuation.js';
import {valuationInput,buildValuationTable,applyValuationTable,sharedValuationTable,calculationBasis,VALUATION_BASIS_VERSION} from '../src/valuation-basis.js';
import {rebuildNotificationBasis,runStrategyIncremental,strategySnapshot,addIndicators} from '../src/strategy-runtime.js';
import {NotificationLedger} from '../src/notification-ledger.js';
import worker,{refreshNotificationTicker} from '../src/index.js';
import {isUsEquitySession,previousUsEquitySession} from '../src/us-market-calendar.js';

function prices(){
  const data={QQQ:[],SPY:[],BIL:[]};let index=0;
  for(let time=Date.parse('2004-01-02');time<=Date.parse('2026-11-02');time+=86400000){
    if([0,6].includes(new Date(time).getUTCDay()))continue;
    const DateKey=new Date(time).toISOString().slice(0,10);
    for(const ticker of Object.keys(data)){
      if(ticker==='BIL'&&DateKey<'2007-05-30')continue;
      const Close=ticker==='BIL'?90+index*.001:100*Math.exp(index*(ticker==='QQQ'?.0003:.0002))*(1+.1*Math.sin(index/200));
      data[ticker].push({Date:DateKey,Open:Close,High:Close,Low:Close,Close,RawClose:Close,Dividends:index%63===0?.2:0,Volume:100});
    }
    index++;
  }
  return data;
}
const source=prices();
const memory=()=>{const map=new Map();return {map,get:async key=>map.get(key)||null,put:async(key,value)=>map.set(key,value)};};

test('monthly closing sessions skip US exchange holidays rather than waiting for nonexistent rows',()=>{
  assert.equal(previousUsEquitySession('2027-06-01'),'2027-05-28');
  assert.equal(isUsEquitySession('2026-04-03'),false);
  assert.equal(isUsEquitySession('2026-06-19'),false);
  assert.equal(isUsEquitySession('2026-07-03'),false);
  assert.equal(isUsEquitySession('2026-11-26'),false);
  assert.equal(isUsEquitySession('2026-11-27'),true);
  assert.equal(isUsEquitySession('2027-12-31'),true);
  assert.equal(isUsEquitySession('2025-01-09'),false);
});

test('monthly table equals full history and remains identical when the consumer has only 1500 rows',async()=>{
  const table=buildValuationTable(await valuationInput(source,'2026-11','2026-10-30'));
  const full=structuredClone(source);addCompositeValuationScore(full);
  const short=Object.fromEntries(Object.entries(source).map(([t,rows])=>[t,structuredClone(rows.slice(-1500))]));
  addCompositeValuationScore(short);
  assert.notEqual(short.QQQ.at(-1).VALUATION_SCORE,full.QQQ.at(-1).VALUATION_SCORE);
  applyValuationTable(short,table);
  const expected=new Map(full.QQQ.map(row=>[row.Date,row.VALUATION_SCORE]));
  for(const row of short.QQQ)assert.equal(row.VALUATION_SCORE,expected.get(row.Date),row.Date);
});

test('current-month prices do not invalidate the monthly cache; corrections to closed history do',async()=>{
  const kv=memory();let builds=0;
  const env={MARKET_DATA:kv,VALUATION_DATA:{getByName:()=>({build:async input=>{builds++;return buildValuationTable(input);}})}};
  const first=await sharedValuationTable(env,source,'2026-11');
  const changed=structuredClone(source);changed.QQQ.at(-1).Close*=.5;
  assert.equal((await sharedValuationTable(env,changed,'2026-11')).revision,first.revision);
  assert.equal(builds,1);
  changed.QQQ.at(-2).Close*=.5;
  assert.notEqual((await sharedValuationTable(env,changed,'2026-11')).revision,first.revision);
  assert.equal(builds,2);
});

test('short history, missing closing session and mismatched markets cannot produce a ready score',async()=>{
  const short=Object.fromEntries(Object.entries(source).map(([t,rows])=>[t,rows.slice(-1500)]));
  await assert.rejects(()=>valuationInput(short,'2026-11'),/history incomplete/);
  await assert.rejects(()=>valuationInput(source,'2026-11','2026-10-29'),/closing session/);
  const missing=structuredClone(source);missing.SPY=missing.SPY.filter(row=>row.Date!=='2026-10-30');
  await assert.rejects(()=>valuationInput(missing,'2026-11'),/mismatched/);
});

test('a new month preserves the previous checkpoint identity; past score corrections invalidate it',async()=>{
  const table=buildValuationTable(await valuationInput(source,'2026-11'));
  const previous=await calculationBasis(table,'2026-10-28');
  const beforeNovember={...table,scores:table.scores.filter(row=>row.month<'2026-11')};
  assert.deepEqual(await calculationBasis(beforeNovember,'2026-10-28'),previous);
  const corrected=structuredClone(table);corrected.scores.find(row=>row.month==='2026-06').score+=1;
  assert.notEqual((await calculationBasis(corrected,'2026-10-28')).history_revision,previous.history_revision);
});

test('history backfill runs once, then refreshes only the recent overlap without truncating the archive',async()=>{
  const kv=memory(),now=Date.parse('2026-11-03T00:00:00Z'),starts=[];
  const rows=source.QQQ.map(r=>({date:r.Date,open:r.Open,high:r.High,low:r.Low,close:r.Close,volume:r.Volume}));
  const options={now,historyStart:'2004-01-01',preserveAllHistory:true,loadRows:async(_t,start)=>{starts.push(start);return rows.filter(r=>Date.parse(r.date)/1000>=start);}};
  await refreshNotificationTicker({MARKET_DATA:kv},'QQQ',options);
  const second=await refreshNotificationTicker({MARKET_DATA:kv},'QQQ',options);
  assert.equal(starts.length,1);assert.equal(second.rows.length,source.QQQ.length);
  const meta=JSON.parse(kv.map.get('market-refresh:QQQ'));meta.expected_session='2026-10-30';kv.map.set('market-refresh:QQQ',JSON.stringify(meta));
  await refreshNotificationTicker({MARKET_DATA:kv},'QQQ',options);
  assert.equal(starts.length,2);assert.equal(starts[1],Date.parse('2026-10-26')/1000);
});

test('failed backfill retains prices, defers retries and does not advertise complete history',async()=>{
  const kv=memory(),now=Date.parse('2026-11-03T00:00:00Z');let calls=0;
  kv.map.set('notification-tail:QQQ',JSON.stringify(source.QQQ.slice(-1500)));
  const options={now,historyStart:'2004-01-01',preserveAllHistory:true,loadRows:async()=>{calls++;throw Error('unavailable');}};
  const result=await refreshNotificationTicker({MARKET_DATA:kv},'QQQ',options);
  assert.equal(result.freshness.history_ready,false);assert.equal(result.freshness.status,'stale');
  assert.equal(result.rows.length,1500);
  await refreshNotificationTicker({MARKET_DATA:kv},'QQQ',options);assert.equal(calls,1);
});

test('basis recovery corrects peak memory while preserving holdings and quarantining invalid pending events',()=>{
  const def={strategy:{id:'recovery'},assets:{required:['QQQ','BIL']},state:{peak:{initial:0,rules:[{when:'QQQ.valuation_score > state.peak',set:'=QQQ.valuation_score'}]},mode:{initial:'NORMAL',rules:[{when:'QQQ.valuation_score < state.peak - 25',set:'DEFENSE'}]}},target:[{when:"state.mode == 'DEFENSE'",weights:{QQQ:'30%',BIL:'70%'}},{weights:{QQQ:'70%',BIL:'30%'}}],rebalance:[{check:'daily',when:'changed(state.mode)'}]};
  const row=(Date,score)=>({Date,Open:100,High:100,Low:100,Close:100,Volume:1,VALUATION_SCORE:score});
  const data={QQQ:[row('2026-06-01',72.18),row('2026-10-28',69),row('2026-11-02',51.68)],BIL:[row('2026-06-01',0),row('2026-10-28',0),row('2026-11-02',0)]};
  const old={date:'2026-10-28',portfolio:{cash:5,positions:{QQQ:.7,BIL:.3},pending:{QQQ:.3,BIL:.7},remaining:1,active:{}},runtime:{state:{peak:76.8,mode:'NORMAL'}},notification_outbox:[{event_id:'invalid'}]};
  const fixed=rebuildNotificationBasis([def],def,data,old,{version:VALUATION_BASIS_VERSION});
  assert.equal(fixed.runtime.state.peak,72.18);assert.deepEqual(fixed.portfolio.positions,old.portfolio.positions);assert.equal(fixed.portfolio.cash,5);
  assert.equal(fixed.portfolio.pending,null);assert.equal(fixed.notification_outbox.length,0);assert.equal(fixed.notification_quarantined_outbox[0].event_id,'invalid');
  const next=runStrategyIncremental([def],def,data,fixed);
  assert.equal(next.snapshot.runtime.state.mode,'NORMAL');assert.ok(next.history.every(row=>!row.target));
});

test('public score endpoint shares one build and returns 503 rather than a short-history fallback',async t=>{
  const original=globalThis.fetch,oldNow=Date.now;t.after(()=>{globalThis.fetch=original;Date.now=oldNow;});
  Date.now=()=>Date.parse('2026-11-03T00:00:00Z');const kv=memory();let calls=0;
  globalThis.fetch=async url=>{
    calls++;const ticker=new URL(url).pathname.split('/').at(-1),rows=source[ticker];
    return Response.json({chart:{result:[{timestamp:rows.map(r=>Date.parse(r.Date)/1000),indicators:{quote:[Object.fromEntries(['open','high','low','close','volume'].map(k=>[k,rows.map(r=>r[k==='volume'?'Volume':k[0].toUpperCase()+k.slice(1)])]))]}}]}});
  };
  const env={MARKET_DATA:kv};
  const first=await worker.fetch(new Request('https://fixture/valuation-scores'),env,{});
  assert.equal(first.status,200);assert.equal((await first.json()).version,VALUATION_BASIS_VERSION);
  const before=calls;assert.equal((await worker.fetch(new Request('https://fixture/valuation-scores'),env,{})).status,200);assert.equal(calls,before);
  globalThis.fetch=async()=>{throw Error('outage');};
  assert.equal((await worker.fetch(new Request('https://fixture/valuation-scores'),{MARKET_DATA:memory()},{})).status,503);
});

test('basis migration backup and event receipts survive an atomic commit',async()=>{
  const records=new Map(),storage={get:async k=>structuredClone(records.get(k)),put:async(k,v)=>records.set(k,structuredClone(v))};storage.transaction=fn=>fn(storage);
  const ledger=new NotificationLedger(storage);
  await ledger.commit({date:'2026-10-28',notification_outbox:[{event_id:'a'}]},0);
  await ledger.claim(['a'],'sender');await ledger.ack(['a'],'sender',123);
  const old=await ledger.read();
  await ledger.commit({...old,calculation_basis:{version:VALUATION_BASIS_VERSION},basis_recovery:{revision:'fixed'}},old.notification_revision);
  assert.equal(records.get('record').receipts[0].message_id,123);
  assert.equal(records.get(`basis-backup:${VALUATION_BASIS_VERSION}:fixed`).snapshot.date,'2026-10-28');
});

test('evaluation previews recovery without advancing state, then atomically corrects once and retains delivery evidence',async t=>{
  const original=globalThis.fetch,oldNow=Date.now;t.after(()=>{globalThis.fetch=original;Date.now=oldNow;});
  Date.now=()=>Date.parse('2026-11-03T00:00:00Z');
  const data=structuredClone(source);for(const rows of Object.values(data))addIndicators(rows);
  const table=buildValuationTable(await valuationInput(data,'2026-11'));applyValuationTable(data,table);
  const def={strategy:{id:'basis-recovery',name:'Recovery',version:1},assets:{required:['QQQ','SPY','BIL']},state:{peak:{initial:0,rules:[{when:'QQQ.valuation_score > state.peak',set:'=QQQ.valuation_score'}]}},target:[{weights:{QQQ:'70%',SPY:'0%',BIL:'30%'}}],rebalance:[]};
  const past=Object.fromEntries(Object.entries(data).map(([t,rows])=>[t,rows.filter(r=>r.Date<='2026-10-28')]));
  const old=strategySnapshot([def],def,past);const correctPeak=old.runtime.state.peak;old.runtime.state.peak+=10;old.notification_outbox=[{event_id:'invalid',type:'REBALANCE'}];
  const records=new Map(),storage={get:async k=>structuredClone(records.get(k)),put:async(k,v)=>records.set(k,structuredClone(v))};storage.transaction=fn=>fn(storage);
  const ledger=new NotificationLedger(storage);await ledger.commit(old,0);
  const kv=memory();
  for(const [ticker,rows] of Object.entries(data)){
    kv.map.set(`notification-tail:${ticker}`,JSON.stringify(rows));
    kv.map.set(`market-refresh:${ticker}`,JSON.stringify({expected_session:'2026-11-02',latest_session:'2026-11-02',history_start:Date.parse('2004-01-01')/1000,history_month:'2026-11',status:'fresh'}));
  }
  const env={MARKET_DATA:kv,NOTIFICATION_API_KEY:'fixture',STRATEGY_MANIFEST_URL:'https://fixture/manifest.json',NOTIFICATION_STATE:{getByName:()=>ledger}};
  globalThis.fetch=async url=>String(url).endsWith('manifest.json')?Response.json({enabled_bundle:'enabled.json',strategies:[{id:def.strategy.id,path:'a.yaml'}]}):Response.json([def]);
  const evaluate=async preview=>{
    const reply=await worker.fetch(new Request('https://fixture/notification-evaluations',{method:'POST',headers:{Authorization:'Bearer fixture','Content-Type':'application/json'},body:JSON.stringify({strategy_ids:[def.strategy.id],preview})}),env,{});
    assert.equal(reply.status,200);return (await reply.json()).evaluations[0];
  };
  const preview=await evaluate(true);assert.equal(preview.basis_recovered,true);assert.equal((await ledger.read()).runtime.state.peak,correctPeak+10);
  const live=await evaluate(false);assert.equal(live.evaluation_status,'READY');assert.equal(live.basis_recovered,true);
  assert.ok(live.alerts.some(event=>event.type==='UPDATE'));assert.ok(live.alerts.every(event=>event.event_id!=='invalid'));
  const saved=await ledger.read();assert.equal(saved.runtime.state.peak,correctPeak);assert.equal(saved.notification_quarantined_outbox[0].event_id,'invalid');
  assert.equal((await evaluate(false)).basis_recovered,false);
});

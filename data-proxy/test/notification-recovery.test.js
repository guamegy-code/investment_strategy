import assert from 'node:assert/strict';
import test from 'node:test';
import worker,{loadDefinitions,refreshNotificationTicker,loadPriceRange,expectedCompletedSession} from '../src/index.js';
import {NotificationLedger,nextRecoveryTime} from '../src/notification-ledger.js';
import {strategySnapshot} from '../src/strategy-runtime.js';

function storage(){
  const records=new Map();let tail=Promise.resolve();
  const api={get:async key=>structuredClone(records.get(key)),put:async(key,value)=>{records.set(key,structuredClone(value));}};
  api.transaction=callback=>{const result=tail.then(()=>callback(api));tail=result.catch(()=>{});return result;};
  return api;
}

test('state and pending events commit together; stale evaluations cannot overwrite ACKs',async()=>{
  const ledger=new NotificationLedger(storage());
  await ledger.commit({date:'2026-10-28',notification_outbox:[{event_id:'event-1'}]},0);
  const snapshot=await ledger.read();
  assert.deepEqual(await ledger.claim(['event-1'],'sender-a'),['event-1']);
  assert.deepEqual(await ledger.claim(['event-1'],'sender-b'),[]);
  await ledger.ack(['event-1'],'sender-b',9);
  assert.equal((await ledger.read()).notification_outbox.length,1);
  await ledger.ack(['event-1'],'sender-a',10);
  await assert.rejects(()=>ledger.commit(snapshot,snapshot.notification_revision),/state changed/);
  assert.equal((await ledger.read()).notification_outbox.length,0);
});

test('abandoned delivery lease becomes claimable and recovery stays scheduled',async()=>{
  const ledger=new NotificationLedger(storage());
  await ledger.commit({notification_outbox:[{event_id:'a'}]},0);
  await ledger.claim(['a'],'dead-sender',1000);
  assert.deepEqual(await ledger.claim(['a'],'replacement',121001),['a']);
  const now=Date.parse('2026-11-03T00:00:00Z');
  assert.equal(nextRecoveryTime(false,now),now+300000);
  assert.equal(nextRecoveryTime(true,now),Date.parse('2026-11-03T23:15:00Z'));
});

test('selected strategy bundle replaces the full YAML catalogue',async t=>{
  const original=globalThis.fetch; t.after(()=>globalThis.fetch=original);
  let calls=0;
  globalThis.fetch=async url=>{calls++;return String(url).endsWith('manifest.json')?Response.json({enabled_bundle:'enabled.json',strategies:Array.from({length:48},(_,i)=>({id:'s'+i,path:i+'.yaml'}))}):Response.json([{strategy:{id:'s1'}}]);};
  const definitions=await loadDefinitions({STRATEGY_MANIFEST_URL:'https://fixture/manifest.json'},['s1']);
  assert.equal(definitions.length,1);assert.equal(calls,2);
});

test('valuation refresh preserves history; deferred refresh preserves its actual failure',async t=>{
  const original=globalThis.fetch;t.after(()=>globalThis.fetch=original);
  const records=new Map(),now=Date.parse('2026-11-03T00:00:00Z');
  const rows=Array.from({length:1500},(_,i)=>({Date:new Date(Date.parse('2020-01-01')+i*86400000).toISOString().slice(0,10),Open:1,High:1,Low:1,Close:1,Volume:1}));
  records.set('notification-tail:QQQ',JSON.stringify(rows));
  const env={MARKET_DATA:{get:async key=>records.get(key)||null,put:async(key,value)=>records.set(key,value)}};
  globalThis.fetch=async()=>Response.json({chart:{result:[{timestamp:[Date.parse('2026-11-02')/1000],indicators:{quote:[{open:[1],high:[1],low:[1],close:[1],volume:[1]}]}}]}});
  await refreshNotificationTicker(env,'QQQ',{tailRows:20,lookbackDays:30,now});
  assert.equal(JSON.parse(records.get('notification-tail:QQQ')).length,1500);
  records.set('market-refresh:QQQ',JSON.stringify({expected_session:'2026-11-02',status:'stale',last_error:'network failed',next_refresh_at:new Date(now+60000).toISOString()}));
  records.set('notification-tail:QQQ',JSON.stringify([rows[0]]));
  const result=await refreshNotificationTicker(env,'QQQ',{tailRows:1,now});
  assert.equal(result.freshness.status,'stale');assert.equal(result.freshness.last_error,'network failed');
});

test('lost evaluation response replays its pending event until a successful delivery ACK',async t=>{
  const original=globalThis.fetch,oldNow=Date.now;t.after(()=>{globalThis.fetch=original;Date.now=oldNow;});
  Date.now=()=>Date.parse('2026-10-29T23:00:00Z');
  const def={strategy:{id:'simple',name:'Simple',version:1},assets:{required:['QQQ','BIL']},state:{mode:{initial:'NORMAL',check:'daily',rules:[{when:'QQQ.close < 90',set:'DEFENSE'}]}},target:[{when:"state.mode == 'DEFENSE'",weights:{QQQ:'0%',BIL:'100%'}},{weights:{QQQ:'100%',BIL:'0%'}}],rebalance:[{check:'daily',when:'changed(state.mode)'}]};
  const row=(date,price)=>({Date:date,Open:price,High:price,Low:price,Close:price,Volume:1});
  const snapshot=strategySnapshot([def],def,{QQQ:[row('2026-10-28',100)],BIL:[row('2026-10-28',100)]});
  const records=new Map([['notification-state:simple',JSON.stringify(snapshot)]]),ledgers=new Map();
  const env={NOTIFICATION_API_KEY:'test',STRATEGY_MANIFEST_URL:'https://fixture/manifest.json',MARKET_DATA:{get:async key=>records.get(key)||null,put:async(key,value)=>records.set(key,value)},NOTIFICATION_STATE:{getByName:key=>{if(!ledgers.has(key))ledgers.set(key,new NotificationLedger(storage()));return ledgers.get(key);}}};
  globalThis.fetch=async url=>{
    const path=new URL(url).pathname;
    if(path.endsWith('manifest.json'))return Response.json({enabled_bundle:'enabled.json',strategies:[{id:'simple',path:'simple.yaml'}]});
    if(path.endsWith('enabled.json'))return Response.json([def]);
    const price=path.endsWith('QQQ')?80:100;
    return Response.json({chart:{result:[{timestamp:[Date.parse('2026-10-29')/1000],indicators:{quote:[{open:[price],high:[price],low:[price],close:[price],volume:[1]}]}}]}});
  };
  const evaluate=async()=>{const response=await worker.fetch(new Request('https://fixture/notification-evaluations',{method:'POST',headers:{Authorization:'Bearer test','Content-Type':'application/json'},body:JSON.stringify({strategy_ids:['simple'],client_id:'client-a'})}),env,{});assert.equal(response.status,200);return response.json();};
  const first=await evaluate(),second=await evaluate();
  const event=first.evaluations[0].alerts.find(item=>item.type==='REBALANCE');assert.ok(event);
  assert.equal(second.evaluations[0].alerts[0].event_id,event.event_id);
  const ledger=env.NOTIFICATION_STATE.getByName('client-a:simple');
  await ledger.claim([event.event_id],'sender');await ledger.ack([event.event_id],'sender',12);
  assert.equal((await evaluate()).evaluations[0].alerts.length,0);
});

test('public daily prices exclude a bar beyond the last completed market session',async t=>{
  const original=globalThis.fetch;t.after(()=>globalThis.fetch=original);
  const closed=expectedCompletedSession('QQQ'),closedTime=Date.parse(closed);
  globalThis.fetch=async()=>Response.json({chart:{result:[{timestamp:[closedTime/1000,(closedTime+86400000)/1000],indicators:{quote:[{open:[100,101],high:[100,101],low:[100,101],close:[100,101],volume:[1,1]}]}}]}});
  const result=await loadPriceRange({},null,'QQQ',closedTime/1000,(Date.now()+2*86400000)/1000);
  assert.deepEqual(result.rows.map(row=>row.date),[closed]);
});

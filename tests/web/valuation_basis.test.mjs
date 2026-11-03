import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source=readFileSync(new URL('../../src/web/app.js',import.meta.url),'utf8');
const helper=source.slice(source.indexOf('let sharedValuationRequest='),source.indexOf('async function downloadMissingData(def){',source.indexOf('let sharedValuationRequest=')));
function runtime(fetch){
  const context=vm.createContext({URL,Date,Map,AbortSignal,proxyUrl:'https://fixture',PROXY_REFRESH_INTERVAL_MS:900000,
    latestCachedDate:rows=>rows.at(-1)?.Date||'',addCompositeValuationScore:()=>{throw Error('local fallback must not run');},fetch});
  vm.runInContext(helper,context);return context;
}
test('dashboard and indicator consumers share the server table, even with short local history',async()=>{
  let calls=0;
  const app=runtime(async()=>{calls++;return {ok:true,json:async()=>({version:'composite-full-history-v1',status:'ready',month:'2026-11',scores:[{month:'2026-06',score:72.18},{month:'2026-11',score:51.68}]})};});
  app.data={QQQ:[{Date:'2026-06-01',VALUATION_SCORE:76.8},{Date:'2026-11-02',VALUATION_SCORE:47.4}]};
  await vm.runInContext('Promise.all([ensureCompositeValuationScores(data),ensureCompositeValuationScores(data)])',app);
  assert.equal(calls,1);assert.equal(app.data.QQQ[0].VALUATION_SCORE,72.18);assert.equal(app.data.QQQ[1].VALUATION_SCORE,51.68);
});
test('unavailable shared scores preserve previous data and allow a later retry',async()=>{
  let calls=0;
  const app=runtime(async()=>{calls++;return {ok:false,json:async()=>({status:'pending',error:'history pending'})};});
  app.data={QQQ:[{Date:'2026-11-02',VALUATION_SCORE:47.4}]};
  for(let i=0;i<2;i++)await assert.rejects(()=>vm.runInContext('ensureCompositeValuationScores(data)',app),/history pending/);
  assert.equal(calls,2);assert.equal(app.data.QQQ[0].VALUATION_SCORE,47.4);
});
test('an old monthly table cannot label newer market data as current',async()=>{
  const app=runtime(async()=>({ok:true,json:async()=>({version:'composite-full-history-v1',status:'ready',month:'2026-10',scores:[]})}));
  app.data={QQQ:[{Date:'2026-11-02'}]};
  await assert.rejects(()=>vm.runInContext('ensureCompositeValuationScores(data)',app),/갱신/);
});

import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source=readFileSync(new URL('../../src/web/app.js',import.meta.url),'utf8');
const fetchSource=source.slice(source.indexOf('function latestCachedDate('),source.indexOf('function addCompositeValuationScore('));

function runtime(reply){
  const states=new Map(),saved=new Map();let calls=0;
  const context=vm.createContext({Map,Set,URL,Date,Promise,Array,Object,Number,String,AbortSignal,console:{warn:()=>{}},
    proxyUrl:'https://fixture.test',addStrategyIndicators:rows=>rows,isDataProxyTicker:()=>true,indicatorTickerLabels:{},
    saveCachedPrices:async(ticker,rows)=>saved.set(ticker,rows),webDataState:(ticker,state)=>states.set(ticker,state),
    fetch:async()=>{calls++;if(reply instanceof Error)throw reply;return {ok:true,json:async()=>reply};},
  });
  vm.runInContext(fetchSource,context);
  return {context,states,saved,call:code=>vm.runInContext(code,context),calls:()=>calls};
}

test('HTTP success with stale prices remains stale and is eligible for a second attempt',async()=>{
  const app=runtime({data:{QQQ:[{date:'2026-10-28',open:1,high:1,low:1,close:1,volume:1}]},price_freshness:{QQQ:{status:'pending',expected_session:'2026-11-02'}}});
  await app.call("fetchProxyTickerData({},['QQQ'])");
  assert.equal(app.states.get('QQQ').status,'stale');
  assert.equal(app.states.get('QQQ').latest,'2026-10-28');
  await app.call("fetchProxyTickerData({},['QQQ'])");assert.equal(app.calls(),2);
});

test('a failed refresh preserves usable cached data and exposes the failure state',async()=>{
  const app=runtime(new Error('Injected outage'));
  const data={QQQ:[{Date:'2026-10-28',Close:10}],SPY:[{Date:'2026-11-02',Close:20}]};
  app.context.input=data;
  await app.call("fetchProxyTickerData(input,['QQQ'])");
  assert.equal(data.QQQ[0].Close,10);assert.equal(data.SPY[0].Close,20);
  assert.equal(app.states.get('QQQ').status,'failed');
  assert.equal(app.states.get('QQQ').latest,'2026-10-28');
});

test('one missing ticker is marked failed while successful ticker data is saved',async()=>{
  const app=runtime({data:{QQQ:[{date:'2026-11-02',open:1,high:1,low:1,close:1,volume:1}]},price_freshness:{QQQ:{status:'fresh',expected_session:'2026-11-02'}}});
  await app.call("fetchProxyTickerData({},['QQQ','SPY'])");
  assert.equal(app.states.get('QQQ').status,'ready');assert.equal(app.states.get('SPY').status,'failed');
  assert.ok(app.saved.has('QQQ'));
});

test('recalculation failure is isolated to its strategy and does not erase other results',async()=>{
  const recovery={strategies:new Map(),prices:new Map(),forceResults:new Set()};
  const context=vm.createContext({webRecovery:recovery,renderWebRecovery:()=>{},calculateStrategy:async()=>{throw new Error('Bad input');},$:()=>({value:''}),strategyDependencies:()=>({tickers:[]})});
  vm.runInContext(source.slice(source.indexOf('async function run(def,options='),source.indexOf('function webDataState(')),context);
  recovery.strategies.set('other',{status:'ready'});
  await assert.rejects(()=>vm.runInContext("run({strategy:{id:'failed',name:'Failed'}})",context),/Bad input/);
  assert.equal(recovery.strategies.get('failed').status,'failed');assert.equal(recovery.strategies.get('other').status,'ready');
});

test('manual recalculation targets one strategy and preserves other results',async()=>{
  const definitions=[{strategy:{id:'a'}},{strategy:{id:'b'}}];
  const results=new Map([['a','old-a'],['b','old-b']]);
  const recovery={strategies:new Map(),prices:new Map(),forceResults:new Set(),busy:false};
  const context=vm.createContext({Map,Set,webRecovery:recovery,renderWebRecovery:()=>{},selectedDefinitions:()=>definitions,
    dashboardResults:results,runDashboard:async()=>true,$:()=>({classList:{contains:()=>true}}),strategyDependencies:()=>({tickers:[]})});
  vm.runInContext(source.slice(source.indexOf('async function retryWebsite('),source.indexOf('globalThis.OfflineStrategyRuntime=',source.indexOf('async function retryWebsite('))),context);
  await vm.runInContext("retryWebsite('calculate','a')",context);
  assert.deepEqual([...recovery.forceResults],['a']);assert.equal(results.get('b'),'old-b');assert.equal(results.get('a'),'old-a');assert.equal(recovery.busy,false);
});

function installDependencies(context){
  const start=source.indexOf('function strategyDependencies(');
  vm.runInContext(source.slice(start,source.indexOf('function strategyTickers(',start)),context);
}

test('successful calculation checks real dependency metadata for stale data',async()=>{
  const def={strategy:{id:'a',name:'A'},assets:{required:['QQQ']}};
  const recovery={strategies:new Map(),prices:new Map([['QQQ',{status:'stale',latest:'2026-10-28'}]]),forceResults:new Set(['a'])};
  const context=vm.createContext({webRecovery:recovery,definitions:[def],def,renderWebRecovery:()=>{},
    calculateStrategy:async()=>[{date:'2026-10-28'}],$:()=>({value:''}),krwAdjustedBaseTicker:()=>null,tickerFxRate:()=>null});
  installDependencies(context);
  vm.runInContext(source.slice(source.indexOf('async function run(def,options='),source.indexOf('function webDataState(')),context);
  await vm.runInContext('run(def)',context);
  assert.equal(recovery.strategies.get('a').status,'previous');
  assert.equal(recovery.forceResults.has('a'),false);
  recovery.prices.set('QQQ',{status:'ready',latest:'2026-11-02'});
  await vm.runInContext('run(def)',context);
  assert.equal(recovery.strategies.get('a').status,'ready');
});

for(const action of ['data','all'])test(`${action} retry uses real dependencies and recalculates only affected strategies`,async()=>{
  const definitions=[{strategy:{id:'a'},assets:{required:['QQQ']}},{strategy:{id:'b'},assets:{required:['SPY']}}];
  const recovery={strategies:new Map(),prices:new Map([['QQQ',{status:'failed'}]]),forceResults:new Set(),busy:false};
  const freshness=new Map([['QQQ',true],['SPY',true]]),fetched=[],status={};let dashboardCalls=0;
  const context=vm.createContext({webRecovery:recovery,definitions,renderWebRecovery:()=>{},selectedDefinitions:()=>definitions,
    proxyTickerFreshness:freshness,loadData:async()=>({}),fetchProxyTickerData:async(_data,tickers)=>fetched.push(...tickers),
    runDashboard:async()=>{dashboardCalls++;},$:id=>id==='status'?status:{classList:{contains:()=>true}},
    krwAdjustedBaseTicker:()=>null,tickerFxRate:()=>null});
  installDependencies(context);
  vm.runInContext(source.slice(source.indexOf('async function retryWebsite('),source.indexOf('globalThis.OfflineStrategyRuntime=',source.indexOf('async function retryWebsite('))),context);
  await vm.runInContext(`retryWebsite('${action}','QQQ')`,context);
  assert.equal(status.textContent,undefined);
  assert.deepEqual(fetched,['QQQ']);assert.deepEqual([...recovery.forceResults],['a']);
  assert.equal(freshness.has('QQQ'),false);assert.equal(freshness.has('SPY'),true);
  assert.equal(dashboardCalls,1);assert.equal(recovery.busy,false);
});

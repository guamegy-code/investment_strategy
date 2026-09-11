
const bundle = JSON.parse(document.getElementById('strategy-bundle').textContent);
const $ = (id) => document.getElementById(id);
let marketData = null;
let definitions = bundle.strategies || [];
let precomputedResults = null;
let dashboardResults = new Map();
const proxyUrl = bundle.data_proxy || localStorage.getItem('investment-strategy:data-proxy') || '';

function bytesFromBase64(text) {
  const binary = atob(text), bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}
function openCache() { return new Promise((resolve,reject)=>{const request=indexedDB.open('investment-strategy-data',2);request.onupgradeneeded=()=>{const db=request.result;if(!db.objectStoreNames.contains('prices'))db.createObjectStore('prices');if(!db.objectStoreNames.contains('market-data'))db.createObjectStore('market-data');};request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);}); }
async function cachedPrices(ticker) { const db=await openCache(); return new Promise((resolve,reject)=>{const request=db.transaction('prices').objectStore('prices').get(ticker);request.onsuccess=()=>resolve(request.result||null);request.onerror=()=>reject(request.error);}); }
async function saveCachedPrices(ticker, rows) { const db=await openCache(); return new Promise((resolve,reject)=>{const request=db.transaction('prices','readwrite').objectStore('prices').put(rows,ticker);request.onsuccess=()=>resolve();request.onerror=()=>reject(request.error);}); }
async function cachedMarketData(version) { const db=await openCache(); return new Promise((resolve,reject)=>{const request=db.transaction('market-data').objectStore('market-data').get(version);request.onsuccess=()=>resolve(request.result||null);request.onerror=()=>reject(request.error);}); }
async function saveCachedMarketData(version, data) { const db=await openCache(); return new Promise((resolve,reject)=>{const request=db.transaction('market-data','readwrite').objectStore('market-data').put(data,version);request.onsuccess=()=>resolve();request.onerror=()=>reject(request.error);}); }
async function loadData() {
  if (marketData) return marketData;
  // Hosted builds load only the tickers required by the selected strategies.
  // The bundled archive remains available to the Worker as an upstream fallback,
  // but must not be downloaded by every browser on first load.
  if (bundle.static_site && proxyUrl) {
    marketData = {};
    return marketData;
  }
  if (bundle.static_site && !bundle.data && !bundle.data_url) {
    marketData = {};
    return marketData;
  }
  const version=bundle.market_data_version||bundle.data_url||'embedded-market-data';
  const cached=await cachedMarketData(version).catch(()=>null);
  if(cached){marketData=cached;return marketData;}
  if (!('DecompressionStream' in window)) throw Error('이 브라우저는 압축 데이터 해제를 지원하지 않습니다. 최신 Chrome, Edge 또는 Safari를 사용해 주세요.');
  const compressed=bundle.data?bytesFromBase64(bundle.data):new Uint8Array(await (await fetch(bundle.data_url)).arrayBuffer());
  const stream = new Blob([compressed], {type:'application/gzip'})
    .stream().pipeThrough(new DecompressionStream('gzip'));
  const raw = JSON.parse(await new Response(stream).text());
  marketData = Object.fromEntries(Object.entries(raw).map(([ticker, csv]) => [ticker, parseCsv(csv)]));
  saveCachedMarketData(version,marketData).catch(()=>{});
  return marketData;
}
async function loadPrecomputedResults() {
  if (precomputedResults) return precomputedResults;
  if (!bundle.precomputed_results) return precomputedResults = {};
  const stream = new Blob([bytesFromBase64(bundle.precomputed_results || '')], {type:'application/gzip'}).stream().pipeThrough(new DecompressionStream('gzip'));
  precomputedResults = JSON.parse(await new Response(stream).text());
  return precomputedResults;
}
function strategyTickers(def) { if (def.source) { const source=definitions.find(item=>item.strategy.id===def.source); return [...new Set([...(source?.assets?.required||[]),...Object.values(def.products||{}).flatMap(Object.keys)])]; } return def.assets.required; }
function addStrategyIndicators(rows) {
  const close=rows.map(row=>Number(row.Close)), ema=(span)=>{const out=[],alpha=2/(span+1);close.forEach((value,index)=>out.push(index?value*alpha+out[index-1]*(1-alpha):value));return out;}, ema20=ema(20),ema55=ema(55),ema200=ema(200);
  const rolling=(index,period,fn)=>index<period-1?NaN:fn(close.slice(index-period+1,index+1));
  rows.forEach((row,index)=>{const roc=(period)=>index<period?NaN:(close[index]/close[index-period]-1)*100;const rsi=index<14?NaN:(()=>{const changes=close.slice(index-14,index+1).slice(1).map((value,i)=>value-close[index-14+i]),gain=changes.reduce((sum,value)=>sum+Math.max(value,0),0)/14,loss=changes.reduce((sum,value)=>sum+Math.max(-value,0),0)/14;return loss===0?100:100-100/(1+gain/loss);})();const high=rolling(index,120,values=>Math.max(...values));row.EMA20=ema20[index];row.EMA55=ema55[index];row.EMA200=ema200[index];row.ROC5=roc(5);row.ROC20=roc(20);row.ROC40=roc(40);row.ROC60=roc(60);row.RSI14=rsi;row.DISPARITY60=index<59?NaN:close[index]/rolling(index,60,values=>values.reduce((sum,value)=>sum+value,0)/60)*100;row.DRAWDOWN120=index<119?NaN:close[index]/high-1;row.EMA20_SLOPE5=index<5?NaN:(ema20[index]/ema20[index-5]-1)*100;row.EMA200_SLOPE20=index<20?NaN:(ema200[index]/ema200[index-20]-1)*100;});
  return rows;
}
async function downloadMissingData(def) {
  const data=await loadData();
  for(const ticker of strategyTickers(def))if(!data[ticker]){const cached=await cachedPrices(ticker);if(cached)data[ticker]=cached.rows||cached;}
  const missing=strategyTickers(def).filter(ticker=>!data[ticker]);
  if(!missing.length)return data;
  if(!proxyUrl)throw Error('Missing data for '+missing.join(', ')+'. Configure a data proxy when creating this HTML.');
  const endpoint=new URL('/prices',proxyUrl); endpoint.searchParams.set('tickers',missing.join(',')); endpoint.searchParams.set('start','2010-01-01'); endpoint.searchParams.set('runtime',BROWSER_ENGINE_VERSION);
  const response=await fetch(endpoint); const payload=await response.json(); if(!response.ok)throw Error(payload.error||'Data download failed');
  for(const [ticker,rows] of Object.entries(payload.data||{})){const normalized=addStrategyIndicators(rows.map(row=>({Date:row.date,Open:row.open,High:row.high,Low:row.low,Close:row.close,Volume:row.volume})));data[ticker]=normalized;await saveCachedPrices(ticker,normalized);}
  return data;
}
function parseCsv(text) {
  const [header, ...rows] = text.trim().split(/\r?\n/).filter(Boolean);
  const columns = header.split(',');
  return rows.map(line => {
    const values = line.split(','), row = {};
    columns.forEach((column, index) => row[column] = values[index]);
    return row;
  });
}
function scalar(value) {
  value = value.trim();
  if (!value) return null;
  if ((value[0] === '"' && value.at(-1) === '"') || (value[0] === "'" && value.at(-1) === "'")) return value.slice(1,-1);
  if (value === 'true') return true; if (value === 'false') return false; if (value === 'null') return null;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  if (value[0] === '[' && value.at(-1) === ']') return splitInline(value.slice(1,-1)).filter(Boolean).map(scalar);
  if (value[0] === '{' && value.at(-1) === '}') {
    const result = {}; for (const item of splitInline(value.slice(1,-1))) { const at = colonAt(item); result[item.slice(0,at).trim()] = scalar(item.slice(at+1)); } return result;
  }
  return value;
}
function splitInline(text) { let out=[], start=0, depth=0, quote=''; for(let i=0;i<text.length;i++){const c=text[i]; if(quote){if(c===quote && text[i-1] !== '\\')quote='';} else if(c==='"'||c==="'")quote=c; else if(c==='['||c==='{')depth++; else if(c===']'||c==='}')depth--; else if(c===','&&depth===0){out.push(text.slice(start,i));start=i+1;}} out.push(text.slice(start)); return out; }
function colonAt(text) { let quote='', depth=0; for(let i=0;i<text.length;i++){const c=text[i]; if(quote){if(c===quote&&text[i-1] !== '\\')quote='';} else if(c==='"'||c==="'")quote=c; else if(c==='['||c==='{')depth++; else if(c===']'||c==='}')depth--; else if(c===':'&&depth===0)return i;} return -1; }
function cleanLine(line) { let quote=''; for(let i=0;i<line.length;i++){const c=line[i]; if(quote){if(c===quote&&line[i-1] !== '\\')quote='';} else if(c==='"'||c==="'")quote=c; else if(c==='#')return line.slice(0,i).trimEnd();} return line.trimEnd(); }
function parseYaml(text) {
  const raw = text.replace(/\r/g,'').split('\n').map(cleanLine);
  const lines=[];
  for(let i=0;i<raw.length;i++) { if(!raw[i].trim())continue; const indent=raw[i].match(/^ */)[0].length, body=raw[i].trim();
    if (body.endsWith('>') || body.endsWith('|')) {
      const key=body.slice(0,-1).trimEnd(), pieces=[]; let contentIndent=null;
      while(i+1<raw.length) {
        const next=raw[i+1], nextIndent=next.match(/^ */)[0].length;
        if (!next.trim()) { i++; continue; }
        if (nextIndent<=indent) break;
        if (contentIndent===null) contentIndent=nextIndent;
        if (nextIndent<contentIndent) break;
        i++; pieces.push(raw[i].trim());
      }
      lines.push({indent,text:key+JSON.stringify(pieces.join(' '))});
    }
    else lines.push({indent,text:body}); }
  function node(index, indent) { if(index>=lines.length||lines[index].indent<indent)return [null,index]; const array=lines[index].indent===indent&&lines[index].text.startsWith('- '), out=array?[]:{};
    while(index<lines.length&&lines[index].indent===indent&&(array===lines[index].text.startsWith('- '))){let text=lines[index].text; if(array){text=text.slice(2).trim(); if(!text){let child;[child,index]=node(index+1,lines[index+1]?.indent);out.push(child);continue;} const at=colonAt(text); if(at>0&&!text.startsWith('{')){const obj={}, key=text.slice(0,at).trim(), tail=text.slice(at+1).trim(); index++; if(tail)obj[key]=scalar(tail); else {let child;[child,index]=node(index,lines[index]?.indent);obj[key]=child;} if(index<lines.length&&lines[index].indent>indent){let extra;[extra,index]=node(index,lines[index].indent);Object.assign(obj,extra);} out.push(obj);continue;} out.push(scalar(text));index++;continue;}
      const at=colonAt(text); if(at<1)throw Error('YAML 키를 해석할 수 없습니다: '+text); const key=text.slice(0,at).trim(), tail=text.slice(at+1).trim(); index++; if(tail)out[key]=scalar(tail); else {let child;[child,index]=node(index,lines[index]?.indent);out[key]=child;} }
    return [out,index]; }
  const [result]=node(0,lines[0]?.indent||0); return result;
}
function pct(value) { if(typeof value==='string'&&/^\s*-?\d+(\.\d+)?%\s*$/.test(value))return Number(value.replace('%',''))/100; return Number(value); }
function normalizeExpr(text) { return String(text).replace(/^\s*=\s*/, '').replace(/\b(changed|previous)\s*\(\s*state\.([A-Za-z_$][\w$]*)\s*\)/g,"$1('$2')").replace(/(?<![\w.])(\d+(?:\.\d+)?)%/g,'($1/100)').replace(/\band\b/g,'&&').replace(/\bor\b/g,'||').replace(/\bnot\b/g,'!').replace(/\btrue\b/g,'true').replace(/\bfalse\b/g,'false').replace(/([\w.]+)\s+in\s+(\[[^\]]*\])/g,'$2.includes($1)'); }
function evaluate(expression, ctx) {
  if(typeof expression!=='string')return expression; const code=normalizeExpr(expression); if(/[;`{}]|\b(?:window|document|globalThis|Function|constructor|prototype|__proto__)\b/.test(code))throw Error('허용되지 않은 표현식입니다.');
  const allowed=new Set(['state','variables','parameters','portfolio','changed','previous','target_deviation','abs','min','max','sum','count','all','any','round','clamp','true','false','null',...Object.keys(ctx.market)]);
  const inspected=code.replace(/'(?:\\.|[^'])*'|"(?:\\.|[^"])*"/g,"''");
  for(const match of inspected.matchAll(/\b[A-Za-z_$][\w$]*\b/g)){const word=match[0], before=inspected[match.index-1]; if(before!=='.'&&!allowed.has(word)&&!['includes'].includes(word))throw Error('알 수 없는 표현식 이름: '+word);}
  const names=Object.keys(ctx.market).filter(name=>/^[A-Za-z_$][\w$]*$/.test(name)),values=names.map(name=>ctx.market[name]);
  const helpers={abs:Math.abs,min:Math.min,max:Math.max,sum:(...v)=>v.reduce((a,b)=>a+b,0),count:(...v)=>v.filter(Boolean).length,all:(...v)=>v.every(Boolean),any:(...v)=>v.some(Boolean),round:(v,d=0)=>Number(v.toFixed(d)),clamp:(v,l,h)=>Math.max(l,Math.min(h,v))};
  return Function(...names,'state','variables','parameters','portfolio','changed','previous','target_deviation',...Object.keys(helpers),'"use strict";return ('+code+')')(...values,ctx.state,ctx.variables,ctx.parameters,ctx.portfolio,ctx.changed,ctx.previous,ctx.targetDeviation,...Object.values(helpers));
}
function period(date, check='daily') { if(check==='daily')return date; if(check==='monthly')return date.slice(0,7); if(check==='quarterly')return date.slice(0,4)+'Q'+(Math.floor((Number(date.slice(5,7))-1)/3)+1); const day=new Date(date+'T00:00:00Z'), start=new Date(Date.UTC(day.getUTCFullYear(),0,1)); return day.getUTCFullYear()+'W'+Math.floor((day-start)/604800000); }
function recordsFor(tickers, data) { const byTicker={}; for(const ticker of tickers){if(!data[ticker])throw Error('내장 데이터에 '+ticker+'가 없습니다.'); byTicker[ticker]=new Map(data[ticker].map(row=>[row.Date,row]));} const dates=[...byTicker[tickers[0]].keys()].filter(date=>tickers.every(t=>byTicker[t].has(date))); return dates.map(date=>({date,market:Object.fromEntries(tickers.map(t=>[t,marketRow(byTicker[t].get(date))]))})); }
function marketRow(row) { const out={}; for(const [key,value] of Object.entries(row)){if(key!=='Date')out[key.toLowerCase()]=value===''?NaN:Number(value);} return out; }
function expressionStrings(value,out=[]) { if(typeof value==='string')out.push(value); else if(Array.isArray(value))value.forEach(item=>expressionStrings(item,out)); else if(value&&typeof value==='object')Object.values(value).forEach(item=>expressionStrings(item,out)); return out; }
function requiredMarketFields(def) { const fields={}; for(const text of expressionStrings([def.variables,def.state,def.target,def.rebalance,def.execution]))for(const match of text.matchAll(/\b([A-Z][A-Z0-9_=X-]*)\.([A-Za-z_][\w]*)/g)){const ticker=match[1],field=match[2].toLowerCase();(fields[ticker]??=new Set()).add(field);} return fields; }
class Portfolio { constructor(commission=.00015,slippage=.0002){this.cash=1;this.positions={};this.commission=commission;this.slippage=slippage;this.costs=0;this.pending=null;this.remaining=0;this.history=[];this.rebalances=[];this.activeRebalance=null;} value(prices){return this.cash+Object.entries(this.positions).reduce((v,[t,s])=>v+s*prices[t],0);} weights(prices){const total=this.value(prices),out={};for(const t of Object.keys(prices))out[t]=(this.positions[t]||0)*prices[t]/total;return out;} sameTarget(target,tolerance=1e-6){if(!this.pending)return false;const tickers=new Set([...Object.keys(this.pending),...Object.keys(target)]);return[...tickers].every(t=>Math.abs((this.pending[t]||0)-(target[t]||0))<=tolerance);} trade(ticker,shares,price,date){if(Math.abs(shares)<1e-8)return;const direction=shares>0?1:-1, execution=price*(1+direction*this.slippage);if(shares>0)shares=Math.min(shares,Math.max(this.cash,0)/(execution*(1+this.commission)));if(Math.abs(shares)<1e-8)return;const notional=shares*execution,fee=Math.abs(notional)*this.commission;this.cash-=notional+fee;this.costs+=fee+Math.abs(shares)*Math.abs(execution-price);this.positions[ticker]=(this.positions[ticker]||0)+shares;} rebalance(prices,target,date){const total=this.value(prices);for(const [t,w]of Object.entries(target)){const diff=total*w-(this.positions[t]||0)*prices[t];if(diff<0)this.trade(t,-diff/prices[t]*-1,prices[t],date);}for(const [t,w]of Object.entries(target)){const diff=total*w-(this.positions[t]||0)*prices[t];if(diff>0)this.trade(t,diff/prices[t],prices[t],date);}} start(target,days,date,reason){if(this.sameTarget(target))return false;this.pending={...target};this.remaining=days;this.activeRebalance={Date:date,Target:{...target},ExecutionDays:days,Reason:reason};this.rebalances.push(this.activeRebalance);return true;} update(prices,date){if(!this.pending)return;const current=this.weights(prices);if(this.activeRebalance&&!this.activeRebalance.PreWeights){this.activeRebalance.ExecutionDate=date;this.activeRebalance.PreWeights={...current};}this.rebalance(prices,Object.fromEntries(Object.entries(this.pending).map(([t,w])=>[t,current[t]+(w-current[t])/this.remaining])),date);if(--this.remaining===0){this.pending=null;this.activeRebalance=null;}} record(date,prices,state){this.history.push({date,value:this.value(prices),weights:this.weights(prices),state,costs:this.costs});} }
class Declarative { constructor(def){this.def=def;this.state={};this.previous={};this.changed=new Set;this.candidates={};this.lastState={};this.lastRebalance={};for(const [k,v]of Object.entries(def.state||{}))this.state[k]=typeof v.initial==='string'&&v.initial.endsWith('%')?pct(v.initial):v.initial;this.first=true;} context(market,portfolio,target){const weights=portfolio.weights(Object.fromEntries(Object.entries(market).map(([t,r])=>[t,r.close])));return {market,state:this.state,variables:this.variables||{},parameters:this.def.parameters||{},portfolio:{weight:weights},changed:(x)=>this.changed.has(stateName(x)),previous:(x)=>this.previous[stateName(x)],targetDeviation:()=>Math.max(...Object.entries(target||{}).map(([t,w])=>Math.abs((weights[t]||0)-w)),0)};} step(date,market,portfolio){this.variables={};let ctx=this.context(market,portfolio);for(const [k,v]of Object.entries(this.def.variables||{}))this.variables[k]=evaluate(v,ctx);this.previous={...this.state};this.changed=new Set();for(const [name,config]of Object.entries(this.def.state||{})){const p=period(date,config.check);if(this.lastState[name]===p)continue;this.lastState[name]=p;ctx=this.context(market,portfolio);let selected=(config.rules||[]).find(rule=>rule.otherwise||evaluate(rule.when,ctx));if(!selected){delete this.candidates[name];continue;}let desired=selected.set;desired=typeof desired==='string'&&desired.startsWith('=')?evaluate(desired,ctx):(typeof desired==='string'&&desired.endsWith('%')?pct(desired):desired);if(desired===this.state[name]){delete this.candidates[name];continue;}const candidate=this.candidates[name],days=candidate&&candidate.value===desired?candidate.days+1:1;if(days>=Number(selected.confirm||1)){this.state[name]=desired;this.changed.add(name);delete this.candidates[name];}else this.candidates[name]={value:desired,days};}
      ctx=this.context(market,portfolio);let weights;for(const rule of this.def.target){if(!rule.when||evaluate(rule.when,ctx)){weights={};for(const [t,v]of Object.entries(rule.weights))weights[t]=typeof v==='string'&&/^\s*\d+(\.\d+)?%\s*$/.test(v)?pct(v):Number(evaluate(v,ctx));break;}}if(!weights)throw Error('목표 비중 규칙이 없습니다.');ctx=this.context(market,portfolio,weights);let rebalance=false,days;for(let i=0;i<(this.def.rebalance||[]).length;i++){const rule=this.def.rebalance[i],p=period(date,rule.check);const prior=this.lastRebalance[i];this.lastRebalance[i]=p;if(this.first||p===prior)continue;if(evaluate(rule.when,ctx)){rebalance=true;days=Number(rule.days===undefined?1:evaluate(rule.days,ctx));break;}}this.first=false;return {target:weights,rebalance,days:days||Number(evaluate((this.def.execution||{}).days||1,ctx)),state:this.state.market_mode||this.state.defense_mode||''};} }
function rotationNumber(value){const number=Number(value);return Number.isFinite(number)?number:null;}
function sameRotationMix(left,right){const keys=new Set([...Object.keys(left||{}),...Object.keys(right||{})]);return[...keys].every(key=>Math.abs((left?.[key]||0)-(right?.[key]||0))<=1e-12);}
function selectRotation(rotation,market){const sleeve=String(rotation.sleeve),cash=market[sleeve]||{},cashValues=Object.fromEntries(['roc60','roc120','roc252'].map(field=>[field,rotationNumber(cash[field])])),details={};if(Object.values(cashValues).some(value=>value===null))return{mix:{[sleeve]:1},selected:[],details};const winners=new Map();for(const candidate of rotation.candidates||[]){const ticker=String(candidate.ticker),row=market[ticker]||{},values=Object.fromEntries(['close','ema200','roc60','roc120','roc252','vol60'].map(field=>[field,rotationNumber(row[field])])),reasons=[];if(Object.values(values).some(value=>value===null))reasons.push('필수 추세 지표 부족');else if(values.close<=values.ema200)reasons.push('가격이 EMA200 아래');else if(values.roc60<=cashValues.roc60)reasons.push('3개월 수익률이 현금 슬리브 이하');else if(values.vol60<=0)reasons.push('변동성 계산 불가');if(reasons.length){details[ticker]={eligible:false,reasons};continue;}const score=.5*(values.roc60-cashValues.roc60)+.3*(values.roc120-cashValues.roc120)+.2*(values.roc252-cashValues.roc252);details[ticker]={eligible:true,score,volatility:values.vol60,roc60_excess:values.roc60-cashValues.roc60,roc120_excess:values.roc120-cashValues.roc120,roc252_excess:values.roc252-cashValues.roc252,reasons:['EMA200 상단','3개월 수익률 현금 초과']};const prior=winners.get(String(candidate.group));if(!prior||score>prior.score)winners.set(String(candidate.group),{...candidate,score});}const selectedConfig=[...winners.values()].sort((left,right)=>right.score-left.score).slice(0,Number(rotation.top_n??2)),selectedTickers=new Set(selectedConfig.map(candidate=>String(candidate.ticker))),winnerTickers=new Set([...winners.values()].map(candidate=>String(candidate.ticker)));for(const[ticker,detail]of Object.entries(details))if(detail.eligible)detail.selection_status=selectedTickers.has(ticker)?'선택':winnerTickers.has(ticker)?'상위 선택 수 밖':'같은 그룹 내 점수 열위';if(!selectedConfig.length)return{mix:{[sleeve]:1},selected:[],details};const inverse=Object.fromEntries(selectedConfig.map(candidate=>[candidate.ticker,1/details[candidate.ticker].volatility])),total=Object.values(inverse).reduce((sum,value)=>sum+value,0),maxSingle=pct(rotation.max_single_sleeve_share??.5),maxGold=pct(rotation.max_gold_sleeve_share??.3),maxEquity=pct(rotation.max_equity_sleeve_share??.3),mix={};let allocated=0,equity=0;for(const candidate of selectedConfig){const ticker=String(candidate.ticker),assetClass=String(candidate.asset_class),capacity=assetClass==='GOLD'?Math.min(maxSingle,maxGold):assetClass==='EQUITY'?Math.min(maxSingle,Math.max(0,maxEquity-equity)):maxSingle,share=Math.min(inverse[ticker]/total,capacity);if(share<=1e-12)continue;mix[ticker]=share;allocated+=share;if(assetClass==='EQUITY')equity+=share;}mix[sleeve]=Math.max(0,1-allocated);return{mix,selected:selectedConfig.map(candidate=>String(candidate.ticker)).filter(ticker=>ticker in mix),details};}
function rotationExplanation(sleeve,previous,selected,mix,details){if(!selected.length)return `자동 자산 검토: 적격 후보 없음 — ${sleeve} 유지 (EMA200 상단 및 3개월 현금 초과 조건 필요)`;const weights=selected.map(ticker=>`${ticker} ${(mix[ticker]*100).toFixed(1)}%`).join(', '),scores=selected.map(ticker=>`${ticker} 점수 ${details[ticker].score.toFixed(2)}`).join(', ');return `자동 자산 교체: ${(previous.length?previous:[sleeve]).join(', ')} → ${selected.join(', ')}; 배분=${weights}; 근거=EMA200 상단·3개월 현금 초과·복합 모멘텀 (${scores})`;}
function installRotation(StrategyClass){const baseStep=StrategyClass.prototype.step;if(baseStep.__rotationInstalled)return StrategyClass;function step(date,market,portfolio){const signal=baseStep.call(this,date,market,portfolio),rotation=this.def.rotation;if(!rotation)return signal;this.rotationMix??={};this.rotationSelected??=[];this.rotationLastPeriod??=null;this.rotationActive??=false;const sleeve=String(rotation.sleeve),sleeveWeight=Number(signal.target[sleeve]||0),active=sleeveWeight>1e-12;let rebalance=signal.rebalance,reason=signal.reason||null,target=signal.target;if(active){const currentPeriod=period(date,rotation.check||'monthly');if(!this.rotationActive||currentPeriod!==this.rotationLastPeriod){const previous=[...this.rotationSelected],selection=selectRotation(rotation,market),changed=!sameRotationMix(selection.mix,this.rotationMix),explanation=rotationExplanation(sleeve,previous,selection.selected,selection.mix,selection.details);this.rotationMix=selection.mix;this.rotationSelected=selection.selected;this.rotationLastPeriod=currentPeriod;this.rotationActive=true;this.rotationDecision={date,reviewed:true,sleeve,sleeve_weight:sleeveWeight,previous_selected:previous,selected:selection.selected,mix:{...selection.mix},candidates:selection.details,explanation};if(changed){rebalance=true;reason=reason?`${reason} | ${explanation}`:explanation;}}target={...target,[sleeve]:sleeveWeight*(this.rotationMix[sleeve]||0)};for(const candidate of rotation.candidates||[]){const ticker=String(candidate.ticker);target[ticker]=sleeveWeight*(this.rotationMix[ticker]||0);}}else if(this.rotationActive){const previous=[...this.rotationSelected],changed=Object.entries(this.rotationMix).some(([ticker,weight])=>ticker!==sleeve&&weight>1e-12);this.rotationActive=false;this.rotationLastPeriod=null;this.rotationMix={[sleeve]:1};this.rotationSelected=[];const explanation=`자동 자산 교체 종료: 전략 기본 목표에 따라 ${sleeve} 슬리브가 0%`;this.rotationDecision={date,reviewed:false,sleeve,sleeve_weight:sleeveWeight,previous_selected:previous,selected:[],mix:{[sleeve]:1},candidates:{},explanation};if(changed){rebalance=true;reason=reason?`${reason} | ${explanation}`:explanation;}}if(!active){target={...target};for(const candidate of rotation.candidates||[])target[String(candidate.ticker)]=0;}return{...signal,target,rebalance,reason};}step.__rotationInstalled=true;StrategyClass.prototype.step=step;return StrategyClass;}
function installRotationStability(StrategyClass){const baseStep=StrategyClass.prototype.step;if(baseStep.__rotationStabilityInstalled)return StrategyClass;function step(date,market,portfolio){const rotation=this.def.rotation,previousSelected=[...(this.rotationSelected||[])],previousMix={...(this.rotationMix||{})},previousHold=Number(this.rotationHoldPeriods||0),signal=baseStep.call(this,date,market,portfolio),decision=this.rotationDecision;if(!rotation||!decision?.reviewed||decision.date!==date){if(rotation&&!this.rotationActive)this.rotationHoldPeriods=0;return signal;}let selected=[...decision.selected],mix={...decision.mix};const sameAssets=selected.length===previousSelected.length&&selected.every(ticker=>previousSelected.includes(ticker));if(sameAssets)selected=[...previousSelected];let selectionChanged=selected.length!==previousSelected.length||selected.some(ticker=>!previousSelected.includes(ticker));const allIncumbentsEligible=previousSelected.length>0&&previousSelected.every(ticker=>decision.candidates?.[ticker]?.eligible);if(selectionChanged&&allIncumbentsEligible){const minimumHold=Number(rotation.minimum_hold_periods||0),margin=pct(rotation.switch_score_margin||0),entrants=selected.filter(ticker=>!previousSelected.includes(ticker)),departures=previousSelected.filter(ticker=>!selected.includes(ticker)),bestEntrant=Math.max(...entrants.map(ticker=>Number(decision.candidates?.[ticker]?.score??-Infinity))),weakestDeparture=Math.min(...departures.map(ticker=>Number(decision.candidates?.[ticker]?.score??Infinity)));if(previousHold<minimumHold||bestEntrant-weakestDeparture<=margin){selected=[...previousSelected];mix={...previousMix};selectionChanged=false;}}const minimumWeightChange=pct(rotation.minimum_weight_change||0),tickers=[...new Set([...Object.keys(previousMix),...Object.keys(mix)])],weightChange=Math.max(...tickers.map(ticker=>Math.abs(Number(mix[ticker]||0)-Number(previousMix[ticker]||0))),0);let changed=selectionChanged||weightChange>Math.max(minimumWeightChange,1e-12);if(!selectionChanged&&!changed&&Object.keys(previousMix).length)mix={...previousMix};this.rotationSelected=selected;this.rotationMix=mix;this.rotationHoldPeriods=!selectionChanged&&previousSelected.length?previousHold+1:selected.length?1:0;const sleeve=String(rotation.sleeve),sleeveWeight=Number(decision.sleeve_weight||0),explanation=changed?rotationExplanation(sleeve,previousSelected,selected,mix,decision.candidates):`자동 자산 교체 검토: 변경 폭이 기준(${(minimumWeightChange*100).toFixed(1)}%p) 이하여서 기존 구성 유지`;this.rotationDecision={...decision,selected:[...selected],mix:{...mix},explanation};const target={...signal.target,[sleeve]:sleeveWeight*(mix[sleeve]||0)};for(const candidate of rotation.candidates||[])target[String(candidate.ticker)]=sleeveWeight*(mix[String(candidate.ticker)]||0);const declarative=String(signal.reason||'').split(/\s*\|\s*/).find(part=>/^DECLARATIVE_RULE_\d+$/.test(part))||null,reason=[declarative,changed?explanation:null].filter(Boolean).join(' | ')||null;return{...signal,target,rebalance:Boolean(declarative)||changed,reason};}step.__rotationStabilityInstalled=true;StrategyClass.prototype.step=step;const baseSnapshot=StrategyClass.prototype.snapshot,baseRestore=StrategyClass.prototype.restore;if(baseSnapshot)StrategyClass.prototype.snapshot=function(){return{...baseSnapshot.call(this),rotationHoldPeriods:Number(this.rotationHoldPeriods||0)};};if(baseRestore)StrategyClass.prototype.restore=function(snapshot){baseRestore.call(this,snapshot);this.rotationHoldPeriods=Number(snapshot?.rotationHoldPeriods||0);};return StrategyClass;}
function installRotationSuspension(StrategyClass){const baseStep=StrategyClass.prototype.step;if(baseStep.__rotationSuspensionInstalled)return StrategyClass;function step(date,market,portfolio){const signal=baseStep.call(this,date,market,portfolio),rotation=this.def.rotation,expression=rotation?.suspend_when;if(expression===undefined||!evaluate(expression,this.context(market,portfolio,signal.target)))return signal;const sleeve=String(rotation.sleeve),target={...signal.target},previous=[...(this.rotationSelected||[])];let sleeveWeight=Number(target[sleeve]||0),changed=false;for(const candidate of rotation.candidates||[]){const ticker=String(candidate.ticker),weight=Number(target[ticker]||0);sleeveWeight+=weight;changed=changed||weight>1e-12;target[ticker]=0;}target[sleeve]=sleeveWeight;this.rotationMix={[sleeve]:1};this.rotationSelected=[];this.rotationLastPeriod=period(date,rotation.check||'monthly');this.rotationActive=true;this.rotationHoldPeriods=0;const explanation=`자동 자산 교체 일시 중단: ${sleeve} 유지`,baseReason=String(signal.reason||'').split(/\s*\|\s*/)[0]||null,reason=[baseReason,changed?explanation:null].filter(Boolean).join(' | ')||null;this.rotationDecision={date,reviewed:false,suspended:true,sleeve,sleeve_weight:sleeveWeight,previous_selected:previous,selected:[],mix:{[sleeve]:1},candidates:{},explanation};return{...signal,target,rebalance:signal.rebalance||changed,reason};}step.__rotationSuspensionInstalled=true;StrategyClass.prototype.step=step;return StrategyClass;}
function installFinalTargetRebalance(StrategyClass){const baseStep=StrategyClass.prototype.step;if(baseStep.__finalTargetRebalanceInstalled)return StrategyClass;function step(date,market,portfolio){const signal=baseStep.call(this,date,market,portfolio),match=String(signal.reason||'').match(/^DECLARATIVE_RULE_(\d+)(?:\s*\|\s*(.*))?$/);if(match){const rule=this.def.rebalance?.[Number(match[1])-1];if(!rule||evaluate(rule.when,this.context(market,portfolio,signal.target)))return signal;const remainingReason=match[2]||null;return{...signal,rebalance:Boolean(remainingReason),reason:remainingReason};}if(!this.def.rotation||!signal.rebalance||signal.reason)return signal;const finalContext=this.context(market,portfolio,signal.target),stillRequired=(this.def.rebalance||[]).some(rule=>evaluate(rule.when,finalContext));return stillRequired?signal:{...signal,rebalance:false};}step.__finalTargetRebalanceInstalled=true;StrategyClass.prototype.step=step;return StrategyClass;}
installRotation(Declarative);
installRotationStability(Declarative);
installRotationSuspension(Declarative);
installFinalTargetRebalance(Declarative);
function applyRotationRiskCap(def,target){
  const rotation=def.rotation,riskAssets=new Set(def.assets?.risk||[]),cap=.70;
  if(!rotation||!riskAssets.size)return {target,capped:false};
  const riskWeight=[...riskAssets].reduce((sum,ticker)=>sum+Number(target[ticker]||0),0);
  if(riskWeight<=cap+1e-12)return {target,capped:false};
  const rotationRiskAssets=(rotation.candidates||[]).map(candidate=>String(candidate.ticker)).filter(ticker=>riskAssets.has(ticker));
  const reducible=rotationRiskAssets.reduce((sum,ticker)=>sum+Number(target[ticker]||0),0),excess=riskWeight-cap;
  if(reducible+1e-12<excess)throw Error('기본 목표 위험자산이 로테이션 위험 한도 70%를 초과합니다.');
  const adjusted={...target},scale=Math.max(0,(reducible-excess)/reducible);
  for(const ticker of rotationRiskAssets)adjusted[ticker]=Number(adjusted[ticker]||0)*scale;
  adjusted[String(rotation.sleeve)]=Number(adjusted[String(rotation.sleeve)]||0)+excess;
  return {target:adjusted,capped:true};
}
function installRotationRiskCap(StrategyClass){
  const baseStep=StrategyClass.prototype.step;
  function step(date,market,portfolio){
    const signal=baseStep.call(this,date,market,portfolio),result=applyRotationRiskCap(this.def,signal.target);
    const riskAssets=this.def.assets?.risk||[];
    if(!result.capped)return signal;
    const riskWeight=riskAssets.reduce((sum,ticker)=>sum+Number(result.target[ticker]||0),0);
    if(this.rotationDecision)this.rotationDecision={...this.rotationDecision,risk_cap:.70,risk_weight:riskWeight};
    const notes=[];
    if(result.capped)notes.push('위험자산 합계가 70%를 넘지 않도록 초과분을 안전 슬리브에 유지');
    return {...signal,target:result.target,reason:[signal.reason,...notes].filter(Boolean).join(' | ')};
  }
  StrategyClass.prototype.step=step;
}
installRotationRiskCap(Declarative);
function stateName(value){if(typeof value==='string')return value.split('.').at(-1);return ''}
function mapProductTarget(sourceTarget,def,source,actual){const target={},riskAssets=new Set(source.assets?.risk||[]),riskCap=Number(source.parameters?.canonical_risk_weight??.70);for(const [asset,weight]of Object.entries(sourceTarget)){const products=def.products?.[asset]||{[asset]:1},items=Object.entries(products),currentWeight=items.reduce((sum,[product])=>sum+(actual[product]||0),0),preserveMix=items.length>1&&riskAssets.has(asset)&&currentWeight>riskCap+1e-8&&weight>riskCap+1e-8;let allocated=0;for(const [index,[product,configuredShare]]of items.entries()){const share=preserveMix?(actual[product]||0)/currentWeight:pct(configuredShare),productWeight=index===items.length-1?weight-allocated:weight*share;target[product]=(target[product]||0)+productWeight;allocated+=productWeight;}}return target;}
function resolve(def, all){if(!def.source)return new Declarative(def);const source=all[def.source];if(!source)throw Error('source 전략을 찾을 수 없습니다: '+def.source);const runtime=new Declarative(source);return {step(date,market,portfolio){const prices=Object.fromEntries(Object.entries(market).map(([t,r])=>[t,r.close])),actual=portfolio.weights(prices),virtual={weights:()=>Object.fromEntries((source.assets.required||[]).map(asset=>[asset,Object.entries(def.products[asset]||{[asset]:1}).reduce((s,[p])=>s+(actual[p]||0),0)]))};const signal=runtime.step(date,market,virtual),target=mapProductTarget(signal.target,def,source,actual);return {...signal,target};}};}
function runWithData(def,data,availableDefinitions=definitions){const all=Object.fromEntries(availableDefinitions.map(d=>[d.strategy.id,d]));all[def.strategy.id]=def;const source=def.source?all[def.source]:def,tickers=def.source?[...new Set([...(source.assets.required||[]),...Object.values(def.products).flatMap(Object.keys)])]:def.assets.required,required=requiredMarketFields(source);const rows=recordsFor(tickers,data).filter(row=>tickers.every(t=>[...(required[t]||[])].every(field=>Number.isFinite(row.market[t][field])))),runtime=resolve(def,all),portfolio=new Portfolio();for(const row of rows){const opens=Object.fromEntries(Object.entries(row.market).map(([t,v])=>[t,v.open]));portfolio.update(opens,row.date);const signal=runtime.step(row.date,row.market,portfolio);if(portfolio.history.length===0)portfolio.start(signal.target,signal.days,row.date,'INITIAL');else if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,'RULE');const closes=Object.fromEntries(Object.entries(row.market).map(([t,v])=>[t,v.close]));portfolio.record(row.date,closes,signal.state);}const eventsByDate=new Map(portfolio.rebalances.map(event=>[event.ExecutionDate||event.Date,event]));return portfolio.history.map(row=>{const event=eventsByDate.get(row.date);return event?{...row,target:event.Target,preWeights:event.PreWeights||null,executionDays:event.ExecutionDays}:row;});}
async function run(def){return runWithData(def,await downloadMissingData(def));}
function metrics(history){if(!history.length)return{total:0,cagr:0,mdd:0};const values=history.map(row=>Number(row.value)),invalid=values.findIndex(value=>!Number.isFinite(value));if(invalid>=0)throw Error(`성과 계산 값이 유효하지 않습니다: ${history[invalid].date}`);const first=values[0],last=values.at(-1),years=(new Date(history.at(-1).date)-new Date(history[0].date))/31557600000;let peak=first,mdd=0;for(const value of values){peak=Math.max(peak,value);mdd=Math.min(mdd,value/peak-1);}return{total:last/first-1,cagr:years?Math.pow(last/first,1/years)-1:0,mdd};}
function draw(history){const canvas=$('chart'),c=canvas.getContext('2d'),w=canvas.width=canvas.clientWidth*devicePixelRatio,h=canvas.height=340*devicePixelRatio;c.scale(devicePixelRatio,devicePixelRatio);const width=canvas.clientWidth,height=340;c.clearRect(0,0,width,height);const values=history.map(x=>x.value/history[0].value-1),min=Math.min(...values,0),max=Math.max(...values,0),range=max-min||1;c.strokeStyle='#2563eb';c.lineWidth=2;c.beginPath();values.forEach((v,i)=>{const x=i/(values.length-1)*width,y=height-20-(v-min)/range*(height-40);i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
function renderList(){const selected=$('strategies');if(!selected)return;selected.innerHTML='';definitions.filter(d=>d.strategy.enabled!==false).forEach((d,i)=>{const option=document.createElement('option');option.value=String(i);option.textContent=d.strategy.name;selected.append(option);});}
$('import').onchange=async(e)=>{try{const text=await e.target.files[0].text(),definition=parseYaml(text);if(!definition?.strategy?.id)throw Error('strategy.id가 필요합니다.');definitions=definitions.filter(d=>d.strategy.id!==definition.strategy.id);definitions.push(definition);renderList();$('status').textContent='YAML 전략을 불러왔습니다. 목록에서 선택하면 자동으로 준비됩니다.';}catch(error){$('status').textContent='YAML 오류: '+error.message;}};
globalThis.OfflineStrategyRuntime={parseYaml,evaluate,normalizeExpr,runWithData,metrics,Portfolio,Declarative};
function fmtPercent(value){return `${(value*100).toFixed(2)}%`;}
function localizeDashboard(){document.documentElement.lang='ko';document.querySelector('.research-brand-caption').textContent='퀀트 리서치';document.querySelector('.research-eyebrow').textContent='백테스트 대시보드';document.querySelector('.research-title').textContent='투자 전략 리서치';document.querySelector('.research-subtitle').textContent='전략 성과를 비교하고 종목별 지표를 분석합니다.';document.querySelector('.research-status').textContent='● 준비 완료';$('analysis-tab').textContent='성과 분석';$('indicators-tab').textContent='지표 연구';document.querySelector('#analysis-view .form-label').textContent='분석 기간';$('strategy-list').previousElementSibling.textContent='비교 전략';document.querySelector('label.btn').childNodes[0].textContent='YAML 가져오기';$('indicator-type').innerHTML='<option value="price">가격 · 추세</option><option value="oscillator">오실레이터</option><option value="risk">리스크</option>';document.querySelectorAll('.card-title').forEach((node,index)=>{const labels=['누적 수익률','낙폭 경로','전략 상세','성과 요약','지표 연구'];if(labels[index])node.textContent=labels[index];});const descriptions=[['performance-plot','동일 시작점으로 정규화한 전략별 성과'],['drawdown-plot','고점 대비 손실과 회복 구간 비교'],['detail-plot','자산 비중과 실제 리밸런싱 시점을 확인합니다.'],['summary','열을 정렬하거나 너비를 조절해 전략을 비교할 수 있습니다.'],['indicator-plot','종목과 지표를 조합해 시장 구간을 분석합니다.']];for(const [id,label] of descriptions){const card=$(id).closest('.research-card');card?.querySelector('.research-card-description')&&(card.querySelector('.research-card-description').textContent=label);}}
const graphConfig={responsive:true,displaylogo:false,scrollZoom:true,doubleClick:'reset',showAxisDragHandles:false,showAxisRangeEntryBoxes:false,modeBarButtonsToRemove:['lasso2d','select2d','zoom2d','zoomIn2d','zoomOut2d','autoScale2d','pan2d']};
function chartLayout(title,{slider=false,selector=true,detail=false,indicator=false}={}){const buttons=[{count:1,label:'1년',step:'year',stepmode:'backward'},{count:3,label:'3년',step:'year',stepmode:'backward'},{count:5,label:'5년',step:'year',stepmode:'backward'},{label:'전체',step:'all'}],top=selector?(indicator?154:detail?96:210):48,selectorPosition=indicator?{x:0,xanchor:'left',y:1.40}:detail?{x:1,xanchor:'right',y:1.18}:{x:0,xanchor:'left',y:1.65};return {dragmode:'pan',margin:{l:58,r:52,t:top,b:slider?78:54},paper_bgcolor:'transparent',plot_bgcolor:'transparent',hovermode:'x unified',hoverdistance:-1,spikedistance:-1,hoverlabel:{bgcolor:'rgba(255,255,255,.96)',bordercolor:'#dce1e7',font:{color:'#182433',size:12,family:'-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif'},align:'left',namelength:-1},xaxis:{type:'date',tickformat:'%Y.%m',hoverformat:'%Y.%m.%d',gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false,rangeslider:{visible:slider,thickness:.09,bgcolor:'rgba(106,109,120,.06)',bordercolor:'#dce1e7',borderwidth:1},rangeselector:selector?{buttons,...selectorPosition,yanchor:'bottom',bgcolor:'rgba(106,109,120,.08)',activecolor:'rgba(41,98,255,.18)',bordercolor:'#dce1e7',borderwidth:1,font:{size:11}}:undefined},yaxis:{gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false,tickformat:'.0f',ticksuffix:'%',title:{text:title==='낙폭 경로'?'낙폭 (%)':'수익률 (%)',standoff:10}},legend:{orientation:'h',y:1.02,x:0,yanchor:'bottom',entrywidth:indicator ? .2 : .5,entrywidthmode:'fraction',font:{size:12}},font:{family:'-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif',color:'#182433',size:13}};}
function bindChartTooltip(plotId,kind){const plot=$(plotId);if(!plot||plot.dataset.tooltipBound)return;plot.dataset.tooltipBound='1';let card;const hide=()=>{if(card)card.style.display='none';};plot.on('plotly_unhover',hide);plot.on('plotly_hover',event=>{const points=event.points||[];if(!points.length)return;const date=String(points[0].x||'').slice(0,10).replaceAll('-','.');const rows=points.filter(point=>point.data?.hoverinfo!=='skip').map(point=>{const value=typeof point.y==='number'?`${point.y.toFixed(2)}%`:'';return `<div class="research-custom-tooltip-row"><span class="research-custom-tooltip-label">${point.data?.name||''}</span><strong class="research-custom-tooltip-value">${value}</strong></div>`;});const returnPoint=points.find(point=>point.data?.name==='누적 수익률')||points[0],extra=returnPoint.customdata&&kind==='detail'?`<div class="research-custom-tooltip-separator"></div><div class="research-custom-tooltip-extra">${returnPoint.customdata}</div>`:'';if(!rows.length&&!extra)return;if(!card){card=document.createElement('div');card.className='offline-chart-tooltip';document.body.append(card);}card.innerHTML=`<div class="research-custom-tooltip-card"><div class="research-custom-tooltip-date">${date}</div><div class="research-custom-tooltip-grid">${rows.join('')}</div>${extra}</div>`;card.style.display='block';const rect=plot.getBoundingClientRect(),bbox=points[0].bbox||{},x=rect.left+(bbox.x0||rect.width/2),y=rect.top+(bbox.y0||rect.height/2),above=y-rect.top>rect.height-(y-rect.top);card.style.left=`${Math.max(12,Math.min(window.innerWidth-card.offsetWidth-12,x-card.offsetWidth/2))}px`;card.style.top=`${above?Math.max(12,y-card.offsetHeight-16):Math.min(window.innerHeight-card.offsetHeight-12,y+20)}px`;});}
function selectedDefinitions(){const ids=[...document.querySelectorAll('#strategy-list input:checked')].map(input=>input.value);return definitions.filter(def=>ids.includes(def.strategy.id));}
function visibleHistory(history){const start=$('start-date').value,end=$('end-date').value;return history.filter(row=>(!start||row.date>=start)&&(!end||row.date<=end));}
function formatTransactionCost(value){return Number.isFinite(value)?Number(value).toFixed(6):'-';}
function setRangeYears(plotId,dates){const plot=$(plotId),parent=plot?.parentElement;if(!plot||!parent||!dates?.length)return;let labels=parent.querySelector(`.offline-range-years[data-for="${plotId}"]`);if(!labels){labels=document.createElement('div');labels.className='offline-range-years';labels.dataset.for=plotId;plot.after(labels);}const first=new Date(dates[0]).getFullYear(),last=new Date(dates.at(-1)).getFullYear(),step=Math.max(1,Math.ceil((last-first)/6)),years=[];for(let year=first;year<=last;year+=step)years.push(year);if(years.at(-1)!==last)years.push(last);labels.innerHTML=years.map(year=>`<span>${year}</span>`).join('');}
function drawSummary(entries){const rows=entries.map(([def,allHistory])=>{const history=visibleHistory(allHistory),m=metrics(history),daily=history.slice(1).map((row,index)=>row.value/history[index].value-1),mean=daily.reduce((sum,value)=>sum+value,0)/Math.max(daily.length,1),variance=daily.reduce((sum,value)=>sum+(value-mean)**2,0)/Math.max(daily.length-1,1),vol=Math.sqrt(variance)*Math.sqrt(252),downside=Math.sqrt(daily.filter(value=>value<0).reduce((sum,value)=>sum+value**2,0)/Math.max(daily.filter(value=>value<0).length,1))*Math.sqrt(252),sharpe=vol?m.cagr/vol:0,sortino=downside?m.cagr/downside:0,calmar=m.mdd?m.cagr/Math.abs(m.mdd):0,start=history[0]?.date||'',end=history.at(-1)?.date||'',costs=Math.max(0,Number(history.at(-1)?.costs||0)-Number(history[0]?.costs||0));return `<tr><td>${def.strategy.name}</td><td>${fmtPercent(m.cagr)}</td><td>${fmtPercent(m.mdd)}</td><td>${fmtPercent(vol)}</td><td>${sharpe.toFixed(2)}</td><td>${sortino.toFixed(2)}</td><td>${calmar.toFixed(2)}</td><td>${formatTransactionCost(costs)}</td><td>${fmtPercent(m.total)}</td><td>${start}</td><td>${end}</td></tr>`;});$('summary').innerHTML=`<table><thead><tr><th>전략</th><th>CAGR</th><th>MDD</th><th>변동성</th><th>Sharpe</th><th>Sortino</th><th>Calmar</th><th title="초기자산 1 기준의 누적 거래비용">거래비용 (초기자산=1)</th><th>전체 수익률</th><th>시작일</th><th>종료일</th></tr></thead><tbody>${rows.join('')}</tbody></table>`;}
function renderDetail(){const id=$('detail-strategy').value,entry=dashboardResults.get(id);if(!entry)return;const [def,allHistory]=entry,history=visibleHistory(allHistory),m=metrics(history),daily=history.slice(1).map((row,index)=>row.value/history[index].value-1),mean=daily.reduce((sum,value)=>sum+value,0)/Math.max(daily.length,1),variance=daily.reduce((sum,value)=>sum+(value-mean)**2,0)/Math.max(daily.length-1,1),vol=Math.sqrt(variance)*Math.sqrt(252),sharpe=vol?m.cagr/vol:0,icons=['↗','↕','⚖','%'],cards=[['CAGR',fmtPercent(m.cagr),'positive'],['최대 낙폭',fmtPercent(m.mdd),'negative'],['위험조정 성과',sharpe.toFixed(2),'primary'],['전체 수익률',fmtPercent(m.total),'cyan']];$('detail-kpis').innerHTML=cards.map(([label,value,color],index)=>`<div class="card research-kpi-card"><div class="research-kpi-body"><div><div class="research-kpi-label">${label}</div><div class="research-kpi-value">${value}</div></div><span class="research-kpi-icon research-kpi-${color}">${icons[index]}</span></div></div>`).join('');const base=history[0].value,returns=history.map(row=>(row.value/base-1)*100),tickers=Object.keys(history[0].weights),detailText=row=>`상태: ${row.state||'-'}<br>종목 비중<br>${tickers.map(ticker=>`${ticker} ${((row.weights[ticker]||0)*100).toFixed(1)}%`).join('<br>')}`,traces=[];for(const ticker of tickers)traces.push({x:history.map(row=>row.date),y:history.map((row,index)=>(row.weights[ticker]||0)*returns[index]),name:ticker,stackgroup:'portfolio-return',mode:'lines',line:{width:.4},hoverinfo:'skip'});traces.push({x:history.map(row=>row.date),y:returns,name:'누적 수익률',showlegend:false,line:{color:'#182433',width:1.2},customdata:history.map(detailText),hoverinfo:'none'});const events=history.filter((row,index)=>index>0&&tickers.some(ticker=>Math.abs((row.weights[ticker]||0)-(history[index-1].weights[ticker]||0))>=0.02));if(events.length)traces.push({x:events.map(row=>row.date),y:events.map(row=>(row.value/base-1)*100),name:'리밸런싱',mode:'markers',marker:{symbol:'diamond',size:9,color:'#d63939',line:{color:'#fff',width:1}},customdata:events.map(detailText),hoverinfo:'skip'});const layout=chartLayout('누적 수익률',{slider:true,selector:true,detail:true});Plotly.react('detail-plot',traces,layout,graphConfig);bindChartTooltip('detail-plot','detail');setRangeYears('detail-plot',history.map(row=>row.date));}
function renderAnalysis(){const entries=[...dashboardResults.values()],performance=[],drawdown=[],colors=['#2962FF','#089981','#FF9800','#9C27B0','#F23645','#00BCD4'];for(const [index,[def,allHistory]] of entries.entries()){const history=visibleHistory(allHistory);if(!history.length)continue;const base=history[0].value,returns=history.map(row=>(row.value/base-1)*100),peaks=[];let peak=-Infinity;for(const value of history.map(row=>row.value)){peak=Math.max(peak,value);peaks.push((value/peak-1)*100);}performance.push({x:history.map(row=>row.date),y:returns,name:def.strategy.name,line:{width:.9},hoverinfo:'none'});drawdown.push({x:history.map(row=>row.date),y:peaks,name:def.strategy.name,line:{width:.9},hoverinfo:'none'});}Plotly.react('performance-plot',performance,chartLayout('누적 수익률'),graphConfig);Plotly.react('drawdown-plot',drawdown,chartLayout('낙폭 경로'),graphConfig);bindChartTooltip('performance-plot','comparison');bindChartTooltip('drawdown-plot','comparison');document.querySelectorAll('.offline-range-years[data-for="performance-plot"],.offline-range-years[data-for="drawdown-plot"]').forEach(node=>node.remove());drawSummary(entries);const detail=$('detail-strategy'),previous=detail.value;detail.innerHTML=entries.map(([def])=>`<option value="${def.strategy.id}">${def.strategy.name}</option>`).join('');detail.value=dashboardResults.has(previous)?previous:entries[0]?.[0].strategy.id||'';renderDetail();}
async function runDashboard(){try{$('status').textContent='선택한 전략을 실행하고 있습니다…';const selected=selectedDefinitions();if(!selected.length)throw Error('전략을 하나 이상 선택하세요.');const cached=await loadPrecomputedResults(),settled=await Promise.allSettled(selected.map(async def=>{const saved=cached[def.strategy.id]||[];if(saved.some(row=>row.target))return [def,saved];const calculated=await run(def),eventsByDate=new Map(calculated.filter(row=>row.target).map(row=>[row.date,row]));return [def,saved.length?saved.map(row=>({...row,...(eventsByDate.get(row.date)||{})})):calculated];})),completed=settled.filter(result=>result.status==='fulfilled').map(result=>result.value),failed=settled.filter(result=>result.status==='rejected');if(!completed.length)throw failed[0].reason;dashboardResults=new Map(completed.map(entry=>[entry[0].strategy.id,entry]));renderAnalysis();$('status').textContent=failed.length?`${completed.length}개 전략 완료 · ${failed.length}개 실패: ${failed.map(item=>item.reason.message).join(' | ')}`:`${completed.length}개 전략 실행을 완료했습니다.`; }catch(error){$('status').textContent=`오류: ${error.message}`;console.error(error);}}
function indicatorFields(type){return type==='oscillator'?['RSI14','MACD','MACD_SIGNAL']:type==='risk'?['DRAWDOWN120','VOL60','ATR60']:['Close','EMA20','EMA55','EMA200','MA20','MA55'];}
var indicatorSelection=new Set();
async function renderIndicators(){try{const data=await loadData(),type=$('indicator-type').value,start=$('indicator-start-date').value,end=$('indicator-end-date').value,pairs=[...indicatorSelection].map(key=>key.split('|')).filter(([,field])=>indicatorFields(type).includes(field)),palette=['#206bc4','#2fb344','#f59f00','#ae3ec9','#d63939','#17a2b8'],traces=[];for(const [index,[ticker,name]] of pairs.entries()){const rows=(data[ticker]||[]).filter(row=>(!start||row.Date>=start)&&(!end||row.Date<=end)),values=rows.map(row=>Number(row[name]??row[name.toLowerCase()]));traces.push({x:rows.map(row=>row.Date),y:values,name:`${ticker} · ${name}`,line:{width:.9},hoverinfo:'none'});}const layout=chartLayout(`지표 연구 · ${type}`,{slider:true,selector:true,detail:true});if(type==='price'){layout.yaxis.tickformat=undefined;layout.yaxis.title={text:'기준=100'};const selectedStrategy=$('indicator-strategy').value,cached=await loadPrecomputedResults(),history=(cached[selectedStrategy]||[]).filter(row=>(!start||row.date>=start)&&(!end||row.date<=end));if(history.length){const base=history[0].value,weightText=row=>Object.entries(row.weights||{}).filter(([,weight])=>weight>0.001).map(([ticker,weight])=>`${ticker} ${(weight*100).toFixed(1)}%`).join('<br>');traces.push({x:history.map(row=>row.date),y:history.map(row=>row.value/base-1),name:'전략 누적 수익률',yaxis:'y2',line:{color:'#182433',width:1},customdata:history.map(weightText),hoverinfo:'none'});const assets=Object.keys(history[0].weights||{}),events=history.filter((row,index)=>index>0&&assets.some(asset=>Math.abs((row.weights[asset]||0)-(history[index-1].weights[asset]||0))>=0.02));if(events.length&&[...$('indicator-overlays').querySelectorAll('input:checked')].some(input=>input.value==='rebalances'))traces.push({x:events.map(row=>row.date),y:events.map(row=>row.value/base-1),name:'리밸런싱',yaxis:'y2',mode:'markers',marker:{symbol:'diamond',size:8,color:'#d63939'},customdata:events.map(weightText),hoverinfo:'skip'});layout.yaxis2={title:'전략 수익률',overlaying:'y',side:'right',tickformat:'.0%',showgrid:false};}}Plotly.react('indicator-plot',traces,layout,{responsive:true,displaylogo:false});bindChartTooltip('indicator-plot','indicator');}catch(error){$('status').textContent=`오류: ${error.message}`;}}
async function setupDashboard(){const enabled=definitions.filter(def=>def.strategy.enabled!==false);$('strategy-count').textContent=`${enabled.length}개 전략`;$('strategy-list').innerHTML=enabled.map(def=>`<label><input type="checkbox" value="${def.strategy.id}" checked>${def.strategy.name}</label>`).join('');const data=await loadData(),cached=await loadPrecomputedResults(),dates=Object.values(cached).flatMap(history=>history.map(row=>row.date));$('start-date').value=dates.length?dates.sort()[0]:Object.values(data)[0][0].Date;$('end-date').value=dates.length?dates.sort().at(-1):Object.values(data)[0].at(-1).Date;$('indicator-ticker').innerHTML=Object.keys(data).map(ticker=>`<option>${ticker}</option>`).join('');if(data.QQQ)$('indicator-ticker').value='QQQ';$('detail-strategy').onchange=renderDetail;$('start-date').onchange=renderAnalysis;$('end-date').onchange=renderAnalysis;$('analysis-tab').onclick=()=>{$('analysis-view').classList.remove('offline-hidden');$('indicators-view').classList.add('offline-hidden');$('analysis-tab').classList.add('active');$('indicators-tab').classList.remove('active');};$('indicators-tab').onclick=()=>{$('analysis-view').classList.add('offline-hidden');$('indicators-view').classList.remove('offline-hidden');$('analysis-tab').classList.remove('active');$('indicators-tab').classList.add('active');renderIndicators();};$('import').onchange=async event=>{try{const def=parseYaml(await event.target.files[0].text());definitions=definitions.filter(item=>item.strategy.id!==def.strategy.id);definitions.push(def);await setupDashboard();$('status').textContent='YAML 전략을 불러왔습니다. 목록에서 선택하면 자동으로 준비됩니다.';}catch(error){$('status').textContent=`YAML 오류: ${error.message}`;}};runDashboard();}
function setupIndicatorControls(){const tickers=$('indicator-ticker'),type=$('indicator-type'),strategy=$('indicator-strategy'),fields=$('indicator-fields'),matrix=$('indicator-matrix'),tabs=$('indicator-panel-tabs'),summary=$('indicator-selection-summary'),data=marketData,labels={price:'가격 · 추세',oscillator:'오실레이터',risk:'리스크',Close:'종가',EMA20:'EMA 20',EMA55:'EMA 55',EMA200:'EMA 200',MA20:'이동평균 20',MA55:'이동평균 55',RSI14:'RSI 14',MACD:'MACD',MACD_SIGNAL:'MACD 신호',DRAWDOWN120:'120일 낙폭',VOL60:'60일 변동성',ATR60:'ATR 60'},escapeHtml=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));tickers.innerHTML=Object.keys(data).map(ticker=>`<option value="${ticker}">${ticker}</option>`).join('');fields.innerHTML=['Close','EMA20','EMA55'].map(field=>`<option value="${field}" selected>${field}</option>`).join('');if(!indicatorSelection.size)for(const field of ['Close','EMA20','EMA55'])indicatorSelection.add(`QQQ|${field}`);const enabled=definitions.filter(def=>def.strategy.enabled!==false);strategy.innerHTML='<option value="">전략 표시 안 함</option>'+enabled.map(def=>`<option value="${escapeHtml(def.strategy.id)}">${escapeHtml(def.strategy.name)}</option>`).join('');if(enabled.length)strategy.value=enabled[0].strategy.id;const dates=Object.values(data).flat().map(row=>row.Date).sort();$('indicator-start-date').value=dates[0]||'';$('indicator-end-date').value=dates.at(-1)||'';const refreshSummary=()=>{const pairs=[...indicatorSelection];summary.innerHTML=pairs.length?pairs.map(key=>{const [ticker,field]=key.split('|');return `<span class="research-indicator-selection-badge">${ticker} · ${labels[field]||field}</span>`;}).join(''):'<span class="research-indicator-selection-empty">선택된 조합 없음</span>';};const drawMatrix=()=>{const panel=type.value,available=Object.keys(data),rows=indicatorFields(panel);tabs.innerHTML=['price','oscillator','risk'].map(value=>`<button type="button" class="btn btn-sm ${value===panel?'btn-primary':'btn-ghost-secondary'}" data-panel="${value}">${labels[value]}</button>`).join('');matrix.innerHTML=`<div class="research-indicator-matrix-scroll"><div class="research-indicator-matrix-table" style="--indicator-ticker-count:${available.length}"><div class="research-indicator-matrix-header"><div class="research-indicator-matrix-corner">지표 / 종목</div>${available.map(ticker=>`<button type="button" class="research-indicator-column-toggle" data-ticker="${ticker}">${ticker}</button>`).join('')}</div>${rows.map(field=>`<div class="research-indicator-matrix-row"><button type="button" class="research-indicator-row-toggle" data-field="${field}">${labels[field]||field}</button>${available.map(ticker=>`<label><input class="indicator-matrix-cell" type="checkbox" value="${ticker}|${field}" ${indicatorSelection.has(`${ticker}|${field}`)?'checked':''}></label>`).join('')}</div>`).join('')}</div></div>`;tabs.querySelectorAll('button').forEach(button=>button.onclick=()=>{type.value=button.dataset.panel;drawMatrix();renderIndicators();});matrix.querySelectorAll('.indicator-matrix-cell').forEach(input=>input.onchange=()=>{input.checked?indicatorSelection.add(input.value):indicatorSelection.delete(input.value);refreshSummary();renderIndicators();});matrix.querySelectorAll('.research-indicator-column-toggle').forEach(button=>button.onclick=()=>{const cells=[...matrix.querySelectorAll(`.indicator-matrix-cell[value^="${button.dataset.ticker}|"]`)],checked=cells.some(input=>input.checked);cells.forEach(input=>{input.checked=!checked;input.checked?indicatorSelection.add(input.value):indicatorSelection.delete(input.value);});refreshSummary();renderIndicators();});matrix.querySelectorAll('.research-indicator-row-toggle').forEach(button=>button.onclick=()=>{const cells=[...matrix.querySelectorAll(`.indicator-matrix-cell[value$="|${button.dataset.field}"]`)],checked=cells.some(input=>input.checked);cells.forEach(input=>{input.checked=!checked;input.checked?indicatorSelection.add(input.value):indicatorSelection.delete(input.value);});refreshSummary();renderIndicators();});};strategy.onchange=renderIndicators;$('indicator-start-date').onchange=renderIndicators;$('indicator-end-date').onchange=renderIndicators;$('indicator-overlays').onchange=renderIndicators;refreshSummary();drawMatrix();}
const baseSetupDashboard=setupDashboard;
setupDashboard=async()=>{await baseSetupDashboard();setupIndicatorControls();};
function localizeIndicatorView(){const cards=document.querySelectorAll('#indicators-view .research-card'),labels=document.querySelectorAll('#indicators-view .form-label');cards[0]?.querySelector('.card-title')&&(cards[0].querySelector('.card-title').textContent='지표 연구');cards[0]?.querySelector('.research-card-description')&&(cards[0].querySelector('.research-card-description').textContent='종목과 지표를 조합해 시장 구간을 분석합니다.');cards[1]?.querySelector('.card-title')&&(cards[1].querySelector('.card-title').textContent='표시 지표');cards[1]?.querySelector('.research-card-description')&&(cards[1].querySelector('.research-card-description').textContent='선택된 종목과 지표 조합');if(labels[0])labels[0].textContent='분석 기간';if(labels[1])labels[1].textContent='전략 표시';if(labels[2])labels[2].textContent='전략 이벤트';const editor=$('indicator-editor');editor?.querySelector('summary span:nth-child(2)')&&(editor.querySelector('summary span:nth-child(2)').textContent='종목 · 지표 편집');const events=$('indicator-overlays');if(events){const eventLabels=events.querySelectorAll('label');if(eventLabels[0])eventLabels[0].lastChild.textContent='상태 구간';if(eventLabels[1])eventLabels[1].lastChild.textContent='리밸런싱';}}
const parityStyle=document.createElement('style');parityStyle.textContent='.offline-date-range{width:300px;gap:8px}.offline-date-range input,#start-date,#end-date,#indicator-start-date,#indicator-end-date{min-width:0;padding:7px 8px;font-size:16px!important;text-align:center}.research-kpi-card{position:relative;overflow:hidden}.research-kpi-icon{position:absolute;top:16px;right:16px;width:34px;height:34px;display:grid;place-items:center;border-radius:10px;font-size:18px;font-weight:700}.research-kpi-positive{color:#2fb344;background:rgba(47,179,68,.12)}.research-kpi-negative{color:#d63939;background:rgba(214,57,68,.12)}.research-kpi-primary{color:#206bc4;background:rgba(32,107,196,.12)}.research-kpi-cyan{color:#0ca8c0;background:rgba(12,168,192,.12)}.win11-date-picker{position:fixed;z-index:3000;width:320px;padding:12px;border:1px solid #d7dce4;border-radius:12px;background:#fff;box-shadow:0 18px 42px rgba(24,36,51,.2);color:#182433}.win11-date-picker[hidden]{display:none}.win11-date-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}.win11-date-title{min-width:0;border:0;background:transparent;color:#182433;font:700 15px inherit;cursor:pointer}.win11-date-nav{width:32px;height:32px;border:0;border-radius:7px;background:transparent;color:#182433;font-size:21px;line-height:1;cursor:pointer}.win11-date-nav:hover,.win11-date-nav:focus-visible,.win11-date-title:hover,.win11-date-title:focus-visible{background:#eef3fa;outline:none}.win11-date-weekdays,.win11-date-days{display:grid;grid-template-columns:repeat(7,1fr);gap:2px}.win11-date-weekdays{margin-bottom:4px;color:#65758b;font-size:12px;text-align:center}.win11-date-day{height:36px;border:0;border-radius:7px;background:transparent;color:#182433;font:inherit;cursor:pointer}.win11-date-day:hover,.win11-date-day:focus-visible,.win11-date-choice:hover,.win11-date-choice:focus-visible{background:#e9f2ff;outline:none}.win11-date-day.is-outside{color:#97a3b4}.win11-date-day.is-today{box-shadow:inset 0 0 0 1px #206bc4}.win11-date-day.is-selected{background:#206bc4;color:#fff;box-shadow:none}.win11-date-choices{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.win11-date-choice{min-height:48px;border:0;border-radius:8px;background:transparent;color:#182433;font:600 14px inherit;cursor:pointer}.win11-date-choice.is-selected{background:#206bc4;color:#fff}.win11-date-picker.is-dark{background:#202b3a;border-color:#36465a;color:#f2f6fb}.win11-date-picker.is-dark .win11-date-title,.win11-date-picker.is-dark .win11-date-nav,.win11-date-picker.is-dark .win11-date-day,.win11-date-picker.is-dark .win11-date-choice{color:#f2f6fb}.win11-date-picker.is-dark .win11-date-weekdays,.win11-date-picker.is-dark .win11-date-day.is-outside{color:#aeb9c7}.win11-date-picker.is-dark .win11-date-nav:hover,.win11-date-picker.is-dark .win11-date-nav:focus-visible,.win11-date-picker.is-dark .win11-date-title:hover,.win11-date-picker.is-dark .win11-date-title:focus-visible,.win11-date-picker.is-dark .win11-date-day:hover,.win11-date-picker.is-dark .win11-date-day:focus-visible,.win11-date-picker.is-dark .win11-date-choice:hover,.win11-date-picker.is-dark .win11-date-choice:focus-visible{background:#30425a}@media(max-width:520px){.win11-date-picker{width:calc(100vw - 24px)}}';document.head.append(parityStyle);
const baseIndicatorRenderer=renderIndicators;
renderIndicators=async()=>{await baseIndicatorRenderer();const plot=$('indicator-plot');if(!plot?.data)return;const palette=['#2962FF','#089981','#FF9800','#9C27B0','#F23645','#00BCD4'];Plotly.restyle(plot,{'line.color':plot.data.map((_,index)=>palette[index%palette.length])});const layout=chartLayout('지표 연구',{slider:true,selector:true,indicator:true});layout.yaxis.tickformat=undefined;layout.yaxis.title={text:$('indicator-type').value==='price'?'기준=100':'지표 값'};Plotly.relayout(plot,layout);};
const dashboardSetupWithIndicators=setupDashboard;
setupDashboard=async()=>{await dashboardSetupWithIndicators();for(const [id,classes] of Object.entries({'performance-plot':['research-graph','research-navigation-graph'],'drawdown-plot':['research-graph','research-navigation-graph'],'detail-plot':['research-graph','research-detail-graph','research-combined-detail-graph'],'indicator-plot':['research-graph','research-indicator-graph']}))$(id)?.classList.add(...classes);const resize=ids=>requestAnimationFrame(()=>ids.forEach(id=>{const plot=$(id);if(plot?.data)Plotly.Plots.resize(plot);}));$('analysis-tab').onclick=()=>{$('analysis-view').classList.remove('offline-hidden');$('indicators-view').classList.add('offline-hidden');$('analysis-tab').classList.add('active');$('indicators-tab').classList.remove('active');resize(['performance-plot','drawdown-plot','detail-plot']);};$('indicators-tab').onclick=async()=>{$('analysis-view').classList.add('offline-hidden');$('indicators-view').classList.remove('offline-hidden');$('analysis-tab').classList.remove('active');$('indicators-tab').classList.add('active');await renderIndicators();resize(['indicator-plot']);};};
let indicatorPanel=field=>['Close','EMA20','EMA55','EMA200','MA20','MA55'].includes(field)?'price':['RSI14','MACD','MACD_SIGNAL'].includes(field)?'oscillator':'risk';
const baseDetailRenderer=renderDetail;
const detailCardStyle=document.createElement('style');detailCardStyle.textContent='.research-kpi-description{margin-top:2px;color:#667382;font-size:11px}.research-kpi-value{margin-top:6px}';document.head.append(detailCardStyle);
renderDetail=()=>{baseDetailRenderer();const cards=[['CAGR','연환산 수익률','↗','positive'],['MDD','최대 낙폭','↕','negative'],['Sharpe','위험조정 성과','⚖','primary'],['Total Return','전체 수익률','%','cyan']];[...$('detail-kpis').children].forEach((card,index)=>{const value=card.querySelector('.research-kpi-value')?.textContent||'-',item=cards[index];card.innerHTML=`<div class="research-kpi-body"><div><div class="research-kpi-label">${item[0]}</div><div class="research-kpi-description">${item[1]}</div><div class="research-kpi-value">${value}</div></div><span class="research-kpi-icon research-kpi-${item[3]}">${item[2]}</span></div>`;});};
renderIndicators=async()=>{try{const data=await loadData(),start=$('indicator-start-date').value,end=$('indicator-end-date').value,pairs=[...indicatorSelection].map(key=>key.split('|')),palette=['#2962FF','#089981','#FF9800','#9C27B0','#F23645','#00BCD4'],groups=['price','oscillator','risk'].map(panel=>[panel,pairs.filter(([,field])=>indicatorPanel(field)===panel)]).filter(([,items])=>items.length),traces=[];for(const [panel,items] of groups){const axisIndex=groups.findIndex(([key])=>key===panel)+1,axis=axisIndex===1?'y':`y${axisIndex}`;for(const [index,[ticker,name]] of items.entries()){const rows=(data[ticker]||[]).filter(row=>(!start||row.Date>=start)&&(!end||row.Date<=end));traces.push({x:rows.map(row=>row.Date),y:rows.map(row=>Number(row[name]??row[name.toLowerCase()])),name:`${ticker} · ${name}`,xaxis:axisIndex===1?'x':`x${axisIndex}`,yaxis:axis,line:{width:.9},hoverinfo:'none'});}}const layout=chartLayout('지표 연구',{slider:true,selector:true,indicator:true}),count=Math.max(groups.length,1),domains=count===1?[[0,1]]:count===2?[[.54,1],[0,.42]]:[[.72,1],[.38,.64],[0,.30]],baseX={type:'date',tickformat:'%Y.%m',hoverformat:'%Y.%m.%d',gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false};layout.margin={l:58,r:58,t:154,b:82};layout.annotations=[];for(let index=0;index<count;index++){const panel=groups[index]?.[0]||'price',xkey=`xaxis${index+1===1?'':index+1}`,ykey=`yaxis${index+1===1?'':index+1}`,isBottom=index===count-1,axisX={...baseX,domain:[0,1],anchor:`y${index+1===1?'':index+1}`,showticklabels:isBottom,matches:index?'x':undefined};if(index===0)axisX.rangeselector=layout.xaxis.rangeselector;if(isBottom)axisX.rangeslider=layout.xaxis.rangeslider;layout[xkey]=axisX;layout[ykey]={gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false,domain:domains[index],title:{text:panel==='price'?'가격 · 추세':panel==='oscillator'?'오실레이터':'리스크',font:{size:12}},tickfont:{size:11}};layout.annotations.push({text:panel==='price'?'가격 · 추세':panel==='oscillator'?'오실레이터':'리스크',x:0,y:domains[index][1],xref:'paper',yref:'paper',xanchor:'left',yanchor:'bottom',showarrow:false,font:{size:13,color:'#182433'}});}if(groups.some(([panel])=>panel==='price')){const selected=$('indicator-strategy').value,cached=await loadPrecomputedResults(),history=(cached[selected]||[]).filter(row=>(!start||row.date>=start)&&(!end||row.date<=end));if(history.length){const priceIndex=groups.findIndex(([panel])=>panel==='price')+1,base=history[0].value,weightText=row=>Object.entries(row.weights||{}).filter(([,weight])=>weight>.001).map(([ticker,weight])=>`${ticker} ${(weight*100).toFixed(1)}%`).join('<br>'),axis=`y${count+1}`;layout[`yaxis${count+1}`]={title:'전략 수익률',overlaying:priceIndex===1?'y':`y${priceIndex}`,side:'right',tickformat:'.0%',showgrid:false};traces.push({x:history.map(row=>row.date),y:history.map(row=>row.value/base-1),name:'전략 누적 수익률',xaxis:priceIndex===1?'x':`x${priceIndex}`,yaxis:axis,line:{color:'#182433',width:1},customdata:history.map(weightText),hoverinfo:'none'});const assets=Object.keys(history[0].weights||{}),events=history.filter((row,index)=>index>0&&assets.some(asset=>Math.abs((row.weights[asset]||0)-(history[index-1].weights[asset]||0))>=.02));if(events.length&&[...$('indicator-overlays').querySelectorAll('input:checked')].some(input=>input.value==='rebalances'))traces.push({x:events.map(row=>row.date),y:events.map(row=>row.value/base-1),name:'리밸런싱',xaxis:priceIndex===1?'x':`x${priceIndex}`,yaxis:axis,mode:'markers',marker:{symbol:'diamond',size:8,color:'#F23645'},customdata:events.map(weightText),hoverinfo:'skip'});}}layout.legend={orientation:'h',y:1.02,x:0,yanchor:'bottom',entrywidth:.2,entrywidthmode:'fraction',font:{size:12}};Plotly.react('indicator-plot',traces,layout,graphConfig);bindChartTooltip('indicator-plot','indicator');}catch(error){$('status').textContent=`오류: ${error.message}`;}};

const dashPalette=['#2962FF','#089981','#FF9800','#9C27B0','#F23645','#00BCD4'];
const strategyChartColors=new Map();
function strategyChartColor(strategyId){for(const definition of definitions){const id=definition?.strategy?.id;if(id&&!strategyChartColors.has(id))strategyChartColors.set(id,dashPalette[strategyChartColors.size%dashPalette.length]);}if(!strategyChartColors.has(strategyId))strategyChartColors.set(strategyId,dashPalette[strategyChartColors.size%dashPalette.length]);return strategyChartColors.get(strategyId);}
const tooltipEscape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const tooltipNumber=value=>Number.isFinite(Number(value))?Number(value).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—';
const tooltipIndicatorValue=point=>{const raw=point?.customdata||point;return point?.data?.type==='candlestick'?`시 ${tooltipNumber(raw.open)}\n고 ${tooltipNumber(raw.high)}\n저 ${tooltipNumber(raw.low)}\n종 ${tooltipNumber(raw.close)}`:tooltipNumber(point?.customdata??point?.y);};
function candleIndexForDate(dates,hoverDate){let matched=-1;for(let index=0;index<(dates||[]).length;index++)if(String(dates[index]).slice(0,10)<=hoverDate)matched=index;return matched;}
const percentText=value=>`${Number(value).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}%`;

function tooltipRow(label,value,target=null,expanded=false){return `<div class="research-custom-tooltip-row"><span class="research-custom-tooltip-label">${tooltipEscape(label)}</span><span class="research-custom-tooltip-value">${tooltipEscape(value)}</span>${expanded?`<span class="research-custom-tooltip-arrow">${target?'→':''}</span><span class="research-custom-tooltip-target">${tooltipEscape(target||'')}</span>`:''}</div>`;}
function tooltipCard(date,rows,{detail=false,targets=false,footer=''}={}){return `<div class="research-custom-tooltip-card ${detail?'research-detail-tooltip-card':'research-comparison-tooltip-card'}"><div class="research-custom-tooltip-date">${tooltipEscape(date)}</div><div class="research-custom-tooltip-grid ${detail?'research-detail-tooltip-grid':'research-comparison-tooltip-grid'}${targets?' research-detail-tooltip-grid-with-targets':''}">${rows.join('')}</div>${footer?`<div class="research-custom-tooltip-footer">${tooltipEscape(footer)}</div>`:''}</div>`;}
function dateAnnotations(start,end,y){if(!start||!end)return[];const first=new Date(`${start}T00:00:00Z`),last=new Date(`${end}T00:00:00Z`);if(!(first<last))return[{x:.5,xref:'paper',y,yref:'paper',text:String(first.getUTCFullYear()),showarrow:false,font:{size:10,color:'#667382'},xanchor:'center',yanchor:'top'}];const span=last.getUTCFullYear()-first.getUTCFullYear(),raw=Math.max(1,Math.ceil(span/10)),step=[1,2,3,5,10,20,50,100].find(value=>value>=raw)||100,marks=[first];for(let year=first.getUTCFullYear()+1;year<=last.getUTCFullYear();year++)if(year%step===0)marks.push(new Date(Date.UTC(year,0,1)));marks.push(last);const duration=last-first,seen=new Set;return marks.filter(date=>{if(seen.has(date.getUTCFullYear()))return false;seen.add(date.getUTCFullYear());return true;}).map(date=>({x:Math.max(0,Math.min(1,(date-first)/duration)),xref:'paper',y,yref:'paper',text:String(date.getUTCFullYear()),showarrow:false,font:{size:10,color:'#667382'},xanchor:'center',yanchor:'top'}));}

chartLayout=function(title,{slider=false,selector=true,separateSelector=false,legendColumns=2,legendItems=1,legendPlotGap=6,selectorLegendGap=14,height=450,start=null,end=null}={}){const rows=Math.max(1,Math.ceil(legendItems/Math.max(1,legendColumns))),top=selector?(separateSelector?Math.max(96,32+selectorLegendGap+19*rows+legendPlotGap):96):48,bottom=slider?78:54,plotHeight=Math.max(height-top-bottom,1),legendY=1+legendPlotGap/plotHeight,selectorY=legendY+(19*rows+selectorLegendGap)/plotHeight,sliderLabelY=-.235,buttons=[{count:1,label:'1년',step:'year',stepmode:'backward'},{count:3,label:'3년',step:'year',stepmode:'backward'},{count:5,label:'5년',step:'year',stepmode:'backward'},{label:'전체',step:'all'}],xaxis={fixedrange:false,type:'date',tickformat:'%Y.%m',tickformatstops:[{dtickrange:[null,'M1'],value:'%Y.%m.%d'},{dtickrange:['M1',null],value:'%Y.%m'}],nticks:8,hoverformat:'%Y.%m.%d',gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false,automargin:true};if(slider)xaxis.rangeslider={visible:true,thickness:.09,bgcolor:'rgba(106,109,120,.06)',bordercolor:'#dce1e7',borderwidth:1,range:start&&end?[start,end]:undefined};if(selector)xaxis.rangeselector={buttons,x:separateSelector?0:1,xanchor:separateSelector?'left':'right',y:separateSelector?selectorY:1.18,yanchor:'bottom',bgcolor:'rgba(106,109,120,.08)',activecolor:'rgba(41,98,255,.18)',bordercolor:'#dce1e7',borderwidth:1,font:{size:11}};if(start&&end){xaxis.minallowed=start;xaxis.maxallowed=end;}return {height,dragmode:'pan',margin:{l:56,r:20,t:top,b:bottom,autoexpand:true},colorway:dashPalette,paper_bgcolor:'transparent',plot_bgcolor:'transparent',hovermode:'x unified',hoverdistance:-1,spikedistance:-1,showlegend:true,xaxis,yaxis:{gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false,fixedrange:true,automargin:true,tickfont:{size:12},title:{text:title==='낙폭 경로'?'낙폭 (%)':'수익률 (%)',font:{size:13},standoff:10}},legend:{orientation:'h',y:separateSelector?legendY:1.02,x:0,yanchor:'bottom',entrywidth:separateSelector?1/Math.max(1,legendColumns):undefined,entrywidthmode:separateSelector?'fraction':undefined,font:{size:12}},annotations:slider?dateAnnotations(start,end,sliderLabelY):[],font:{family:'-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",sans-serif',color:'#182433',size:13}};};

function positionTooltip(card,plot,kind,pointer){const graph=plot.getBoundingClientRect();let cx=pointer?.clientX,cy=pointer?.clientY;if(cx===undefined&&pointer?.touches?.length){cx=pointer.touches[0].clientX;cy=pointer.touches[0].clientY;}const anchorX=Number.isFinite(cx)?cx:graph.left+graph.width/2,anchorY=Number.isFinite(cy)?cy:graph.top+graph.height/2,placeLeft=anchorX>graph.left+graph.width/2,placeAbove=anchorY>graph.top+graph.height/2;let left,top;if(kind==='comparison'){left=anchorX-card.offsetWidth/2;top=placeAbove?anchorY-card.offsetHeight-18:anchorY+18;}else{left=placeLeft?anchorX-card.offsetWidth-14:anchorX+14;top=anchorY-card.offsetHeight/2;}card.style.left=`${Math.max(12,Math.min(window.innerWidth-card.offsetWidth-12,left))}px`;card.style.top=`${Math.max(12,Math.min(window.innerHeight-card.offsetHeight-12,top))}px`;}

bindChartTooltip=function(plotId,kind){const plot=$(plotId);if(!plot||plot.dataset.dashTooltipBound)return;plot.dataset.dashTooltipBound='1';let card;const hide=()=>{if(card)card.style.display='none';};const checkOutside=event=>{let cx=event.clientX,cy=event.clientY;if(cx===undefined&&event.changedTouches?.length){cx=event.changedTouches[0].clientX;cy=event.changedTouches[0].clientY;}else if(cx===undefined&&event.touches?.length){cx=event.touches[0].clientX;cy=event.touches[0].clientY;}if(cx===undefined)return;const rect=plot.getBoundingClientRect();if(cx<rect.left||cx>rect.right||cy<rect.top||cy>rect.bottom)hide();};document.addEventListener('touchstart',checkOutside,{passive:true});plot.on('plotly_unhover',hide);['plotly_hover', 'plotly_click'].forEach(evt => plot.on(evt, event=>{const all=event.points||[],points=all.filter(point=>kind==='indicator'?point.data?.meta&&!point.data.meta.excludeTooltip:Boolean(point.customdata));if(!points.length)return hide();const date=String(points[0].x||points[0].customdata?.date||'').slice(0,10).replaceAll('-','.');let html;if(kind==='comparison'){const rows=points.filter(point=>point.customdata?.name).map(point=>tooltipRow(point.customdata.name,point.customdata.value));if(!rows.length)return hide();html=tooltipCard(points[0].customdata.date||date,rows);}else if(kind==='detail'){const point=points.find(item=>item.customdata?.assets);if(!point)return hide();const data=point.customdata,targets=data.assets.some(asset=>asset.target),rows=[tooltipRow('누적 수익률',data.return,null,targets),'<div class="research-custom-tooltip-separator"></div>',...data.assets.map(asset=>tooltipRow(asset.name,asset.current,asset.target,targets))];html=tooltipCard(data.date,rows,{detail:true,targets,footer:data.executionDays?`분할 체결 ${data.executionDays}거래일`:''});points.splice(0,points.length,point);}else{const panel=points[0].data.meta.panel,strategy=points.find(point=>point.data.meta.isStrategySeries)?.customdata,strategyContext=panel==='price'?strategy:null,targets=strategyContext?.assets?.some(asset=>asset.target)||false,rows=[];if(strategyContext){const state=strategyContext.state?` (${strategyContext.state})`:'';rows.push(tooltipRow(`누적 수익률${state}`,strategyContext.return,null,targets),'<div class="research-custom-tooltip-separator"></div>',...strategyContext.assets.map(asset=>tooltipRow(asset.name,asset.current,asset.target,targets)),'<div class="research-indicator-tooltip-section-separator"></div>');}rows.push(...points.filter(point=>!point.data.meta.isStrategySeries).map(point=>tooltipRow(point.data.meta.tooltipName,tooltipIndicatorValue(point),null,targets)));if(!rows.length)return hide();html=tooltipCard(date,rows,{targets,footer:strategyContext?.executionDays?`분할 체결 ${strategyContext.executionDays}거래일`:''});}if(!card){card=document.createElement('div');card.className=`offline-chart-tooltip research-chart-tooltip research-${kind}-tooltip`;document.body.append(card);}card.innerHTML=html;card.style.display='block';positionTooltip(card,plot,kind,event.event);}));};

function detailTooltipPayload(history,returns,tickers){return history.map((row,index)=>{const target=row.target&&typeof row.target==='object'?row.target:null,preWeights=row.preWeights&&typeof row.preWeights==='object'?row.preWeights:null;return {date:row.date.replaceAll('-','.'),return:percentText(returns[index]),assets:tickers.map(ticker=>({name:ticker,current:percentText(((preWeights||row.weights||{})[ticker]||0)*100),target:target?percentText((target[ticker]||0)*100):null})),executionDays:target?row.executionDays:null,state:row.state||''};});}

renderAnalysis=function(){const entries=[...dashboardResults.values()],performance=[],drawdown=[],start=$('start-date').value,end=$('end-date').value;for(const [def,allHistory] of entries){const history=visibleHistory(allHistory);if(!history.length)continue;const base=history[0].value,returns=history.map(row=>(row.value/base-1)*100),peaks=[];let peak=-Infinity;for(const value of history.map(row=>row.value)){peak=Math.max(peak,value);peaks.push((value/peak-1)*100);}const common={x:history.map(row=>row.date),name:def.strategy.name,mode:'lines',connectgaps:false,line:{color:strategyChartColor(def.strategy.id),width:.9},hoverinfo:'none'};performance.push({...common,y:returns,customdata:history.map((row,i)=>({date:row.date.replaceAll('-','.'),name:def.strategy.name,value:percentText(returns[i])}))});drawdown.push({...common,y:peaks,customdata:history.map((row,i)=>({date:row.date.replaceAll('-','.'),name:def.strategy.name,value:percentText(peaks[i])}))});}const options={selector:true,separateSelector:true,legendColumns:2,legendItems:entries.length,height:450,start,end};Plotly.react('performance-plot',performance,chartLayout('누적 수익률',options),graphConfig);Plotly.react('drawdown-plot',drawdown,chartLayout('낙폭 경로',options),graphConfig);bindChartTooltip('performance-plot','comparison');bindChartTooltip('drawdown-plot','comparison');drawSummary(entries);const detail=$('detail-strategy'),previous=detail.value;detail.innerHTML=entries.map(([def])=>`<option value="${def.strategy.id}">${def.strategy.name}</option>`).join('');detail.value=dashboardResults.has(previous)?previous:entries[0]?.[0].strategy.id||'';renderDetail();};

renderDetail=function(){const id=$('detail-strategy').value,entry=dashboardResults.get(id);if(!entry)return;const [def,allHistory]=entry,history=visibleHistory(allHistory),m=metrics(history),daily=history.slice(1).map((row,index)=>row.value/history[index].value-1),mean=daily.reduce((sum,value)=>sum+value,0)/Math.max(daily.length,1),variance=daily.reduce((sum,value)=>sum+(value-mean)**2,0)/Math.max(daily.length-1,1),vol=Math.sqrt(variance)*Math.sqrt(252),sharpe=vol?m.cagr/vol:0,cards=[['CAGR','연환산 수익률',fmtPercent(m.cagr),'↗','positive'],['MDD','최대 낙폭',fmtPercent(m.mdd),'↕','negative'],['Sharpe','위험조정 성과',sharpe.toFixed(2),'⚖','primary'],['Total Return','전체 수익률',fmtPercent(m.total),'%','cyan']];$('detail-kpis').innerHTML=cards.map(([label,description,value,icon,color])=>`<div class="card research-kpi-card"><div class="research-kpi-body"><div><div class="research-kpi-label">${label}</div><div class="research-kpi-description">${description}</div><div class="research-kpi-value">${value}</div></div><span class="research-kpi-icon research-kpi-${color}">${icon}</span></div></div>`).join('');if(!history.length)return;const base=history[0].value,returns=history.map(row=>(row.value/base-1)*100),tickers=Object.keys(history[0].weights||{}),payload=detailTooltipPayload(history,returns,tickers),traces=[];for(const ticker of tickers)traces.push({x:history.map(row=>row.date),y:history.map((row,index)=>(row.weights?.[ticker]||0)*returns[index]),name:ticker,stackgroup:'portfolio-return',mode:'lines',line:{width:.4},hoverinfo:'skip'});traces.push({x:history.map(row=>row.date),y:returns,name:'누적 수익률',showlegend:false,line:{color:'#182433',width:1.2},customdata:payload,hoverinfo:'none'});const hasEventMetadata=history.some(row=>row.target),events=history.map((row,index)=>({row,index})).filter(({row,index})=>hasEventMetadata?Boolean(row.target):(index>0&&tickers.some(ticker=>Math.abs((row.weights?.[ticker]||0)-(history[index-1].weights?.[ticker]||0))>=.02)));if(events.length)traces.push({x:events.map(item=>item.row.date),y:events.map(item=>returns[item.index]),name:'리밸런싱',mode:'markers',marker:{symbol:'diamond',size:9,color:'#F23645',line:{color:'#fff',width:1}},hoverinfo:'skip'});const start=history[0].date,end=history.at(-1).date,layout=chartLayout('누적 수익률',{slider:true,selector:true,height:520,start,end});layout.xaxis.unifiedhovertitle={text:'%{x|%Y.%m.%d}'};Plotly.react('detail-plot',traces,layout,graphConfig);bindChartTooltip('detail-plot','detail');};

renderIndicators=async function(){try{const data=await loadData(),start=$('indicator-start-date').value,end=$('indicator-end-date').value,pairs=[...indicatorSelection].map(key=>key.split('|')),groups=['price','oscillator','risk'].map(panel=>[panel,pairs.filter(([,field])=>indicatorPanel(field)===panel)]).filter(([,items])=>items.length),selected=$('indicator-strategy').value,cached=await loadPrecomputedResults(),current=dashboardResults.get(selected)?.[1],history=(current?.length?current:(cached[selected]||[])).filter(row=>(!start||row.date>=start)&&(!end||row.date<=end));if(history.length&&!groups.some(([panel])=>panel==='price'))groups.unshift(['price',[]]);const count=Math.max(groups.length,1),height=342+210*count,legendItems=pairs.length+(history.length?1:0),layout=chartLayout('지표 연구',{slider:true,selector:true,separateSelector:true,legendColumns:5,legendItems,legendPlotGap:42,selectorLegendGap:24,height,start,end}),domains=count===1?[[0,1]]:count===2?[[.555,1],[0,.445]]:[[.69,1],[.345,.62],[0,.275]],traces=[],baseX={type:'date',tickformat:'%Y.%m',tickformatstops:[{dtickrange:[null,'M1'],value:'%Y.%m.%d'},{dtickrange:['M1',null],value:'%Y.%m'}],hoverformat:'%Y.%m.%d',gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false,fixedrange:false};layout.annotations=[];let styleIndex=0;for(let panelIndex=0;panelIndex<count;panelIndex++){const [panel,items]=groups[panelIndex]||['price',[]],axisNumber=panelIndex+1,axis=axisNumber===1?'y':`y${axisNumber}`;for(const [ticker,name] of items){const rows=(data[ticker]||[]).filter(row=>(!start||row.Date>=start)&&(!end||row.Date<=end)),closeBase=Number(rows[0]?.Close),values=rows.map(row=>Number(row[name]??row[name.toLowerCase()])),indexed=panel==='price'&&closeBase?values.map(value=>value/closeBase*100):values,dashes=['solid','dash','dot','dashdot'];traces.push({type:'scattergl',x:rows.map(row=>row.Date),y:indexed,name:`${ticker} · ${name}`,xaxis:axisNumber===1?'x':`x${axisNumber}`,yaxis:axis,mode:'lines',connectgaps:false,line:{color:dashPalette[styleIndex%dashPalette.length],width:.75,dash:dashes[Math.floor(styleIndex/dashPalette.length)%dashes.length]},meta:{tooltipName:`${ticker} · ${name}`,panel},hoverinfo:'none'});styleIndex++;}const xkey=`xaxis${axisNumber===1?'':axisNumber}`,ykey=`yaxis${axisNumber===1?'':axisNumber}`,isBottom=panelIndex===count-1;layout[xkey]={...baseX,domain:[0,1],anchor:axis,showticklabels:isBottom,matches:panelIndex?'x':undefined};layout[ykey]={gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false,domain:domains[panelIndex],fixedrange:true,automargin:true,title:{text:panel==='price'?'기준=100':'값',font:{size:13},standoff:10},tickfont:{size:12}};layout.annotations.push({text:panel==='price'?'가격 · 추세':panel==='oscillator'?'오실레이터':'리스크',x:0,y:domains[panelIndex][1],xref:'paper',yref:'paper',xanchor:'left',yanchor:'bottom',showarrow:false,font:{size:13,color:'#182433'}});}const bottomKey=`xaxis${count===1?'':count}`;layout[bottomKey].rangeslider=layout.xaxis.rangeslider;layout[bottomKey].rangeselector=layout.xaxis.rangeselector;if(count>1){delete layout.xaxis.rangeslider;delete layout.xaxis.rangeselector;}if(history.length){const priceIndex=groups.findIndex(([panel])=>panel==='price')+1,base=history[0].value,tickers=Object.keys(history[0].weights||{}),returns=history.map(row=>row.value/base*100),detailReturns=history.map(row=>(row.value/base-1)*100),basePayload=detailTooltipPayload(history,detailReturns,tickers),payload=history.map((row,index)=>({...basePayload[index],state:({BULL:'상승',CAUTION:'주의',BEAR:'하락',RECOVERY:'회복'})[row.state]||row.state||''}));traces.push({type:'scattergl',x:history.map(row=>row.date),y:returns,name:definitions.find(def=>def.strategy.id===selected)?.strategy.name||'전략',xaxis:priceIndex===1?'x':`x${priceIndex}`,yaxis:priceIndex===1?'y':`y${priceIndex}`,mode:'lines',connectgaps:false,line:{color:'#182433',width:1.2},meta:{tooltipName:'전략',panel:'price',isStrategySeries:true},customdata:payload,hoverinfo:'none'});const overlays=[...$('indicator-overlays').querySelectorAll('input:checked')].map(input=>input.value),events=history.map((row,index)=>({row,index})).filter(({row})=>Boolean(row.target));if(overlays.includes('rebalances')&&events.length)traces.push({type:'scattergl',x:events.map(item=>item.row.date),y:events.map(item=>returns[item.index]),name:'리밸런싱',showlegend:false,xaxis:priceIndex===1?'x':`x${priceIndex}`,yaxis:priceIndex===1?'y':`y${priceIndex}`,mode:'markers',marker:{symbol:'diamond',size:7,color:'#F23645'},meta:{excludeTooltip:true,panel:'price'},hoverinfo:'skip'});if(overlays.includes('states')){const colors={BULL:['rgba(47,179,68,.18)','rgba(47,179,68,.45)'],CAUTION:['rgba(245,159,0,.22)','rgba(245,159,0,.52)'],BEAR:['rgba(214,57,57,.18)','rgba(214,57,57,.46)'],RECOVERY:['rgba(32,107,196,.18)','rgba(32,107,196,.45)']},shapes=[];let run=0;for(let index=1;index<=history.length;index++)if(index===history.length||history[index].state!==history[run].state){const [fill,line]=colors[history[run].state]||['rgba(98,105,118,.12)','rgba(98,105,118,.35)'];shapes.push({type:'rect',x0:history[run].date,x1:history[Math.min(index,history.length-1)].date,xref:priceIndex===1?'x':`x${priceIndex}`,y0:0,y1:1,yref:`${priceIndex===1?'y':`y${priceIndex}`} domain`,fillcolor:fill,line:{color:line,width:.35},layer:'below'});run=index;}layout.shapes=shapes;}}layout.legend={orientation:'h',x:0,y:1+42/Math.max(height-layout.margin.t-layout.margin.b,1),yanchor:'bottom',entrywidth:.2,entrywidthmode:'fraction',font:{size:12}};layout.annotations.push(...dateAnnotations(start,end,-.235));$('indicator-plot').style.height=`${height}px`;$('indicator-plot').style.minHeight=`${height}px`;Plotly.react('indicator-plot',traces,layout,graphConfig);bindChartTooltip('indicator-plot','indicator');}catch(error){$('status').textContent=`오류: ${error.message}`;}};
function applyResponsiveLegend(plotId,legendPlotGap,selectorLegendGap){if(window.innerWidth>760)return;const plot=$(plotId),items=(plot?.data||[]).filter(trace=>trace.showlegend!==false&&trace.name).length,height=Number(plot?.layout?.height||plot?.clientHeight||450),bottom=plot?.layout?.margin?.b||54,top=Math.max(96,32+selectorLegendGap+19*Math.max(items,1)+legendPlotGap),plotHeight=Math.max(height-top-bottom,1),selectorAxis=Object.keys(plot.layout||{}).find(key=>/^xaxis\d*$/.test(key)&&plot.layout[key]?.rangeselector),update={'margin.t':top,'legend.entrywidth':1,'legend.entrywidthmode':'fraction','legend.y':1+legendPlotGap/plotHeight};if(selectorAxis)update[`${selectorAxis}.rangeselector.y`]=update['legend.y']+(19*Math.max(items,1)+selectorLegendGap)/plotHeight;Plotly.relayout(plot,update);}
// Keep stacked-series legends in the same order as the declared asset list.
const assetOrderedChartLayout=chartLayout;
chartLayout=(...args)=>{const layout=assetOrderedChartLayout(...args);layout.legend={...layout.legend,traceorder:'normal'};return layout;};
const dashMatchedAnalysisRenderer=renderAnalysis;
renderAnalysis=function(){dashMatchedAnalysisRenderer();requestAnimationFrame(()=>{applyResponsiveLegend('performance-plot',6,14);applyResponsiveLegend('drawdown-plot',6,14);});};

const dashboardTargetIndicatorRenderer=renderIndicators;
renderIndicators=async function(){
  const selected=$('indicator-strategy').value,entry=dashboardResults.get(selected);
  if(!entry)return dashboardTargetIndicatorRenderer();
  const originalLoadPrecomputedResults=loadPrecomputedResults;
  loadPrecomputedResults=async()=>({
    ...await originalLoadPrecomputedResults(),
    [selected]:entry[1],
  });
  try{return await dashboardTargetIndicatorRenderer();}
  finally{loadPrecomputedResults=originalLoadPrecomputedResults;}
};
const dashMatchedIndicatorRenderer=renderIndicators;
renderIndicators=async function(){await dashMatchedIndicatorRenderer();applyResponsiveLegend('indicator-plot',42,24);};

const boundedIndicatorRenderer=renderIndicators;
renderIndicators=async function(){await boundedIndicatorRenderer();const plot=$('indicator-plot'),start=$('indicator-start-date').value,end=$('indicator-end-date').value;if(!plot?.data||!start||!end)return;const axes=Object.keys(plot.layout||{}).filter(key=>/^xaxis\d*$/.test(key)),bottomAxis=axes.sort((left,right)=>Number(left.slice(5)||1)-Number(right.slice(5)||1)).at(-1),updates={},plotHeight=Number(plot.layout?.height||plot.clientHeight||520),marginTop=Number(plot.layout?.margin?.t||96),marginBottom=Number(plot.layout?.margin?.b||78),detailSliderPixels=(520-96-78)*.09,sliderThickness=detailSliderPixels/Math.max(plotHeight-marginTop-marginBottom,1);for(const axis of axes){updates[`${axis}.minallowed`]=start;updates[`${axis}.maxallowed`]=end;updates[`${axis}.rangeslider.visible`]=axis===bottomAxis;}updates[`${bottomAxis}.rangeslider.thickness`]=sliderThickness;updates[`${bottomAxis}.rangeslider.bgcolor`]='rgba(106,109,120,.06)';updates[`${bottomAxis}.rangeslider.bordercolor`]='#dce1e7';updates[`${bottomAxis}.rangeslider.borderwidth`]=1;updates[`${bottomAxis}.rangeslider.range`]=[start,end];Plotly.relayout(plot,updates);};

const rebalanceMetadataLoads=new Map(),rebalanceMetadataReady=new Set();
function hydrateRebalanceMetadata(id,def,history){
  if(history.some(row=>row.target)||rebalanceMetadataReady.has(id))return Promise.resolve(history);
  if(!rebalanceMetadataLoads.has(id))rebalanceMetadataLoads.set(id,run(def).then(calculated=>{
    const eventsByDate=new Map(calculated.filter(row=>row.target).map(row=>[row.date,row]));
    for(const row of history){const event=eventsByDate.get(row.date);if(event){row.target=event.target;row.preWeights=event.preWeights;row.executionDays=event.executionDays;}}
    rebalanceMetadataReady.add(id);
    return history;
  }).catch(error=>{console.warn('리밸런싱 메타데이터를 불러오지 못했습니다.',error);return history;}));
  return rebalanceMetadataLoads.get(id);
}

let dashboardRunRevision=0,dashboardRunTimer=null;
function scheduleDashboardRun(){
  const revision=++dashboardRunRevision;
  if(dashboardRunTimer!==null)clearTimeout(dashboardRunTimer);
  $('status').textContent='선택 변경을 반영하고 있습니다…';
  dashboardRunTimer=setTimeout(()=>{
    dashboardRunTimer=null;
    void runDashboard(revision);
  },1000);
}
function clearDashboardVisuals(){
  dashboardResults=new Map();
  Plotly.react('performance-plot',[],chartLayout('누적 수익률'),graphConfig);
  Plotly.react('drawdown-plot',[],chartLayout('낙폭 경로'),graphConfig);
  Plotly.react('detail-plot',[],chartLayout('누적 수익률',{slider:true,selector:true,detail:true}),graphConfig);
  $('detail-kpis').replaceChildren();
  $('detail-strategy').replaceChildren();
  drawSummary([]);
  document.querySelectorAll('.offline-range-years').forEach(node=>node.remove());
}

runDashboard=async function(requestedRevision){
  let revision=requestedRevision;
  if(revision===undefined){
    if(dashboardRunTimer!==null){clearTimeout(dashboardRunTimer);dashboardRunTimer=null;}
    revision=++dashboardRunRevision;
  }else if(revision!==dashboardRunRevision)return false;
  try{
    $('status').textContent='저장된 전략 결과를 불러오고 있습니다…';
    const selected=selectedDefinitions();
    if(!selected.length){
      clearDashboardVisuals();
      $('status').textContent='전략을 하나 이상 선택하세요.';
      return true;
    }
    const cached=bundle.precomputed_results_currency==='KRW'?await loadPrecomputedResults():{};
    const settled=await Promise.allSettled(selected.map(async def=>{
      const saved=cached[def.strategy.id]||[];
      return [def,saved.length?saved:await run(def)];
    }));
    if(revision!==dashboardRunRevision)return false;
    const completed=settled.filter(result=>result.status==='fulfilled').map(result=>result.value);
    const failed=settled.filter(result=>result.status==='rejected');
    if(!completed.length)throw failed[0].reason;
    dashboardResults=new Map(completed.map(entry=>[entry[0].strategy.id,entry]));
    renderAnalysis();
    $('status').textContent=failed.length?`${completed.length}개 전략 로드 · ${failed.length}개 실패: ${failed.map(item=>item.reason.message).join(' | ')}`:'';
    return true;
  }catch(error){
    if(revision!==dashboardRunRevision)return false;
    clearDashboardVisuals();
    $('status').textContent=`오류: ${error.message}`;
    console.error(error);
    return true;
  }
};

const targetAwareDetailRenderer=renderDetail;
renderDetail=function(){targetAwareDetailRenderer();const id=$('detail-strategy').value,entry=dashboardResults.get(id);if(!entry||entry[1].some(row=>row.target)||rebalanceMetadataReady.has(id))return;const schedule=window.requestIdleCallback||((callback)=>setTimeout(callback,2000));schedule(()=>{hydrateRebalanceMetadata(id,entry[0],entry[1]).then(()=>{if($('detail-strategy').value===id)renderDetail();});});};

let indicatorControlsInitialized=false;
setupDashboard=async()=>{const enabled=definitions.filter(def=>def.strategy.enabled!==false),cached=await loadPrecomputedResults(),dates=Object.values(cached).flatMap(history=>history.map(row=>row.date)).sort();$('strategy-count').textContent=`${enabled.length}개 전략`;$('strategy-list').innerHTML=enabled.map(def=>`<label><input type="checkbox" value="${def.strategy.id}" checked>${def.strategy.name}</label>`).join('');$('start-date').value=dates[0]||'';$('end-date').value=dates.at(-1)||'';$('detail-strategy').onchange=renderDetail;$('start-date').onchange=renderAnalysis;$('end-date').onchange=renderAnalysis;$('analysis-tab').onclick=()=>{$('analysis-view').classList.remove('offline-hidden');$('indicators-view').classList.add('offline-hidden');$('analysis-tab').classList.add('active');$('indicators-tab').classList.remove('active');requestAnimationFrame(()=>['performance-plot','drawdown-plot','detail-plot'].forEach(id=>$(id)?.data&&Plotly.Plots.resize($(id))));};$('indicators-tab').onclick=async()=>{$('analysis-view').classList.add('offline-hidden');$('indicators-view').classList.remove('offline-hidden');$('analysis-tab').classList.remove('active');$('indicators-tab').classList.add('active');await loadData();if(!indicatorControlsInitialized){setupIndicatorControls();indicatorControlsInitialized=true;}await renderIndicators();requestAnimationFrame(()=>$('indicator-plot')?.data&&Plotly.Plots.resize($('indicator-plot')));};$('import').onchange=async event=>{try{const def=parseYaml(await event.target.files[0].text());definitions=definitions.filter(item=>item.strategy.id!==def.strategy.id);definitions.push(def);await setupDashboard();$('status').textContent='YAML 전략을 불러왔습니다. 목록에서 선택하면 자동으로 준비됩니다.';}catch(error){$('status').textContent=`YAML 오류: ${error.message}`;}};for(const [id,classes] of Object.entries({'performance-plot':['research-graph','research-navigation-graph'],'drawdown-plot':['research-graph','research-navigation-graph'],'detail-plot':['research-graph','research-detail-graph','research-combined-detail-graph'],'indicator-plot':['research-graph','research-indicator-graph']}))$(id)?.classList.add(...classes);await runDashboard();};
const statePreservingDashboardSetup=setupDashboard;
const uiStateKey='investment-strategy:ui-state';
const legacyStrategyIds={
  'retirement-allocation':'allocation',
  'retirement-allocation-vxus':'allocation-vxus',
  'retirement-allocation-profit-band':'profit-band',
  'retirement-allocation-profit-band-vxus':'profit-band-vxus',
  'retirement-allocation-profit-band-vxus2':'profit-band-vxus-v2',
  'retirement-profit-band-tdf2050-proxy':'profit-band-tdf2050',
  'retirement-7030-band':'band-7030',
  'retirement-7030-tdf':'band-7030-tdf',
  'pension-kodex-nasdaq':'kodex-nasdaq',
  'pension-time-nasdaq':'time-nasdaq',
  'pension-koact-nasdaq':'koact-nasdaq',
  'pension-nasdaq-product-mix':'nasdaq-mix',
  'pension-time-tdf2050-profit-band':'time-tdf2050-profit-band',
  'pension-product-mix':'nasdaq-tdf2050-7030',
  'pension-product-mix2':'nasdaq-tdf2050-mix',
  'static-retirement-7030':'static-7030',
  'asymmetric-trend-band-add-defense2':'trend-band-defense',
};
function migrateStrategyId(id){return legacyStrategyIds[id]||id;}
function migrateStrategyDefinition(definition){
  if(!definition?.strategy)return definition;
  const strategy={...definition.strategy,id:migrateStrategyId(definition.strategy.id)};
  return {...definition,strategy,...(definition.source?{source:migrateStrategyId(definition.source)}:{})};
}
function loadUiState(){try{const state=JSON.parse(localStorage.getItem(uiStateKey)||'{}')||{};for(const key of ['strategyIds','knownStrategyIds','indicatorSelection'])if(Array.isArray(state[key]))state[key]=state[key].map(migrateStrategyId);for(const key of ['detailStrategy','indicatorStrategy'])if(state[key])state[key]=migrateStrategyId(state[key]);return state;}catch{return {};}}
function saveUiState(activeView){try{activeView=typeof activeView==='string'?activeView:null;const previous=loadUiState(),strategyInputs=[...$('strategy-list').querySelectorAll('input[type="checkbox"]')],state={...previous,strategyIds:strategyInputs.length?strategyInputs.filter(input=>input.checked).map(input=>input.value):previous.strategyIds,knownStrategyIds:definitions.filter(def=>def.strategy.enabled!==false).map(def=>def.strategy.id),analysisStart:$('start-date').value||previous.analysisStart,analysisEnd:$('end-date').value||previous.analysisEnd,detailStrategy:$('detail-strategy').value||previous.detailStrategy,indicatorSelection:[...indicatorSelection],indicatorType:indicatorControlsInitialized?$('indicator-type').value:previous.indicatorType,indicatorStrategy:indicatorControlsInitialized?$('indicator-strategy').value:previous.indicatorStrategy,indicatorStart:indicatorControlsInitialized?$('indicator-start-date').value:previous.indicatorStart,indicatorEnd:indicatorControlsInitialized?$('indicator-end-date').value:previous.indicatorEnd,indicatorOverlays:indicatorControlsInitialized?[...$('indicator-overlays').querySelectorAll('input:checked')].map(input=>input.value):previous.indicatorOverlays,activeView:activeView||previous.activeView||($('indicators-view').classList.contains('offline-hidden')?'analysis':'indicators')};localStorage.setItem(uiStateKey,JSON.stringify(state));}catch{}}
const savedUiState=loadUiState();
if(Array.isArray(savedUiState.indicatorSelection))indicatorSelection=new Set(savedUiState.indicatorSelection);
if(Array.isArray(savedUiState.importedDefinitions)){const importedDefinitions=savedUiState.importedDefinitions.map(migrateStrategyDefinition),importedIds=new Set(importedDefinitions.map(def=>def?.strategy?.id).filter(Boolean));definitions=[...definitions.filter(def=>!importedIds.has(def.strategy.id)),...importedDefinitions];}
setupDashboard=async function(){
  const inputs=[...$('strategy-list').querySelectorAll('input[type="checkbox"]')],currentState=loadUiState(),hasSavedStrategyIds=Array.isArray(currentState.strategyIds);
  let knownIds;
  if(inputs.length)knownIds=new Set(inputs.map(input=>input.value));
  else if(Array.isArray(currentState.knownStrategyIds))knownIds=new Set(currentState.knownStrategyIds);
  else if(hasSavedStrategyIds){
    const cached=await loadPrecomputedResults(),imported=(currentState.importedDefinitions||[]).map(def=>def?.strategy?.id).filter(Boolean);
    knownIds=new Set([...Object.keys(cached),...imported,...currentState.strategyIds]);
  }else knownIds=new Set();
  const selectedIds=inputs.length?new Set(inputs.filter(input=>input.checked).map(input=>input.value)):new Set(hasSavedStrategyIds?currentState.strategyIds:definitions.map(def=>def.strategy.id)),originalSelectedDefinitions=selectedDefinitions;
  selectedDefinitions=()=>originalSelectedDefinitions().filter(def=>!knownIds.has(def.strategy.id)||selectedIds.has(def.strategy.id));
  try{await statePreservingDashboardSetup();}finally{selectedDefinitions=originalSelectedDefinitions;}
  for(const input of $('strategy-list').querySelectorAll('input[type="checkbox"]')){
    input.checked=!knownIds.has(input.value)||selectedIds.has(input.value);
    input.onchange=()=>{saveUiState();scheduleDashboardRun();};
  }
  if(currentState.detailStrategy&&dashboardResults.has(currentState.detailStrategy)){$('detail-strategy').value=currentState.detailStrategy;renderDetail();}
  $('detail-strategy').onchange=()=>{saveUiState();renderDetail();};
  saveUiState();
};
const importReadyDashboardSetup=setupDashboard;
setupDashboard=async function(){await importReadyDashboardSetup();$('import').onchange=async event=>{try{const def=parseYaml(await event.target.files[0].text());if(!def?.strategy?.id)throw Error('strategy.id가 필요합니다.');def.strategy.enabled=true;definitions=definitions.filter(item=>item.strategy.id!==def.strategy.id);definitions.push(def);const state=loadUiState(),imported=(state.importedDefinitions||[]).filter(item=>item?.strategy?.id!==def.strategy.id);localStorage.setItem(uiStateKey,JSON.stringify({...state,importedDefinitions:[...imported,def]}));await setupDashboard();$('status').textContent='YAML 전략을 비교 목록에 추가했습니다.';}catch(error){$('status').textContent=`YAML 오류: ${error.message}`;}finally{event.target.value='';}};};
const statefulIndicatorControls=setupIndicatorControls;
setupIndicatorControls=function(){const state=loadUiState(),type=$('indicator-type');if(state.indicatorType&&[...type.options].some(option=>option.value===state.indicatorType))type.value=state.indicatorType;statefulIndicatorControls();const strategy=$('indicator-strategy');if(Object.hasOwn(state,'indicatorStrategy')&&[...strategy.options].some(option=>option.value===state.indicatorStrategy))strategy.value=state.indicatorStrategy;if(state.indicatorStart)$('indicator-start-date').value=state.indicatorStart;if(state.indicatorEnd)$('indicator-end-date').value=state.indicatorEnd;if(Array.isArray(state.indicatorOverlays))for(const input of $('indicator-overlays').querySelectorAll('input'))input.checked=state.indicatorOverlays.includes(input.value);strategy.onchange=async()=>{saveUiState();await renderIndicators();};for(const input of [$('indicator-start-date'),$('indicator-end-date'),$('indicator-overlays')])input.addEventListener('change',saveUiState);$('indicator-panel-tabs').addEventListener('click',()=>setTimeout(saveUiState));$('indicator-matrix').addEventListener('change',()=>setTimeout(saveUiState));};
const viewRestoringDashboardSetup=setupDashboard;
setupDashboard=async function(){const restoreIndicators=loadUiState().activeView==='indicators';if(restoreIndicators){$('analysis-view').classList.add('offline-hidden');$('indicators-view').classList.remove('offline-hidden');$('analysis-tab').classList.remove('active');$('indicators-tab').classList.add('active');}await viewRestoringDashboardSetup();const analysisTab=$('analysis-tab'),indicatorsTab=$('indicators-tab'),showAnalysis=analysisTab.onclick,showIndicators=indicatorsTab.onclick;analysisTab.onclick=()=>{showAnalysis();saveUiState('analysis');};indicatorsTab.onclick=async()=>{await showIndicators();saveUiState('indicators');};if(restoreIndicators)await indicatorsTab.onclick();};
const productDisplayNames={'379810.KS':'KODEX 미국나스닥100','426030.KS':'TIME 미국나스닥100액티브','0015B0.KS':'KoAct 미국나스닥성장액티브','434060.KS':'KODEX TDF2050액티브','069500.KS':'KODEX 200 (069500.KS)','114100.KS':'KODEX 국고채 3년 (114100.KS)','148070.KS':'KOSEF 국고채 10년 (148070.KS)'};
function productDetailAssets(def,tickers){const displayTickers=tickers.filter(ticker=>!Object.values(FX_TICKER_BY_SUFFIX).includes(ticker)),source=def.source?definitions.find(item=>item.strategy.id===def.source):def,required=source?.assets?.required||[],products=def.products||{},items=[],byTicker=new Map(),claimed=new Set(),hiddenAssets=new Set();for(const asset of required){const entries=Object.entries(products[asset]||((displayTickers.includes(asset))?{[asset]:1}:{})).filter(([ticker])=>displayTickers.includes(ticker)),mapped=entries.map(([ticker])=>ticker);if(mapped.some(ticker=>ticker!==asset))hiddenAssets.add(asset);for(const [ticker,share] of entries){const item=byTicker.get(ticker)||{ticker,mappings:[]};if(!byTicker.has(ticker)){byTicker.set(ticker,item);items.push(item);}item.mappings.push({asset,share});claimed.add(ticker);}}for(const ticker of displayTickers)if(!claimed.has(ticker)&&!hiddenAssets.has(ticker))items.push({ticker,mappings:[{asset:ticker,share:1}]});return items;}
function productDetailLabel(item){const name=productDisplayNames[item.ticker]||item.ticker,mappings=item.mappings||[];if(mappings.length===1&&mappings[0].asset===item.ticker&&Math.abs(pct(mappings[0].share)-1)<1e-8)return name;return `${name} (${mappings.map(({asset,share})=>`${asset}${Math.abs(pct(share)-1)<1e-8?'':` ${percentText(pct(share)*100)}`}`).join(' · ')})`;}
function productDetailPayload(history,returns,items){return history.map((row,index)=>{const target=row.target&&typeof row.target==='object'?row.target:null,weights=row.preWeights&&typeof row.preWeights==='object'?row.preWeights:row.weights||{};return {date:row.date.replaceAll('-','.'),return:percentText(returns[index]),assets:items.map(item=>({name:productDetailLabel(item,weights),current:percentText(Number(weights[item.ticker]||0)*100),target:target?percentText(Number(target[item.ticker]||0)*100):null})),executionDays:target?row.executionDays:null,state:row.state||''};});}
const productMappedDetailRenderer=renderDetail;
renderDetail=function(){productMappedDetailRenderer();const id=$('detail-strategy').value,entry=dashboardResults.get(id);if(!entry)return;const [def,allHistory]=entry,history=visibleHistory(allHistory);if(!history.length)return;const base=history[0].value,returns=history.map(row=>(row.value/base-1)*100),items=productDetailAssets(def,Object.keys(history[0].weights||{})),payload=productDetailPayload(history,returns,items),traces=[];for(const item of items)traces.push({x:history.map(row=>row.date),y:history.map((row,index)=>Number(row.weights?.[item.ticker]||0)*returns[index]),name:productDetailLabel(item,history.at(-1).weights),stackgroup:'portfolio-return',mode:'lines',line:{width:.4},hoverinfo:'skip'});traces.push({x:history.map(row=>row.date),y:returns,name:'누적 수익률',showlegend:false,line:{color:'#182433',width:1.2},customdata:payload,hoverinfo:'none'});const hasEventMetadata=history.some(row=>row.target),events=history.map((row,index)=>({row,index})).filter(({row,index})=>hasEventMetadata?Boolean(row.target):(index>0&&items.some(item=>Math.abs(Number(row.weights?.[item.ticker]||0)-Number(history[index-1].weights?.[item.ticker]||0))>=.02)));if(events.length)traces.push({x:events.map(item=>item.row.date),y:events.map(item=>returns[item.index]),name:'리밸런싱',mode:'markers',marker:{symbol:'diamond',size:9,color:'#F23645',line:{color:'#fff',width:1}},hoverinfo:'skip'});const start=history[0].date,end=history.at(-1).date,layout=chartLayout('누적 수익률',{slider:true,selector:true,height:520,start,end});layout.xaxis.unifiedhovertitle={text:'%{x|%Y.%m.%d}'};Plotly.react('detail-plot',traces,layout,graphConfig);bindChartTooltip('detail-plot','detail');};
const indicatorHeaderLayoutStyle=document.createElement('style');
indicatorHeaderLayoutStyle.textContent='.research-indicator-controls>.card-header{display:block}.offline-toolbar-actions{align-self:flex-start;margin-top:26px}.offline-toolbar-actions>.btn{box-sizing:border-box;height:32px;padding:7px 10px;font-size:12px;font-weight:600;line-height:16px}@media(max-width:760px){.offline-toolbar-actions{margin-top:0}}';
document.head.append(indicatorHeaderLayoutStyle);
const rangeSelectorIndicatorRenderer=renderIndicators;
renderIndicators=async function(){
  await rangeSelectorIndicatorRenderer();
  const plot=$('indicator-plot');
  if(!plot?.data)return;
  const buttons=[{count:1,label:'1년',step:'year',stepmode:'backward'},{count:3,label:'3년',step:'year',stepmode:'backward'},{count:5,label:'5년',step:'year',stepmode:'backward'},{label:'전체',step:'all'}];
  const legendItems=plot.data.filter(trace=>trace.showlegend!==false&&trace.name).length,rows=Math.max(1,Math.ceil(legendItems/5)),height=Number(plot.layout.height||plot.clientHeight),margin=plot.layout.margin||{},plotHeight=Math.max(height-Number(margin.t||0)-Number(margin.b||0),1),legendY=Number(plot.layout.legend?.y||1),selectorY=legendY+(19*rows+24)/plotHeight;
  await Plotly.relayout(plot,{'xaxis.rangeselector':{buttons,x:0,xanchor:'left',y:selectorY,yanchor:'bottom',bgcolor:'rgba(106,109,120,.08)',activecolor:'rgba(41,98,255,.18)',bordercolor:'#dce1e7',borderwidth:1,font:{size:11}}});
};
const responsiveDetailHoverRenderer=renderDetail;
renderDetail=function(){
  responsiveDetailHoverRenderer();
  const plot=$('detail-plot');
  if(plot?.data)Plotly.relayout(plot,{hoverdistance:32,spikedistance:32});
};
renderDetail=function(){
  const id=$('detail-strategy').value,entry=dashboardResults.get(id);
  if(!entry)return;
  const [def,allHistory]=entry,history=visibleHistory(detailFxHistories.get(id)||allHistory);
  if(!history.length)return;
  const m=metrics(history),daily=history.slice(1).map((row,index)=>row.value/history[index].value-1),mean=daily.reduce((sum,value)=>sum+value,0)/Math.max(daily.length,1),variance=daily.reduce((sum,value)=>sum+(value-mean)**2,0)/Math.max(daily.length-1,1),vol=Math.sqrt(variance)*Math.sqrt(252),sharpe=vol?m.cagr/vol:0;
  const cards=[['CAGR','연환산 수익률',fmtPercent(m.cagr),'↗','positive'],['MDD','최대 낙폭',fmtPercent(m.mdd),'↕','negative'],['Sharpe','위험조정 성과',sharpe.toFixed(2),'⚖','primary'],['Total Return','전체 수익률',fmtPercent(m.total),'%','cyan']];
  $('detail-kpis').innerHTML=cards.map(([label,description,value,icon,color])=>`<div class="card research-kpi-card"><div class="research-kpi-body"><div><div class="research-kpi-label">${label}</div><div class="research-kpi-description">${description}</div><div class="research-kpi-value">${value}</div></div><span class="research-kpi-icon research-kpi-${color}">${icon}</span></div></div>`).join('');
  const base=history[0].value,returns=history.map(row=>(row.value/base-1)*100),items=productDetailAssets(def,Object.keys(history[0].weights||{})),payload=productDetailPayload(history,returns,items),traces=[];
  for(const item of items)traces.push({x:history.map(row=>row.date),y:history.map((row,index)=>Number(row.weights?.[item.ticker]||0)*returns[index]),name:productDetailLabel(item),stackgroup:'portfolio-return',mode:'lines',line:{width:.4},hoverinfo:'skip'});
  traces.push({x:history.map(row=>row.date),y:returns,name:'누적 수익률',showlegend:false,xaxis:'x',yaxis:'y',line:{color:'#182433',width:1.2},customdata:payload,hoverinfo:'none'});
  const events=history.map((row,index)=>({row,index})).filter(({row})=>Boolean(row.target));
  if(events.length)traces.push({x:events.map(item=>item.row.date),y:events.map(item=>returns[item.index]),name:'리밸런싱 실행',xaxis:'x',yaxis:'y',mode:'markers',marker:{symbol:'diamond',size:9,color:'#F23645',line:{color:'#fff',width:1}},hoverinfo:'skip'});
  const layout=chartLayout('누적 수익률',{slider:true,selector:true,height:620,start:history[0].date,end:history.at(-1).date});
  layout.uirevision=`detail:${$('start-date').value}:${$('end-date').value}`;
  layout.xaxis.rangeselector={...layout.xaxis.rangeselector,x:0,xanchor:'left'};
  layout.xaxis.unifiedhovertitle={text:'%{x|%Y.%m.%d}'};
  layout.hoverdistance=32;
  layout.spikedistance=32;
  $('detail-plot').style.height='620px';
  $('detail-plot').style.minHeight='620px';
  Plotly.react('detail-plot',traces,layout,graphConfig);
  bindChartTooltip('detail-plot','detail');
};
const detailFxHistories=new Map();
const detailUsdToggle=$('detail-remove-fx');
if(detailUsdToggle){
  detailUsdToggle.checked=false;
  detailUsdToggle.nextElementSibling.textContent='달러 기준으로 보기';
}
async function updateDetailFxView(){
  const toggle=$('detail-remove-fx'),id=$('detail-strategy').value,entry=dashboardResults.get(id);
  if(!toggle||!entry)return;
  if(!toggle.checked){detailFxHistories.delete(id);renderDetail();return;}
  toggle.disabled=true;
  try{
    const data=await downloadMissingData(entry[0]),history=runWithData(usdValuationDefinition(entry[0]),data,definitions);
    detailFxHistories.set(id,history);
    renderDetail();
  }catch(error){
    toggle.checked=false;
    $('status').textContent=`달러 기준 성과를 계산하지 못했습니다: ${error.message}`;
  }finally{toggle.disabled=false;}
}
function refreshAnalysisRange(){
  const entries=[...dashboardResults.values()],start=$('start-date').value,end=$('end-date').value,performance=[],drawdown=[];
  for(const [index,[def,allHistory]] of entries.entries()){
    const history=visibleHistory(allHistory);
    if(!history.length)continue;
    const base=history[0].value,returns=history.map(row=>(row.value/base-1)*100),peaks=[];
    let peak=-Infinity;
    for(const value of history.map(row=>row.value)){peak=Math.max(peak,value);peaks.push((value/peak-1)*100);}
    performance.push({x:history.map(row=>row.date),y:returns,customdata:history.map((row,itemIndex)=>({date:row.date.replaceAll('-','.'),name:def.strategy.name,value:percentText(returns[itemIndex])}))});
    drawdown.push({x:history.map(row=>row.date),y:peaks,customdata:history.map((row,itemIndex)=>({date:row.date.replaceAll('-','.'),name:def.strategy.name,value:percentText(peaks[itemIndex])}))});
  }
  const update=(id,traces)=>{
    const plot=$(id);
    if(!plot?.data||plot.data.length!==traces.length)return false;
    Plotly.restyle(plot,{x:traces.map(trace=>trace.x),y:traces.map(trace=>trace.y),customdata:traces.map(trace=>trace.customdata)});
    Plotly.relayout(plot,{'xaxis.range':[start,end],'xaxis.minallowed':start,'xaxis.maxallowed':end});
    return true;
  };
  if(!update('performance-plot',performance)||!update('drawdown-plot',drawdown))return renderAnalysis();
  drawSummary(entries);
  renderDetail();
}
const rangeOptimizedDashboardSetup=setupDashboard;
setupDashboard=async function(){
  await rangeOptimizedDashboardSetup();
  $('start-date').onchange=()=>{saveUiState();refreshAnalysisRange();};
  $('end-date').onchange=()=>{saveUiState();refreshAnalysisRange();};
};
const indicatorCatalog={
  price:['Close','MA20','MA55','MA120','MA200','EMA20','EMA55','EMA120','EMA200','BB_UPPER','BB_MIDDLE','BB_LOWER'],
  oscillator:['RSI14','DISPARITY60','MACD','MACD_SIGNAL','MACD_HIST','STOCH_K','STOCH_D'],
  risk:['DRAWDOWN20','DRAWDOWN60','DRAWDOWN120','ROC252','TR','ATR','ATR60','ATR_PCT','VOL20','VOL60','MDD252','VALUATION_SCORE']
};
const indicatorCatalogLabels={Close:'종가',MA20:'이동평균 20',MA55:'이동평균 55',MA120:'이동평균 120',MA200:'이동평균 200',EMA20:'EMA 20',EMA55:'EMA 55',EMA120:'EMA 120',EMA200:'EMA 200',BB_UPPER:'볼린저 상단',BB_MIDDLE:'볼린저 중심',BB_LOWER:'볼린저 하단',RSI14:'RSI 14',DISPARITY60:'60일 이격도',MACD:'MACD',MACD_SIGNAL:'MACD Signal',MACD_HIST:'MACD Histogram',STOCH_K:'Stochastic %K',STOCH_D:'Stochastic %D',DRAWDOWN20:'20일 낙폭',DRAWDOWN60:'60일 낙폭',DRAWDOWN120:'120일 낙폭',ROC252:'ROC 252일',TR:'TR',ATR:'ATR',ATR60:'ATR 60일',ATR_PCT:'ATR 비율',VOL20:'변동성 20일',VOL60:'변동성 60일',MDD252:'MDD 252일',VALUATION_SCORE:'합성 밸류에이션 점수 (비공식)'};
indicatorFields=panel=>indicatorCatalog[panel]||[];
indicatorPanel=field=>Object.entries(indicatorCatalog).find(([,fields])=>fields.includes(field))?.[0]||'risk';
function applyIndicatorCatalogLabels(){
  document.querySelectorAll('.research-indicator-row-toggle[data-field]').forEach(button=>{button.textContent=indicatorCatalogLabels[button.dataset.field]||button.dataset.field;});
  document.querySelectorAll('.research-indicator-selection-badge').forEach(badge=>{const [ticker,field]=badge.textContent.split(' · ');if(field)badge.textContent=`${ticker} · ${indicatorCatalogLabels[field]||field}`;});
}
const indicatorMatrix=$('indicator-matrix');
if(indicatorMatrix)new MutationObserver(applyIndicatorCatalogLabels).observe(indicatorMatrix,{childList:true,subtree:true});
const catalogIndicatorRenderer=renderIndicators;
renderIndicators=async function(){
  await catalogIndicatorRenderer();
  const plot=$('indicator-plot');
  if(!plot?.data)return;
  Plotly.restyle(plot,{name:plot.data.map(trace=>{const [ticker,field]=String(trace.meta?.tooltipName||'').split(' · ');return field?`${ticker} · ${indicatorCatalogLabels[field]||field}`:trace.name;})});
  applyIndicatorCatalogLabels();
};
const indicatorYearLabelRenderer=renderIndicators;
renderIndicators=async function(){
  await indicatorYearLabelRenderer();
  const plot=$('indicator-plot');
  if(!plot?.data)return;
  const height=Number(plot.layout.height||plot.clientHeight),margin=plot.layout.margin||{},plotHeight=Math.max(height-Number(margin.t||0)-Number(margin.b||0),1),labelY=-(.235*(520-96-78))/plotHeight;
  const annotations=(plot.layout.annotations||[]).map(annotation=>Number(annotation.y)<0?{...annotation,y:labelY}:annotation);
  await Plotly.relayout(plot,{annotations});
};
Object.assign(indicatorCatalogLabels,{Close:'\uc885\uac00',MA20:'\uc774\ub3d9\ud3c9\uade0 20',MA55:'\uc774\ub3d9\ud3c9\uade0 55',MA120:'\uc774\ub3d9\ud3c9\uade0 120',MA200:'\uc774\ub3d9\ud3c9\uade0 200',BB_UPPER:'\ubcfc\ub9b0\uc800 \uc0c1\ub2e8',BB_MIDDLE:'\ubcfc\ub9b0\uc800 \uc911\uc2ec',BB_LOWER:'\ubcfc\ub9b0\uc800 \ud558\ub2e8',DISPARITY60:'60\uc77c \uc774\uaca9\ub3c4',ROC252:'ROC 252\uc77c',ATR60:'ATR 60\uc77c',VOL60:'\ubcc0\ub3d9\uc131 60\uc77c',MDD252:'MDD 252\uc77c'});
function applyIndicatorCatalogLabels(){
  document.querySelectorAll('.research-indicator-row-toggle[data-field]').forEach(button=>{const label=indicatorCatalogLabels[button.dataset.field]||button.dataset.field;if(button.textContent!==label)button.textContent=label;});
  document.querySelectorAll('.research-indicator-selection-badge').forEach(badge=>{const [ticker,field]=badge.textContent.split(' \u00b7 ');const label=field?`${ticker} \u00b7 ${indicatorCatalogLabels[field]||field}`:'';if(label&&badge.textContent!==label)badge.textContent=label;});
}
const localizedCatalogIndicatorRenderer=renderIndicators;
renderIndicators=async function(){
  await localizedCatalogIndicatorRenderer();
  const plot=$('indicator-plot');
  if(!plot?.data)return;
  Plotly.restyle(plot,{name:plot.data.map(trace=>{const [ticker,field]=String(trace.meta?.tooltipName||'').split(' \u00b7 ');return field?`${ticker} \u00b7 ${indicatorCatalogLabels[field]||field}`:trace.name;})});
  applyIndicatorCatalogLabels();
};

function axisLayoutKey(axis){return axis==='x'?'xaxis':axis==='y'?'yaxis':`${axis[0]}axis${axis.slice(1)}`;}
function visibleAxisRange(values){const minimum=Math.min(...values),maximum=Math.max(...values),span=maximum-minimum||Math.max(Math.abs(maximum),1)*.08,padding=span*.08;return [minimum-padding,maximum+padding];}
function bindVisibleYAutoscale(plotId){const plot=$(plotId);if(!plot||plot.dataset.visibleYAutoscaleBound)return;plot.dataset.visibleYAutoscaleBound='1';plot.on('plotly_relayout',change=>{if(!Object.keys(change||{}).some(key=>/^xaxis\d*\.(range|autorange)/.test(key)))return;const updates={};for(const yaxis of [...new Set((plot.data||[]).map(trace=>trace.yaxis||'y'))]){const traces=(plot.data||[]).filter(trace=>(trace.yaxis||'y')===yaxis),values=[];for(const trace of traces){const xaxis=trace.xaxis||'x',range=plot._fullLayout?.[axisLayoutKey(xaxis)]?.range||plot.layout?.[axisLayoutKey(xaxis)]?.range;if(!range?.length)continue;const lower=Date.parse(range[0]),upper=Date.parse(range[1]);for(let index=0;index<(trace.x||[]).length;index++){const date=Date.parse(trace.x[index]),value=Number(trace.y?.[index]);if(Number.isFinite(date)&&Number.isFinite(value)&&date>=lower&&date<=upper)values.push(value);}}if(values.length)updates[`${axisLayoutKey(yaxis)}.range`]=visibleAxisRange(values);}if(Object.keys(updates).length)Plotly.relayout(plot,updates);});}
const visibleYDetailRenderer=renderDetail;
renderDetail=function(){visibleYDetailRenderer();bindVisibleYAutoscale('detail-plot');};
const visibleYIndicatorRenderer=renderIndicators;
renderIndicators=async function(){await visibleYIndicatorRenderer();bindVisibleYAutoscale('indicator-plot');};

const visibleYAnalysisRenderer=renderAnalysis;
renderAnalysis=function(){
  visibleYAnalysisRenderer();
  bindVisibleYAutoscale('performance-plot');
  bindVisibleYAutoscale('drawdown-plot');
};

let indicatorZoomPeriod=null;
const persistentIndicatorZoomRenderer=renderIndicators;
renderIndicators=async function(){
  const period=`${$('indicator-start-date').value}:${$('indicator-end-date').value}`;
  const plot=$('indicator-plot');
  const previousRange=indicatorZoomPeriod===period&&Array.isArray(plot?._fullLayout?.xaxis?.range)
    ? [...plot._fullLayout.xaxis.range]
    : null;
  await persistentIndicatorZoomRenderer();
  if(previousRange?.length===2)await Plotly.relayout($('indicator-plot'),{'xaxis.range':previousRange});
  indicatorZoomPeriod=period;
};

// Imported strategies use this single preparation path: download, calculate,
// validate and cache. It intentionally sits after the dashboard compatibility
// wrappers above, so existing chart behavior is left unchanged.
const BROWSER_ENGINE_VERSION = '2026-09-08.1';
const FX_TICKER_BY_SUFFIX = {'.KS': 'KRW=X', '.KQ': 'KRW=X'};
const TDF2050_PROXY_COMPONENT_WEIGHTS = {SPY:.4081,VXUS:.3339,BND:.258};
const KRW_ADJUSTED_SUFFIX = '_KRW';
const DATA_PROXY_TICKER_PATTERN = /^[A-Z0-9_.^=:-]{1,24}$/;
const KNOWN_MARKET_FIELDS = new Set(['open','high','low','close','volume','ma20','ma55','ma120','ma200','ema20','ema55','ema120','ema200','rsi14','disparity60','macd','macd_signal','macd_hist','stoch_k','stoch_d','roc5','roc20','roc40','roc60','roc90','roc120','roc252','ema20_slope5','ema200_slope20','drawdown20','drawdown60','drawdown120','tr','atr','atr60','atr_pct','bb_middle','bb_upper','bb_lower','vol20','vol60','mdd252','valuation_score']);
function definitionById(id, all=definitions){return all.find(item=>item?.strategy?.id===id)||null;}
function krwAdjustedBaseTicker(ticker){const value=String(ticker||'').toUpperCase();return value.endsWith(KRW_ADJUSTED_SUFFIX)?value.slice(0,-KRW_ADJUSTED_SUFFIX.length):null;}
function isKrwAdjustedTicker(ticker){return Boolean(krwAdjustedBaseTicker(ticker));}
function tickerFxRate(ticker){const upper=String(ticker||'').toUpperCase();return Object.entries(FX_TICKER_BY_SUFFIX).find(([suffix])=>upper.endsWith(suffix))?.[1]||null;}
function isDataProxyTicker(ticker){return DATA_PROXY_TICKER_PATTERN.test(String(ticker||'').toUpperCase())&&!isKrwAdjustedTicker(ticker)&&String(ticker).toUpperCase()!=='TDF2050_PROXY';}
function strategyDependencies(def, all=definitions, seen=new Set()){
  if(!def?.strategy?.id)throw Error('strategy.id가 필요합니다.');
  if(seen.has(def.strategy.id))throw Error(`source 전략이 순환 참조합니다: ${def.strategy.id}`);
  const nextSeen=new Set([...seen,def.strategy.id]),source=def.source?definitionById(def.source,all):def;
  if(!source)throw Error(`source 전략을 찾을 수 없습니다: ${def.source}`);
  const base=def.source?strategyDependencies(source,all,nextSeen):{calculation:source,tickers:[...(source.assets?.required||[]),...(source.assets?.observations||[])]};
  const tickers=[...new Set([...base.tickers,...Object.values(def.products||{}).flatMap(value=>Object.keys(value||{}))])];
  if(base.calculation.valuation?.fx_ticker)tickers.push(base.calculation.valuation.fx_ticker);
  for(const ticker of [...tickers]){const baseTicker=krwAdjustedBaseTicker(ticker);if(baseTicker)tickers.push(baseTicker,'KRW=X');const fx=tickerFxRate(ticker);if(fx)tickers.push(fx);}
  return {calculation:base.calculation,tickers:[...new Set(tickers)]};
}
function strategyTickers(def){return strategyDependencies(def).tickers;}
function rollingValues(values,index,period,fn){return index<period-1?NaN:fn(values.slice(index-period+1,index+1));}
function sampleStd(values){if(values.length<2)return NaN;const mean=values.reduce((sum,value)=>sum+value,0)/values.length;return Math.sqrt(values.reduce((sum,value)=>sum+(value-mean)**2,0)/(values.length-1));}
function emaValues(values,span){const alpha=2/(span+1),out=[];values.forEach((value,index)=>out.push(index?value*alpha+out[index-1]*(1-alpha):value));return out;}
function addStrategyIndicators(rows){
  const close=rows.map(row=>Number(row.Close)),high=rows.map(row=>Number(row.High)),low=rows.map(row=>Number(row.Low));
  const ema=Object.fromEntries([12,20,26,55,120,200].map(span=>[span,emaValues(close,span)]));
  const changes=close.map((value,index)=>index?value-close[index-1]:NaN),returns=close.map((value,index)=>index?value/close[index-1]-1:NaN);
  const tr=close.map((value,index)=>index===0?high[index]-low[index]:Math.max(high[index]-low[index],Math.abs(high[index]-close[index-1]),Math.abs(low[index]-close[index-1])));
  const atr=tr.map((_,index)=>rollingValues(tr,index,14,items=>items.reduce((sum,value)=>sum+value,0)/14));
  const macd=close.map((_,index)=>ema[12][index]-ema[26][index]),macdSignal=emaValues(macd,9);
  const stochK=close.map((value,index)=>{const lowest=rollingValues(low,index,14,items=>Math.min(...items)),highest=rollingValues(high,index,14,items=>Math.max(...items));return Number.isFinite(lowest)&&highest!==lowest?(value-lowest)/(highest-lowest)*100:NaN;});
  const drawdown252=close.map((value,index)=>{const peak=rollingValues(close,index,252,items=>Math.max(...items));return Number.isFinite(peak)?(value-peak)/peak:NaN;});
  rows.forEach((row,index)=>{
    const mean=period=>rollingValues(close,index,period,items=>items.reduce((sum,value)=>sum+value,0)/period),roc=period=>index<period?NaN:(close[index]/close[index-period]-1)*100;
    const gains=changes.slice(index-13,index+1).map(value=>Math.max(value,0)),losses=changes.slice(index-13,index+1).map(value=>Math.max(-value,0));
    const avgGain=index<14?NaN:gains.reduce((sum,value)=>sum+value,0)/14,avgLoss=index<14?NaN:losses.reduce((sum,value)=>sum+value,0)/14;
    const ma20=mean(20),sigma20=rollingValues(close,index,20,sampleStd);
    Object.assign(row,{MA20:ma20,MA55:mean(55),MA120:mean(120),MA200:mean(200),EMA20:ema[20][index],EMA55:ema[55][index],EMA120:ema[120][index],EMA200:ema[200][index],RSI14:Number.isFinite(avgGain)&&Number.isFinite(avgLoss)?(avgLoss===0?100:100-100/(1+avgGain/avgLoss)):NaN,DISPARITY60:index<59?NaN:close[index]/mean(60)*100,MACD:macd[index],MACD_SIGNAL:macdSignal[index],MACD_HIST:macd[index]-macdSignal[index],STOCH_K:stochK[index],STOCH_D:rollingValues(stochK,index,3,items=>items.reduce((sum,value)=>sum+value,0)/3),ROC5:roc(5),ROC20:roc(20),ROC40:roc(40),ROC60:roc(60),ROC90:roc(90),ROC120:roc(120),ROC252:roc(252),EMA20_SLOPE5:index<5?NaN:(ema[20][index]/ema[20][index-5]-1)*100,EMA200_SLOPE20:index<20?NaN:(ema[200][index]/ema[200][index-20]-1)*100,DRAWDOWN20:index<19?NaN:close[index]/rollingValues(close,index,20,items=>Math.max(...items))-1,DRAWDOWN60:index<59?NaN:close[index]/rollingValues(close,index,60,items=>Math.max(...items))-1,DRAWDOWN120:index<119?NaN:close[index]/rollingValues(close,index,120,items=>Math.max(...items))-1,TR:tr[index],ATR:atr[index],ATR60:rollingValues(atr,index,60,items=>items.reduce((sum,value)=>sum+value,0)/60),ATR_PCT:atr[index]/close[index],BB_MIDDLE:ma20,BB_UPPER:ma20+sigma20*2,BB_LOWER:ma20-sigma20*2,VOL20:rollingValues(returns,index,20,items=>sampleStd(items)*Math.sqrt(252)),VOL60:rollingValues(returns,index,60,items=>sampleStd(items)*Math.sqrt(252)),MDD252:rollingValues(drawdown252,index,252,items=>Math.min(...items))});
  });
  return rows;
}
function applyValuationData(def,data){const valuation=def.valuation;if(!valuation?.foreign_assets?.length)return data;const fxByDate=new Map((data[valuation.fx_ticker]||[]).map(row=>[row.Date,Number(row.Close)])),out={...data};if(!fxByDate.size)throw Error(`환율 데이터가 없습니다: ${valuation.fx_ticker}`);for(const ticker of valuation.foreign_assets){const source=data[ticker];if(!source)throw Error(`시장 데이터가 없습니다: ${ticker}`);let rate=NaN;out[ticker]=source.flatMap(row=>{const known=fxByDate.get(row.Date);if(Number.isFinite(known))rate=known;if(!Number.isFinite(rate))return[];const copy={...row};for(const field of ['Open','High','Low','Close'])copy[field]=Number(copy[field])*rate;return copy;});addStrategyIndicators(out[ticker]);}return out;}
function requiredMarketFields(def){const fields={},add=(ticker,...names)=>{fields[ticker]??=new Set();for(const name of names)fields[ticker].add(name);};for(const text of expressionStrings([def.variables,def.state,def.target,def.rebalance,def.execution]))for(const match of String(text).matchAll(/\b([A-Z][A-Z0-9_=X-]*)\.([A-Za-z_][\w]*)/g))add(match[1],match[2].toLowerCase());if(def.rotation){add(def.rotation.sleeve,'roc60','roc120','roc252');for(const candidate of def.rotation.candidates||[])add(candidate.ticker,'close','ema200','roc60','roc120','roc252','vol60');}return fields;}
function validateStrategy(def,all=definitions){const {calculation,tickers}=strategyDependencies(def,all);if(!Array.isArray(calculation.assets?.required)||!calculation.assets.required.length)throw Error('assets.required에 종목을 하나 이상 지정하세요.');if(!Array.isArray(calculation.target)||!calculation.target.length)throw Error('target에 목표 비중 규칙을 하나 이상 지정하세요.');if(calculation.rotation){const rotation=calculation.rotation,required=new Set(calculation.assets.required);if(!required.has(rotation.sleeve))throw Error('rotation.sleeve는 assets.required에 있어야 합니다.');if(!Array.isArray(rotation.candidates)||!rotation.candidates.length)throw Error('rotation.candidates가 필요합니다.');if(!['daily','weekly','monthly','quarterly'].includes(rotation.check||'monthly'))throw Error('rotation.check 주기가 올바르지 않습니다.');for(const candidate of rotation.candidates)if(!required.has(candidate.ticker)||!candidate.group||!['BOND','EQUITY','GOLD'].includes(candidate.asset_class))throw Error('rotation 후보의 ticker, group, asset_class를 확인하세요.');}const fields=requiredMarketFields(calculation),unsupported=[];for(const [ticker,names] of Object.entries(fields))for(const field of names)if(!KNOWN_MARKET_FIELDS.has(field))unsupported.push(`${ticker}.${field}`);if(unsupported.length)throw Error(`지원하지 않는 지표: ${unsupported.join(', ')}`);const unknown=Object.keys(def.products||{}).filter(ticker=>!calculation.assets.required.includes(ticker));if(unknown.length)throw Error(`products에 source 전략의 종목이 아닌 값이 있습니다: ${unknown.join(', ')}`);return {calculation,tickers,fields};}
function openCache(){return new Promise((resolve,reject)=>{const request=indexedDB.open('investment-strategy-data',3);request.onupgradeneeded=()=>{const db=request.result;for(const name of ['prices','market-data','strategy-results'])if(!db.objectStoreNames.contains(name))db.createObjectStore(name);};request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}
async function cachedResult(key){const db=await openCache();return new Promise((resolve,reject)=>{const request=db.transaction('strategy-results').objectStore('strategy-results').get(key);request.onsuccess=()=>resolve(request.result||null);request.onerror=()=>reject(request.error);});}
async function saveCachedResult(key,value){const db=await openCache();return new Promise((resolve,reject)=>{const request=db.transaction('strategy-results','readwrite').objectStore('strategy-results').put(value,key);request.onsuccess=()=>resolve();request.onerror=()=>reject(request.error);});}
function refreshIndicatorTickerCatalog(data){const select=$('indicator-ticker');if(select)select.innerHTML=Object.keys(data).sort().map(ticker=>`<option value="${ticker}">${ticker}</option>`).join('');if(indicatorControlsInitialized)setupIndicatorControls();}
async function loadCachedTickerData(data,tickers){for(const ticker of tickers)if(!data[ticker]){const cached=await cachedPrices(ticker);if(cached)data[ticker]=addStrategyIndicators(cached.rows||cached);}}
function latestCachedDate(rows){return rows.reduce((latest,row)=>row.Date&&row.Date>latest?row.Date:latest,'');}
function earliestCachedDate(rows){return rows.reduce((earliest,row)=>row.Date&&(!earliest||row.Date<earliest)?row.Date:earliest,'');}
function mergeTickerRows(existing,incoming){const merged=new Map((existing||[]).map(row=>[row.Date,row]));for(const row of incoming)merged.set(row.Date,row);return addStrategyIndicators([...merged.values()].sort((left,right)=>left.Date.localeCompare(right.Date)));}
async function fetchProxyTickerData(data,tickers,historyStart=null){const groups=new Map();for(const ticker of [...new Set(tickers.filter(isDataProxyTicker))]){const rows=data[ticker]||[],earliest=earliestCachedDate(rows),start=historyStart&&(!earliest||earliest>historyStart)?historyStart:latestCachedDate(rows)||'2010-01-01';(groups.get(start)||groups.set(start,[]).get(start)).push(ticker);}if(!groups.size)return;if(!proxyUrl){const unavailable=[...groups.values()].flat().filter(ticker=>!data[ticker]);if(unavailable.length)throw Error(`시장 데이터가 없습니다: ${unavailable.join(', ')}`);return;}for(const [startDate,group] of groups)for(let offset=0;offset<group.length;offset+=20){const requested=group.slice(offset,offset+20),endpoint=new URL('/prices',proxyUrl);endpoint.searchParams.set('tickers',requested.join(','));endpoint.searchParams.set('start',startDate);endpoint.searchParams.set('runtime',BROWSER_ENGINE_VERSION);try{const response=await fetch(endpoint,{cache:'no-store'}),payload=await response.json();if(!response.ok)throw Error(payload.error||'시장 데이터 다운로드에 실패했습니다.');for(const [ticker,rows] of Object.entries(payload.data||{})){const incoming=rows.map(row=>({Date:row.date,Open:row.open,High:row.high,Low:row.low,Close:row.close,Volume:row.volume,RawClose:row.raw_close??row.close,Dividends:row.dividends??0})),merged=mergeTickerRows(data[ticker],incoming);data[ticker]=merged;await saveCachedPrices(ticker,merged);}}catch(error){const unavailable=requested.filter(ticker=>!(data[ticker]||[]).length);if(unavailable.length){const message=error instanceof TypeError&&/fetch/i.test(error.message)?`데이터 프록시(${endpoint.origin})에 연결할 수 없습니다. Cloudflare Worker 배포 주소와 상태를 확인하세요.`:error instanceof Error?error.message:'시장 데이터 다운로드에 실패했습니다.';throw Error(message);}console.warn('시장 데이터 갱신이 지연되어 저장된 데이터를 사용합니다.',{tickers:requested,error:error instanceof Error?error.message:String(error)});}}}

// 22번 전략의 네 지표 합성점수를 월말에 확정하고 다음 달부터 사용한다.
function addCompositeValuationScore(data){
  const qqq=[...(data.QQQ||[])].sort((a,b)=>a.Date.localeCompare(b.Date)),align=rows=>{const sorted=[...(rows||[])].sort((a,b)=>a.Date.localeCompare(b.Date)),out=[];let index=0,last=null;for(const row of qqq){while(index<sorted.length&&sorted[index].Date<=row.Date)last=sorted[index++];out.push(last);}return out;};
  if(!qqq.length||!data.SPY?.length||!data.BIL?.length)throw Error('합성 밸류에이션에는 QQQ, SPY, BIL 데이터가 필요합니다.');
  const spy=align(data.SPY),bil=align(data.BIL),qClose=qqq.map(row=>Number(row.Close)),spyClose=spy.map(row=>Number(row?.Close)),bilClose=bil.map(row=>Number(row?.Close));
  const lower=(values,target)=>{let low=0,high=values.length;while(low<high){const middle=(low+high)>>1;if(values[middle]<target)low=middle+1;else high=middle;}return low;},upper=(values,target)=>{let low=0,high=values.length;while(low<high){const middle=(low+high)>>1;if(values[middle]<=target)low=middle+1;else high=middle;}return low;};
  const rolling=(values,window,minimum,selector)=>{const sorted=[],out=Array(values.length).fill(NaN);for(let index=0;index<values.length;index++){const value=values[index];if(Number.isFinite(value))sorted.splice(lower(sorted,value),0,value);const expired=index>=window?values[index-window]:NaN;if(Number.isFinite(expired))sorted.splice(lower(sorted,expired),1);if(Number.isFinite(value)&&sorted.length>=minimum)out[index]=selector(sorted,value);}return out;};
  const median=values=>rolling(values,756,504,sorted=>{const middle=sorted.length>>1;return sorted.length%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2;}),percentile=values=>rolling(values,1260,504,(sorted,value)=>upper(sorted,value)/sorted.length);
  const relative=qClose.map((value,index)=>Math.log(value/spyClose[index])),relativeMedian=median(relative),qqqMedian=median(qClose),relativeRank=percentile(relative.map((value,index)=>value-relativeMedian[index])),stretchRank=percentile(qClose.map((value,index)=>value/qqqMedian[index]-1)),rateRank=percentile(bilClose.map((value,index)=>index<63?NaN:(value/bilClose[index-63])**4-1));
  const dividendYield=Array(qqq.length).fill(NaN);let start=0,sum=0;for(let index=0;index<qqq.length;index++){sum+=Number(qqq[index].Dividends||0);const cutoff=Date.parse(`${qqq[index].Date}T00:00:00Z`)-365*86400000;while(start<index&&Date.parse(`${qqq[start].Date}T00:00:00Z`)<=cutoff)sum-=Number(qqq[start++].Dividends||0);const raw=Number(qqq[index].RawClose??qqq[index].Close);dividendYield[index]=raw>0?sum/raw:NaN;}
  const dividendRank=percentile(dividendYield),score=qqq.map((_,index)=>(relativeRank[index]*.25+stretchRank[index]*.25+rateRank[index]*.35+(1-dividendRank[index])*.15)*100);let month='',prior=NaN,active=NaN;
  data.QQQ=qqq.map((row,index)=>{const next=row.Date.slice(0,7);if(next!==month){active=prior;month=next;}prior=score[index];return{...row,VALUATION_SCORE:active};});return data;
}
function buildTdf2050Proxy(components){const tickers=Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS),byDate=Object.fromEntries(tickers.map(ticker=>[ticker,new Map(components[ticker].map(row=>[row.Date,row]))])),dates=[...byDate[tickers[0]].keys()].filter(date=>tickers.every(ticker=>byDate[ticker].has(date))).sort(),target={...TDF2050_PROXY_COMPONENT_WEIGHTS},weights={...target},rows=[];let previous=null,previousValue=100,previousMonth='';for(let index=1;index<dates.length;index++){const date=dates[index],market=Object.fromEntries(tickers.map(ticker=>[ticker,byDate[ticker].get(dates[index-1])])),month=date.slice(0,7);if(!previous){rows.push({Date:date,Open:100,High:100,Low:100,Close:100,Volume:0});previous=market;previousMonth=month;continue;}if(month!==previousMonth)Object.assign(weights,target);const value=field=>previousValue*tickers.reduce((sum,ticker)=>sum+weights[ticker]*Number(market[ticker][field])/Number(previous[ticker].Close),0),open=value('Open'),close=value('Close'),high=Math.max(value('High'),open,close),low=Math.min(value('Low'),open,close);rows.push({Date:date,Open:open,High:high,Low:low,Close:close,Volume:0});const contributions=Object.fromEntries(tickers.map(ticker=>[ticker,weights[ticker]*Number(market[ticker].Close)/Number(previous[ticker].Close)])),total=Object.values(contributions).reduce((sum,value)=>sum+value,0);for(const ticker of tickers)weights[ticker]=contributions[ticker]/total;previous=market;previousValue=close;previousMonth=month;}return rows;}
async function ensureTdf2050Proxy(data,tickers){if(!tickers.some(ticker=>ticker==='TDF2050_PROXY'||krwAdjustedBaseTicker(ticker)==='TDF2050_PROXY'))return;const components=Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS),unavailable=components.filter(ticker=>!data[ticker]);if(unavailable.length)throw Error(`TDF2050_PROXY 구성 데이터를 찾을 수 없습니다: ${unavailable.join(', ')}`);data.TDF2050_PROXY=addStrategyIndicators(buildTdf2050Proxy(data));await saveCachedPrices('TDF2050_PROXY',data.TDF2050_PROXY);}
function buildKrwAdjustedAsset(base,fx){const fxByDate=new Map(fx.map(row=>[row.Date,Number(row.Close)])),rows=[];let lastFx=NaN;for(const row of [...base].sort((left,right)=>left.Date.localeCompare(right.Date))){const known=fxByDate.get(row.Date);if(Number.isFinite(known))lastFx=known;if(!Number.isFinite(lastFx))continue;rows.push({Date:row.Date,Open:Number(row.Open)*lastFx,High:Number(row.High)*lastFx,Low:Number(row.Low)*lastFx,Close:Number(row.Close)*lastFx,Volume:Number(row.Volume)||0});}return rows;}
async function ensureKrwAdjustedAssets(data,tickers){for(const ticker of tickers.filter(isKrwAdjustedTicker)){const baseTicker=krwAdjustedBaseTicker(ticker),base=data[baseTicker],fx=data['KRW=X'];if(!base||!fx)throw Error(`${ticker}의 원자산 또는 USD/KRW 데이터를 찾을 수 없습니다.`);data[ticker]=addStrategyIndicators(buildKrwAdjustedAsset(base,fx));await saveCachedPrices(ticker,data[ticker]);}}
async function downloadMissingData(def){const data=await loadData(),{tickers,fields}=validateStrategy(def),needsTdf=tickers.some(ticker=>ticker==='TDF2050_PROXY'||krwAdjustedBaseTicker(ticker)==='TDF2050_PROXY'),needsComposite=fields.QQQ?.has('valuation_score'),tdfComponents=needsTdf?Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS):[],refreshTickers=[...new Set([...tickers,...tdfComponents])];await loadCachedTickerData(data,refreshTickers);await fetchProxyTickerData(data,refreshTickers,needsComposite?'2004-01-01':null);if(needsComposite){addCompositeValuationScore(data);await saveCachedPrices('QQQ',data.QQQ);}await ensureTdf2050Proxy(data,tickers);await ensureKrwAdjustedAssets(data,tickers);const unavailable=tickers.filter(ticker=>!data[ticker]&&!isDataProxyTicker(ticker));if(unavailable.length)throw Error(`시장 데이터가 없습니다: ${unavailable.join(', ')}`);const incomplete=tickers.filter(ticker=>!data[ticker]);if(incomplete.length)throw Error(`시세를 찾을 수 없습니다: ${incomplete.join(', ')}`);refreshIndicatorTickerCatalog(data);return data;}
function portfolioPrices(market,field='close',removeFx=true,tickers=Object.keys(market)){const prices={};for(const ticker of tickers){const row=market[ticker];if(!row)continue;const fxTicker=tickerFxRate(ticker),fx=removeFx&&fxTicker?Number(market[fxTicker]?.[field]):1,price=Number(row[field])/(Number.isFinite(fx)&&fx>0?fx:1);if(Number.isFinite(price))prices[ticker]=price;}return prices;}
function strategyHoldingTickers(def,all=definitions){if(!def.source)return [...(def.assets?.required||[])];const source=definitionById(def.source,all);if(!source)throw Error(`Unknown source strategy: ${def.source}`);return [...new Set((source.assets?.required||[]).flatMap(asset=>Object.keys(def.products?.[asset]||{[asset]:1})))];}
function resolve(def,all,removeFx=true){if(!def.source)return new Declarative(def);const source=all[def.source];if(!source)throw Error(`source 전략을 찾을 수 없습니다: ${def.source}`);const runtime=new Declarative(source);return {step(date,market,portfolio){const actual=portfolio.weights(portfolioPrices(market,'close',removeFx)),virtual={weights:()=>Object.fromEntries((source.assets.required||[]).map(asset=>[asset,Object.keys(def.products?.[asset]||{[asset]:1}).reduce((sum,product)=>sum+(actual[product]||0),0)]))},signal=runtime.step(date,market,virtual),target={};for(const [asset,weight] of Object.entries(signal.target))for(const [product,share] of Object.entries(def.products?.[asset]||{[asset]:1}))target[product]=(target[product]||0)+weight*pct(share);return {...signal,target};}};}
function runWithData(def,data,availableDefinitions=definitions,{removeFx=true}={}){const validation=validateStrategy(def,availableDefinitions),all=Object.fromEntries(availableDefinitions.map(item=>[item.strategy.id,item]));all[def.strategy.id]=def;const holdings=strategyHoldingTickers(def,availableDefinitions),valuationRemoveFx=holdings.some(isKrwAdjustedTicker)?false:removeFx,rows=recordsFor(validation.tickers,data).filter(row=>validation.tickers.every(ticker=>[...(validation.fields[ticker]||[])].every(field=>Number.isFinite(Number(row.market[ticker][field])))));if(!rows.length)throw Error('지표 초기 계산 구간 이후에 공통 거래일이 남아 있지 않습니다.');const runtime=resolve(def,all,valuationRemoveFx),portfolio=new Portfolio();for(const row of rows){portfolio.update(portfolioPrices(row.market,'open',valuationRemoveFx,holdings),row.date);const signal=runtime.step(row.date,row.market,portfolio);if(!portfolio.history.length)portfolio.start(signal.target,signal.days,row.date,signal.reason||'INITIAL');else if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,signal.reason||'RULE');portfolio.record(row.date,portfolioPrices(row.market,'close',valuationRemoveFx,holdings),signal.state);}const events=new Map(portfolio.rebalances.map(event=>[event.ExecutionDate||event.Date,event]));return portfolio.history.map(row=>{const event=events.get(row.date);return event?{...row,target:event.Target,preWeights:event.PreWeights||null,executionDays:event.ExecutionDays,reason:event.Reason}:row;});}
const rawRunWithData=runWithData;
function runWithMixedValuation(def,data,availableDefinitions=definitions){const validation=validateStrategy(def,availableDefinitions),all=Object.fromEntries(availableDefinitions.map(item=>[item.strategy.id,item]));all[def.strategy.id]=def;const holdings=strategyHoldingTickers(def,availableDefinitions),valued=applyValuationData(validation.calculation,data),valuedByDate=new Map(recordsFor(validation.tickers,valued).map(row=>[row.date,row.market])),rows=recordsFor(validation.tickers,data).filter(row=>validation.tickers.every(ticker=>[...(validation.fields[ticker]||[])].every(field=>Number.isFinite(Number(row.market[ticker][field])))));if(!rows.length)throw Error('지표 초기 계산 구간 이후에 공통 거래일이 남아 있지 않습니다.');const runtime=resolve(def,all,false),portfolio=new Portfolio();for(const row of rows){const valuationMarket=valuedByDate.get(row.date);if(!valuationMarket)continue;portfolio.update(portfolioPrices(valuationMarket,'open',false,holdings),row.date);const signal=runtime.step(row.date,row.market,portfolio);if(!portfolio.history.length)portfolio.start(signal.target,signal.days,row.date,signal.reason||'INITIAL');else if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,signal.reason||'RULE');portfolio.record(row.date,portfolioPrices(valuationMarket,'close',false,holdings),signal.state);}const events=new Map(portfolio.rebalances.map(event=>[event.ExecutionDate||event.Date,event]));return portfolio.history.map(row=>{const event=events.get(row.date);return event?{...row,target:event.Target,preWeights:event.PreWeights||null,executionDays:event.ExecutionDays,reason:event.Reason}:row;});}
runWithData=function(def,data,availableDefinitions=definitions,options={}){const calculation=strategyDependencies(def,availableDefinitions).calculation;if(calculation.valuation?.signal_currency==='LOCAL')return runWithMixedValuation(def,data,availableDefinitions);const valuationOptions=calculation.valuation?{...options,removeFx:false}:options;return rawRunWithData(def,applyValuationData(calculation,data),availableDefinitions,valuationOptions);};
async function resultKey(def,data){const market=validateStrategy(def).tickers.map(ticker=>[ticker,(data[ticker]||[]).map(row=>[row.Date,row.Open,row.High,row.Low,row.Close,row.Volume,row.RawClose,row.Dividends,row.VALUATION_SCORE])]);const bytes=new TextEncoder().encode(JSON.stringify([BROWSER_ENGINE_VERSION,bundle.market_data_version||'',def,market]));const digest=await crypto.subtle.digest('SHA-256',bytes);return [...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');}
function valuationWeightPortfolio(portfolio,valuationMarket,holdings){const prices=portfolioPrices(valuationMarket,'close',false,holdings);return{weights:()=>portfolio.weights(prices)};}
runWithMixedValuation=function(def,data,availableDefinitions=definitions){
  const validation=validateStrategy(def,availableDefinitions),all=Object.fromEntries(availableDefinitions.map(item=>[item.strategy.id,item]));
  all[def.strategy.id]=def;
  const holdings=strategyHoldingTickers(def,availableDefinitions),valued=applyValuationData(validation.calculation,data),valuedByDate=new Map(recordsFor(validation.tickers,valued).map(row=>[row.date,row.market])),rows=recordsFor(validation.tickers,data).filter(row=>validation.tickers.every(ticker=>[...(validation.fields[ticker]||[])].every(field=>Number.isFinite(Number(row.market[ticker][field]))))),runtime=resolve(def,all,false),portfolio=new Portfolio();
  if(!rows.length)throw Error('지표 초기 계산 구간 이후에 공통 거래일이 남아 있지 않습니다.');
  for(const row of rows){const valuationMarket=valuedByDate.get(row.date);if(!valuationMarket)continue;portfolio.update(portfolioPrices(valuationMarket,'open',false,holdings),row.date);const signal=runtime.step(row.date,row.market,valuationWeightPortfolio(portfolio,valuationMarket,holdings));if(!portfolio.history.length)portfolio.start(signal.target,signal.days,row.date,signal.reason||'INITIAL');else if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,signal.reason||'RULE');portfolio.record(row.date,portfolioPrices(valuationMarket,'close',false,holdings),signal.state);}
  const events=new Map(portfolio.rebalances.map(event=>[event.ExecutionDate||event.Date,event]));
  return portfolio.history.map(row=>{const event=events.get(row.date);return event?{...row,target:event.Target,preWeights:event.PreWeights||null,executionDays:event.ExecutionDays,reason:event.Reason}:row;});
};
function rotationOptionalTickers(def,availableDefinitions=definitions){
  const calculation=strategyDependencies(def,availableDefinitions).calculation;
  return new Set((calculation.rotation?.candidates||[]).map(candidate=>String(candidate.ticker)));
}
// A rotation candidate participates only after its own price and indicators
// exist. A shorter candidate history must not delay the strategy itself.
recordsFor=function(tickers,data,optionalTickers=new Set()){
  const byTicker={};
  for(const ticker of tickers){if(!data[ticker])throw Error(`내장 데이터에 ${ticker}가 없습니다.`);byTicker[ticker]=new Map(data[ticker].map(row=>[row.Date,row]));}
  const required=tickers.filter(ticker=>!optionalTickers.has(ticker));
  if(!required.length)throw Error('기준 거래일을 제공할 필수 종목이 없습니다.');
  const dates=[...byTicker[required[0]].keys()].filter(date=>required.every(ticker=>byTicker[ticker].has(date))),lastOptional={};
  return dates.map(date=>{
    const market={};
    for(const ticker of tickers){
      let row=byTicker[ticker].get(date);
      if(optionalTickers.has(ticker)){
        if(row)lastOptional[ticker]=row;
        else row=lastOptional[ticker];
      }
      if(row)market[ticker]=marketRow(row);
    }
    return {date,market};
  });
};
function validateCalculatedHistory(history){
  const invalid=history.find(row=>!Number.isFinite(Number(row.value))||Object.values(row.weights||{}).some(weight=>!Number.isFinite(Number(weight))));
  if(invalid)throw Error(`포트폴리오 계산 값이 유효하지 않습니다: ${invalid.date}`);
  return history;
}
runWithData=function(def,data,availableDefinitions=definitions,options={}){
  const validation=validateStrategy(def,availableDefinitions),calculation=validation.calculation;
  if(calculation.valuation?.signal_currency==='LOCAL')return runWithMixedValuation(def,data,availableDefinitions);
  const all=Object.fromEntries(availableDefinitions.map(item=>[item.strategy.id,item]));all[def.strategy.id]=def;
  const holdings=strategyHoldingTickers(def,availableDefinitions),removeFx=calculation.valuation?.currency==='USD'?true:calculation.valuation?false:(options.removeFx??true),optional=rotationOptionalTickers(def,availableDefinitions);
  const valued=applyValuationData(calculation,data),rows=recordsFor(validation.tickers,valued,optional).filter(row=>validation.tickers.every(ticker=>optional.has(ticker)||[...(validation.fields[ticker]||[])].every(field=>Number.isFinite(Number(row.market[ticker]?.[field])))));
  if(!rows.length)throw Error('지표 초기 계산 구간 이후에 공통 거래일이 남아 있지 않습니다.');
  const runtime=resolve(def,all,removeFx),portfolio=new Portfolio();
  for(const row of rows){portfolio.update(portfolioPrices(row.market,'open',removeFx,holdings),row.date);const signal=runtime.step(row.date,row.market,portfolio);if(!portfolio.history.length)portfolio.start(signal.target,signal.days,row.date,signal.reason||'INITIAL');else if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,signal.reason||'RULE');portfolio.record(row.date,portfolioPrices(row.market,'close',removeFx,holdings),signal.state);}
  const events=new Map(portfolio.rebalances.map(event=>[event.ExecutionDate||event.Date,event]));
  return validateCalculatedHistory(portfolio.history.map(row=>{const event=events.get(row.date);return event?{...row,target:event.Target,preWeights:event.PreWeights||null,executionDays:event.ExecutionDays,reason:event.Reason}:row;}));
};
runWithMixedValuation=function(def,data,availableDefinitions=definitions){
  const validation=validateStrategy(def,availableDefinitions),all=Object.fromEntries(availableDefinitions.map(item=>[item.strategy.id,item]));all[def.strategy.id]=def;
  const holdings=strategyHoldingTickers(def,availableDefinitions),optional=rotationOptionalTickers(def,availableDefinitions),valued=applyValuationData(validation.calculation,data),valuedByDate=new Map(recordsFor(validation.tickers,valued,optional).map(row=>[row.date,row.market])),rows=recordsFor(validation.tickers,data,optional).filter(row=>validation.tickers.every(ticker=>optional.has(ticker)||[...(validation.fields[ticker]||[])].every(field=>Number.isFinite(Number(row.market[ticker]?.[field])))));
  if(!rows.length)throw Error('지표 초기 계산 구간 이후에 공통 거래일이 남아 있지 않습니다.');
  const runtime=resolve(def,all,false),portfolio=new Portfolio();
  for(const row of rows){const valuationMarket=valuedByDate.get(row.date);if(!valuationMarket)continue;portfolio.update(portfolioPrices(valuationMarket,'open',false,holdings),row.date);const signal=runtime.step(row.date,row.market,valuationWeightPortfolio(portfolio,valuationMarket,holdings));if(!portfolio.history.length)portfolio.start(signal.target,signal.days,row.date,signal.reason||'INITIAL');else if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,signal.reason||'RULE');portfolio.record(row.date,portfolioPrices(valuationMarket,'close',false,holdings),signal.state);}
  const events=new Map(portfolio.rebalances.map(event=>[event.ExecutionDate||event.Date,event]));
  return validateCalculatedHistory(portfolio.history.map(row=>{const event=events.get(row.date);return event?{...row,target:event.Target,preWeights:event.PreWeights||null,executionDays:event.ExecutionDays,reason:event.Reason}:row;}));
};
function backgroundRun(def,data,all){
  if(validateStrategy(def,all).calculation.valuation)return Promise.resolve(runWithData(def,data,all));
  if(!window.Worker)return Promise.resolve(runWithData(def,data,all));
  const runtime=[
    `const FX_TICKER_BY_SUFFIX=${JSON.stringify(FX_TICKER_BY_SUFFIX)};`,
    `const KRW_ADJUSTED_SUFFIX=${JSON.stringify(KRW_ADJUSTED_SUFFIX)};`,
    `const KNOWN_MARKET_FIELDS=new Set(${JSON.stringify([...KNOWN_MARKET_FIELDS])});`,
    definitionById,tickerFxRate,krwAdjustedBaseTicker,isKrwAdjustedTicker,strategyDependencies,expressionStrings,requiredMarketFields,validateStrategy,pct,normalizeExpr,evaluate,period,recordsFor,marketRow,stateName,rotationNumber,sameRotationMix,selectRotation,rotationExplanation,installRotation,installRotationStability,installRotationSuspension,installFinalTargetRebalance,applyRotationRiskCap,installRotationRiskCap,addStrategyIndicators,applyValuationData,portfolioPrices,strategyHoldingTickers,resolve,Portfolio,Declarative,rotationOptionalTickers,validateCalculatedHistory,runWithData
  ].map(item=>typeof item==='function'?item.toString():item).join('\n');
  return new Promise((resolve,reject)=>{
    const source=`${runtime}\ninstallRotation(Declarative);\ninstallRotationStability(Declarative);\ninstallRotationSuspension(Declarative);\ninstallFinalTargetRebalance(Declarative);\ninstallRotationRiskCap(Declarative);\nself.onmessage=event=>{try{self.postMessage({history:runWithData(event.data.def,event.data.data,event.data.all)});}catch(error){self.postMessage({error:error.message||String(error)});}};`;
    const url=URL.createObjectURL(new Blob([source],{type:'text/javascript'})),worker=new Worker(url);
    const finish=()=>{worker.terminate();URL.revokeObjectURL(url);};
    worker.onmessage=event=>{finish();event.data.error?reject(Error(event.data.error)):resolve(event.data.history);};
    worker.onerror=event=>{finish();reject(event.error||Error(event.message));};
    worker.postMessage({def,data,all});
  });
}
async function run(def){const data=await downloadMissingData(def),key=await resultKey(def,data),cached=await cachedResult(key).catch(()=>null);if(cached?.history){try{return validateCalculatedHistory(cached.history);}catch(error){console.warn('유효하지 않은 계산 캐시를 다시 계산합니다.',error);}}let history;try{history=await backgroundRun(def,data,definitions);}catch(error){console.warn('Worker calculation unavailable; using the main thread.',error);history=runWithData(def,data);}validateCalculatedHistory(history);saveCachedResult(key,{history,savedAt:new Date().toISOString()}).catch(()=>{});return history;}
globalThis.OfflineStrategyRuntime={...globalThis.OfflineStrategyRuntime,addStrategyIndicators,validateStrategy,strategyDependencies,runWithData};
function indicatorTickerData(data){
  const visible=new Set();
  for(const def of definitions.filter(item=>item?.strategy?.enabled!==false)){
    try{
      for(const ticker of strategyDependencies(def).tickers){
        if(!ticker.endsWith('=X'))visible.add(ticker);
      }
    }catch{}
  }
  for(const key of indicatorSelection){
    const ticker=key.split('|')[0];
    if(ticker)visible.add(ticker);
  }
  if(!visible.size&&data.QQQ)visible.add('QQQ');
  return Object.fromEntries([...visible].sort().map(ticker=>[ticker,data[ticker]||[]]));
}
const unfilteredIndicatorSetup=setupIndicatorControls;
setupIndicatorControls=function(){
  const allMarketData=marketData;
  marketData=indicatorTickerData(allMarketData);
  try{unfilteredIndicatorSetup();}
  finally{marketData=allMarketData;}
};
async function clearBrowserCache(){
  const db=await openCache(),stores=['prices','market-data','strategy-results'].filter(name=>db.objectStoreNames.contains(name));
  if(!stores.length){db.close();return;}
  await new Promise((resolve,reject)=>{
    const transaction=db.transaction(stores,'readwrite');
    for(const name of stores)transaction.objectStore(name).clear();
    transaction.oncomplete=resolve;
    transaction.onerror=()=>reject(transaction.error);
    transaction.onabort=()=>reject(transaction.error||Error('캐시 초기화가 중단되었습니다.'));
  });
  db.close();
}
async function resetUserState(){
  if(!window.confirm('가져온 전략, 선택 상태, 다운로드한 종목과 계산 결과를 모두 초기화할까요?'))return;
  const button=$('reset');
  button.disabled=true;
  $('status').textContent='저장된 사용자 데이터를 초기화하고 있습니다.';
  try{
    localStorage.removeItem(uiStateKey);
    await clearBrowserCache();
    window.location.reload();
  }catch(error){
    button.disabled=false;
    $('status').textContent=`초기화 오류: ${error.message}`;
  }
}
const preflightSetupDashboard=setupDashboard;
function strategyYamlFilename(definition){
  const value=definition?._yaml_file;
  if(!value)return '';
  return String(value).split(/[\\/]/).at(-1);
}
function syncStrategyButtonMetadata(){
  for(const input of $('strategy-list').querySelectorAll('input[type="checkbox"]')){
    const definition=definitions.find(item=>item.strategy.id===input.value);
    const label=input.closest('label');
    label?.classList.toggle('research-product-strategy',Boolean(definition?.source));
    if(label)label.title=strategyYamlFilename(definition);
  }
}
new MutationObserver(syncStrategyButtonMetadata).observe(
  $('strategy-list'),
  {childList:true},
);
setupDashboard=async function(){
  await preflightSetupDashboard();
  syncStrategyButtonMetadata();
  $('import').onchange=async event=>{
    try{
      const file=event.target.files[0];
      const def=migrateStrategyDefinition(parseYaml(await file.text()));
      if(!def?.strategy?.id)throw Error('strategy.id가 필요합니다.');
      def.strategy.enabled=true;
      def._yaml_file=file.name;
      const nextDefinitions=[...definitions.filter(item=>item.strategy.id!==def.strategy.id),def];
      validateStrategy(def,nextDefinitions);
      definitions=nextDefinitions;
      const state=loadUiState(),imported=(state.importedDefinitions||[]).map(migrateStrategyDefinition).filter(item=>item?.strategy?.id!==def.strategy.id);
      localStorage.setItem(uiStateKey,JSON.stringify({...state,importedDefinitions:[...imported,def]}));
      await setupDashboard();
      $('status').textContent='전략을 가져왔습니다. 필요한 시세와 지표는 자동으로 준비됩니다.';
    }catch(error){$('status').textContent=`YAML 오류: ${error.message}`;}
    finally{event.target.value='';}
  };
  $('reset').onclick=resetUserState;
  const fxToggle=$('detail-remove-fx');
  if(fxToggle){
    fxToggle.onchange=updateDetailFxView;
    $('detail-strategy')?.addEventListener('change',()=>{if(fxToggle.checked)void updateDetailFxView();});
  }
};
localizeDashboard();
document.querySelector('label.btn').childNodes[0].textContent='\uc804\ub7b5 \uac00\uc838\uc624\uae30';
document.querySelector('label.btn').childNodes[0].textContent='전략 가져오기';
document.querySelector('label.btn').childNodes[0].textContent='\uc804\ub7b5 \uac00\uc838\uc624\uae30';
localizeIndicatorView();
async function loadHostedStrategies(){
  if(!bundle.strategy_manifest_url)return;
  const manifestUrl=new URL(bundle.strategy_manifest_url,window.location.href);
  const response=await fetch(manifestUrl);
  if(!response.ok)throw Error('기본 전략 목록을 불러오지 못했습니다.');
  const manifest=await response.json(),entries=Array.isArray(manifest)?manifest:manifest.strategies;
  if(!Array.isArray(entries))throw Error('기본 전략 목록 형식이 올바르지 않습니다.');
  const loaded=await Promise.all(entries.map(async entry=>{
    const path=typeof entry==='string'?entry:entry.path;
    const yamlResponse=await fetch(new URL(path,manifestUrl));
    if(!yamlResponse.ok)throw Error(`전략 파일을 불러오지 못했습니다: ${path}`);
    const definition=parseYaml(await yamlResponse.text());
    definition._yaml_file=String(path).split(/[\\/]/).at(-1);
    return definition;
  }));
  const state=loadUiState();
  const imported=(state.importedDefinitions||[])
    .map(migrateStrategyDefinition)
    .filter(definition=>definition?.strategy?.id);
  const importedIds=new Set(imported.map(definition=>definition.strategy.id));
  definitions=[...loaded.filter(definition=>!importedIds.has(definition.strategy.id)),...imported];
}
const DEFAULT_WEB_START_DATE='2012-01-03';
function defaultStartDate(first,last){
  return DEFAULT_WEB_START_DATE>=first&&DEFAULT_WEB_START_DATE<=last?DEFAULT_WEB_START_DATE:first;
}
function populateAnalysisPeriod(){
  const dates=[...dashboardResults.values()]
    .flatMap(([,history])=>history.map(row=>row.date))
    .filter(Boolean)
    .sort();
  if(!dates.length)return;
  const start=$('start-date'),end=$('end-date'),first=dates[0],last=dates.at(-1);
  if(!start.value||start.value<first||start.value>last)start.value=defaultStartDate(first,last);
  if(!end.value||end.value<first||end.value>last)end.value=last;
}
const periodAwareRunDashboard=runDashboard;
runDashboard=async function(){
  const committed=await periodAwareRunDashboard();
  if(!committed)return;
  populateAnalysisPeriod();
  if(dashboardResults.size)renderAnalysis();
};
const stateRestoringDashboardSetup=setupDashboard;
setupDashboard=async function(){
  const state=loadUiState(),list=$('strategy-list');
  const selectedIds=Array.isArray(state.strategyIds)?new Set(state.strategyIds):null;
  let restored=false,observer;
  const restore=()=>{
    if(restored||!list.querySelector('input[type="checkbox"]'))return;
    restored=true;
    if(selectedIds)for(const input of list.querySelectorAll('input[type="checkbox"]'))input.checked=selectedIds.has(input.value);
    if(state.analysisStart)$('start-date').value=state.analysisStart;
    if(state.analysisEnd)$('end-date').value=state.analysisEnd;
    list.style.visibility='';
    observer?.disconnect();
  };
  if(selectedIds){
    list.style.visibility='hidden';
    observer=new MutationObserver(restore);
    observer.observe(list,{childList:true,subtree:true});
  }
  try{await stateRestoringDashboardSetup();}
  finally{restore();observer?.disconnect();list.style.visibility='';}
};
async function bootstrapDashboard(){
  await loadHostedStrategies();
  if(bundle.static_site&&!Array.isArray(loadUiState().strategyIds)){
    const first=definitions.find(definition=>definition?.strategy?.enabled!==false);
    if(first)localStorage.setItem(uiStateKey,JSON.stringify({...loadUiState(),strategyIds:[first.strategy.id],knownStrategyIds:definitions.filter(definition=>definition?.strategy?.enabled!==false).map(definition=>definition.strategy.id)}));
  }
  await setupDashboard();
}
bootstrapDashboard().catch(error=>{$('status').textContent=`오류: ${error.message}`;});

// Keep product-backed instruments readable throughout indicator research while
// retaining the ticker as the stable value used by selection and calculation.
function indicatorDisplayTicker(ticker){
  return productDisplayNames[ticker]||ticker;
}
function localizeIndicatorProductNames(){
  document.querySelectorAll('.research-indicator-column-toggle[data-ticker]').forEach(button=>{
    button.textContent=indicatorDisplayTicker(button.dataset.ticker);
  });
  for(const option of $('indicator-ticker')?.options||[]){
    option.textContent=indicatorDisplayTicker(option.value);
  }
  document.querySelectorAll('.research-indicator-selection-badge').forEach(badge=>{
    const [ticker,...rest]=badge.textContent.split(' \u00b7 ');
    if(rest.length)badge.textContent=`${indicatorDisplayTicker(ticker)} \u00b7 ${rest.join(' \u00b7 ')}`;
  });
}
const productNamedIndicatorSetup=setupIndicatorControls;
setupIndicatorControls=function(){
  productNamedIndicatorSetup();
  localizeIndicatorProductNames();
};
const productNamedIndicatorRenderer=renderIndicators;
renderIndicators=async function(){
  await productNamedIndicatorRenderer();
  const plot=$('indicator-plot');
  if(!plot?.data)return;
  const labels=plot.data.map(trace=>{
    const raw=String(trace.meta?.tooltipName||'');
    const [ticker,...rest]=raw.split(' \u00b7 ');
    if(!rest.length)return trace.name;
    return `${indicatorDisplayTicker(ticker)} \u00b7 ${indicatorCatalogLabels[rest.join(' \u00b7 ')]||rest.join(' \u00b7 ')}`;
  });
  const metadata=plot.data.map((trace,index)=>({...trace.meta,tooltipName:labels[index]}));
  await Plotly.restyle(plot,{name:labels,meta:metadata});
  localizeIndicatorProductNames();
};

const defaultStartIndicatorControls=setupIndicatorControls;
setupIndicatorControls=function(){
  defaultStartIndicatorControls();
  const state=loadUiState(),start=$('indicator-start-date'),end=$('indicator-end-date');
  if(!state.indicatorStart&&start.value&&end.value)start.value=defaultStartDate(start.value,end.value);
};

const SIMPLE_ROTATION_ASSET_CLASSES={GLD:'GOLD','069500.KS':'EQUITY',VEA:'EQUITY',VWO:'EQUITY'};
function normalizeSimpleRotation(def){
  const rotation=def?.rotation;
  if(!rotation||!Object.hasOwn(rotation,'replace'))return def;
  const allowed=new Set(['replace','review','assets','suspend_when']),unknown=Object.keys(rotation).filter(key=>!allowed.has(key));
  if(unknown.length)throw Error(`rotation의 지원하지 않는 항목: ${unknown.join(', ')}`);
  if(!Array.isArray(rotation.assets)||!rotation.assets.length||rotation.assets.some(ticker=>typeof ticker!=='string'||!ticker))throw Error('rotation.assets에는 종목 코드를 하나 이상 넣어야 합니다.');
  def.rotation={sleeve:rotation.replace,check:rotation.review||'monthly',...(Object.hasOwn(rotation,'suspend_when')?{suspend_when:rotation.suspend_when}:{}),candidates:rotation.assets.map(ticker=>({ticker,group:ticker,asset_class:SIMPLE_ROTATION_ASSET_CLASSES[ticker]||'BOND'})),top_n:2,max_single_sleeve_share:.5,max_gold_sleeve_share:.3,max_equity_sleeve_share:.3,switch_score_margin:3,minimum_hold_periods:3,minimum_weight_change:.1};
  return def;
}
function normalizeAutomaticValuation(def){
  if(!def||def.valuation||def.source)return def;
  const foreign=(def.assets?.required||[]).filter(ticker=>ticker!=='KRW=X'&&!/\.(KS|KQ)$/i.test(String(ticker)));
  return foreign.length?{...def,valuation:{currency:'KRW',fx_ticker:'KRW=X',foreign_assets:foreign,signal_currency:'LOCAL'}}:def;
}
function usdValuationDefinition(def){return {...def,valuation:{currency:'USD',foreign_assets:[],signal_currency:'USD'}};}
const simpleRotationValidator=validateStrategy;
validateStrategy=function(def,all=definitions){return simpleRotationValidator(normalizeAutomaticValuation(normalizeSimpleRotation(def)),all);};

function mountDateRangePicker(displayId,startId,endId,label){
  const start=$(startId),end=$(endId);
  if(!start||!end)return;
  let display=$(displayId);
  if(!display){
    display=document.createElement('input');
    display.id=displayId;
    display.type='text';
    display.className='form-control';
    display.placeholder='기간을 선택하세요';
    display.setAttribute('aria-label',label);
    const container=start.parentElement;
    container.replaceChildren(display,start,end);
    start.hidden=true;
    end.hidden=true;
  }
  if(!window.flatpickr){
    display.value=[start.value,end.value].filter(Boolean).join(' — ');
    return;
  }
  const picker=display._flatpickr||flatpickr(display,{
    mode:'range',
    locale:flatpickr.l10ns.ko,
    dateFormat:'Y.m.d',
    allowInput:false,
    onChange(dates,_,instance){
      if(dates.length!==0&&dates.length!==2)return;
      start.value=dates[0]?instance.formatDate(dates[0],'Y-m-d'):'';
      end.value=dates[1]?instance.formatDate(dates[1],'Y-m-d'):'';
      start.dispatchEvent(new Event('change',{bubbles:true}));
    },
  });
  if(start.value&&end.value)picker.setDate([start.value,end.value],false,'Y-m-d');
}
function installYearDropdown(instance){
  const yearInput=instance?.currentYearElement;
  if(!yearInput||yearInput.tagName==='SELECT')return;
  const select=document.createElement('select');
  select.className=`${yearInput.className} flatpickr-year-select`;
  const current=new Date().getFullYear(),first=1990,last=current+2;
  for(let year=first;year<=last;year++){
    const option=document.createElement('option');
    option.value=String(year);
    option.textContent=String(year);
    option.selected=year===instance.currentYear;
    select.append(option);
  }
  select.addEventListener('change',()=>instance.changeYear(Number(select.value)));
  yearInput.replaceWith(select);
  instance.currentYearElement=select;
  instance._yearDropdown=select;
}
// This mirrors the Windows 11 navigation model: day grid -> month grid ->
// year grid. It stays independent from browser-native date input behavior.
const win11Months=['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'];
const win11Weekdays=['일','월','화','수','목','금','토'];
let activeWin11DatePicker=null;
function win11DateFromValue(value){const date=value?new Date(`${value}T00:00:00`):new Date();return Number.isNaN(date.getTime())?new Date():date;}
function win11DateValue(date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;}
function closeWin11DatePicker(){if(activeWin11DatePicker){activeWin11DatePicker.hidden=true;activeWin11DatePicker=null;}}
function positionWin11DatePicker(picker,input){const rect=input.getBoundingClientRect(),width=Math.min(320,window.innerWidth-24),left=Math.max(12,Math.min(rect.left,window.innerWidth-width-12)),below=rect.bottom+8;picker.style.left=`${left}px`;picker.style.top=`${Math.min(below,window.innerHeight-picker.offsetHeight-12)}px`;}
function createWin11DatePicker(input){
  const picker=document.createElement('div');
  picker.className='win11-date-picker';picker.hidden=true;picker.setAttribute('role','dialog');picker.setAttribute('aria-label','날짜 선택');
  document.body.append(picker);
  let cursor=win11DateFromValue(input.value),view='day',yearPage=cursor.getFullYear()-5;
  const selectDate=date=>{input.value=win11DateValue(date);input.dispatchEvent(new Event('change',{bubbles:true}));closeWin11DatePicker();};
  const render=()=>{
    const year=cursor.getFullYear(),month=cursor.getMonth(),selected=input.value,today=win11DateValue(new Date());
    const title=view==='day'?`${year}년 ${win11Months[month]}`:view==='month'?`${year}년`:`${yearPage} - ${yearPage+11}`;
    const choices=[];
    if(view==='day'){
      const first=new Date(year,month,1),start=new Date(year,month,1-first.getDay());
      for(let index=0;index<42;index++){const date=new Date(start);date.setDate(start.getDate()+index);const value=win11DateValue(date),classes=['win11-date-day'];if(date.getMonth()!==month)classes.push('is-outside');if(value===today)classes.push('is-today');if(value===selected)classes.push('is-selected');choices.push(`<button type="button" class="${classes.join(' ')}" data-day="${value}" aria-label="${value}">${date.getDate()}</button>`);}
      picker.innerHTML=`<div class="win11-date-header"><button type="button" class="win11-date-nav" data-nav="prev" aria-label="이전 달">‹</button><button type="button" class="win11-date-title" data-view="month">${title}</button><button type="button" class="win11-date-nav" data-nav="next" aria-label="다음 달">›</button></div><div class="win11-date-weekdays">${win11Weekdays.map(day=>`<span>${day}</span>`).join('')}</div><div class="win11-date-days">${choices.join('')}</div>`;
    }else if(view==='month'){
      picker.innerHTML=`<div class="win11-date-header"><button type="button" class="win11-date-nav" data-nav="prev" aria-label="이전 해">‹</button><button type="button" class="win11-date-title" data-view="year">${title}</button><button type="button" class="win11-date-nav" data-nav="next" aria-label="다음 해">›</button></div><div class="win11-date-choices">${win11Months.map((name,index)=>`<button type="button" class="win11-date-choice ${index===month?'is-selected':''}" data-month="${index}">${name}</button>`).join('')}</div>`;
    }else{
      picker.innerHTML=`<div class="win11-date-header"><button type="button" class="win11-date-nav" data-nav="prev" aria-label="이전 12년">‹</button><button type="button" class="win11-date-title" data-view="day">${title}</button><button type="button" class="win11-date-nav" data-nav="next" aria-label="다음 12년">›</button></div><div class="win11-date-choices">${Array.from({length:12},(_,index)=>{const item=yearPage+index;return `<button type="button" class="win11-date-choice ${item===year?'is-selected':''}" data-year="${item}">${item}</button>`;}).join('')}</div>`;
    }
    picker.classList.toggle('is-dark',document.documentElement.dataset.theme==='dark'||document.body.classList.contains('theme-dark'));
    positionWin11DatePicker(picker,input);
  };
  picker.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.dataset.day){selectDate(win11DateFromValue(button.dataset.day));return;}if(button.dataset.month!==undefined){cursor.setMonth(Number(button.dataset.month));view='day';render();return;}if(button.dataset.year){cursor.setFullYear(Number(button.dataset.year));view='month';render();return;}if(button.dataset.view){view=button.dataset.view;render();return;}if(button.dataset.nav){const amount=button.dataset.nav==='prev'?-1:1;if(view==='day')cursor.setMonth(cursor.getMonth()+amount);else if(view==='month')cursor.setFullYear(cursor.getFullYear()+amount);else yearPage+=amount*12;render();}});
  input.addEventListener('click',()=>{const opening=picker.hidden;closeWin11DatePicker();if(opening){cursor=win11DateFromValue(input.value);yearPage=cursor.getFullYear()-5;view='day';picker.hidden=false;render();activeWin11DatePicker=picker;}});
  input.addEventListener('keydown',event=>{if(event.key==='Escape')closeWin11DatePicker();if((event.key==='ArrowDown'||event.key==='Enter')&&picker.hidden){event.preventDefault();input.click();}});
  return picker;
}
document.addEventListener('pointerdown',event=>{if(activeWin11DatePicker&&!activeWin11DatePicker.contains(event.target)&&event.target!==activeWin11DatePicker._input)closeWin11DatePicker();});
mountDateRangePicker=function(_displayId,startId,endId){
  for(const input of [$(startId),$(endId)].filter(Boolean)){
    if(input.dataset.win11DatePickerBound)continue;
    input.dataset.win11DatePickerBound='1';input.type='text';input.readOnly=true;input.setAttribute('aria-haspopup','dialog');
    const picker=createWin11DatePicker(input);picker._input=input;
  }
};
const flatpickrDashboardSetup=setupDashboard;
setupDashboard=async function(){
  await flatpickrDashboardSetup();
  mountDateRangePicker('analysis-date-range','start-date','end-date','분석 기간');
  mountDateRangePicker('indicator-date-range','indicator-start-date','indicator-end-date','지표 연구 기간');
};

const CANDLE_TIMEFRAMES={
  daily:{label:'일봉'},
  weekly:{label:'주봉'},
  monthly:{label:'월봉'},
};
const AUTO_QQQ_MOVING_AVERAGES=['MA20','MA55','MA120','MA200'];
function qqqCandleRows(rows,timeframe){
  if(timeframe==='daily')return rows;
  const grouped=new Map();
  for(const row of rows){
    const date=new Date(`${row.Date}T00:00:00Z`);
    const key=timeframe==='monthly'
      ? row.Date.slice(0,7)
      : (()=>{const friday=new Date(date);friday.setUTCDate(date.getUTCDate()+((5-date.getUTCDay()+7)%7));return friday.toISOString().slice(0,10);})();
    const current=grouped.get(key);
    if(!current)grouped.set(key,{...row});
    else grouped.set(key,{...current,Date:row.Date,High:Math.max(Number(current.High),Number(row.High)),Low:Math.min(Number(current.Low),Number(row.Low)),Close:Number(row.Close)});
  }
  return [...grouped.values()];
}
function mergeIndicatorCandleTraces(traces,context){
  if(!Array.isArray(traces)||!context?.rows?.length)return traces;
  const {ticker,displayTicker,timeframe,rows,displayedPairs}=context;
  const closeIndex=traces.findIndex(trace=>{
    const tooltipName=String(trace.meta?.tooltipName||'');
    return trace.meta?.panel==='price'&&!trace.meta?.isStrategySeries&&
      (tooltipName===`${ticker} · Close`||tooltipName===`${displayTicker} · Close`||
       tooltipName===`${ticker} · 종가`||tooltipName===`${displayTicker} · 종가`);
  });
  if(closeIndex<0)return traces;
  const closeTrace=traces[closeIndex],baseline=Number(rows[0]?.Close);
  if(!baseline)return traces;
  for(const [,field] of displayedPairs.filter(([selectedTicker,field])=>
    selectedTicker===ticker&&['Close',...AUTO_QQQ_MOVING_AVERAGES].includes(field))){
    const labels=new Set([field,indicatorCatalogLabels?.[field]||field]);
    const trace=traces.find(item=>{
      if(item.meta?.panel!=='price'||item.meta?.isStrategySeries)return false;
      const [traceTicker,traceField]=String(item.meta?.tooltipName||'').split(' · ');
      return (traceTicker===ticker||traceTicker===displayTicker)&&labels.has(traceField);
    });
    if(trace)trace.customdata=rows.map(row=>Number(row[field]));
  }
  const candles=qqqCandleRows(rows,timeframe),label=CANDLE_TIMEFRAMES[timeframe]?.label||timeframe;
  const candleTrace={type:'candlestick',x:candles.map(row=>row.Date),open:candles.map(row=>Number(row.Open)/baseline*100),high:candles.map(row=>Number(row.High)/baseline*100),low:candles.map(row=>Number(row.Low)/baseline*100),close:candles.map(row=>Number(row.Close)/baseline*100),customdata:candles.map(row=>({open:Number(row.Open),high:Number(row.High),low:Number(row.Low),close:Number(row.Close)})),name:`${displayTicker} · ${label}`,showlegend:false,xaxis:closeTrace.xaxis||'x',yaxis:closeTrace.yaxis||'y',increasing:{line:{color:'#F23645'},fillcolor:'#F23645'},decreasing:{line:{color:'#2962FF'},fillcolor:'#2962FF'},whiskerwidth:.35,hoverinfo:'none',meta:{tooltipName:`${displayTicker} · ${label}`,panel:'price'}};
  traces.splice(closeIndex,1,candleTrace);
  return traces;
}
const candleIndicatorRenderer=renderIndicators;
let indicatorCandleRenderContext=null;
renderIndicators=async function(){
  const control=$('indicator-candles');
  const selectedPairs=[...indicatorSelection].map(key=>key.split('|'));
  const selectedTickers=[...new Set(selectedPairs.map(([ticker])=>ticker))];
  const candleTicker=selectedTickers.length===1?selectedTickers[0]:'';
  const explicitlySelectedMovingAverages=selectedPairs.filter(([ticker,field])=>ticker===candleTicker&&AUTO_QQQ_MOVING_AVERAGES.includes(field));
  const strategyWithSingleQqqIndicator=Boolean($('indicator-strategy')?.value)&&selectedPairs.length===1&&candleTicker==='QQQ';
  const autoMovingAverages=candleTicker&&!explicitlySelectedMovingAverages.length&&!strategyWithSingleQqqIndicator?AUTO_QQQ_MOVING_AVERAGES.map(field=>`${candleTicker}|${field}`):[];
  for(const key of autoMovingAverages)indicatorSelection.add(key);
  const displayedPairs=[...indicatorSelection].map(key=>key.split('|'));
  const candleCloseSelected=Boolean(candleTicker)&&indicatorSelection.has(`${candleTicker}|Close`);
  if(control)control.classList.toggle('offline-hidden',!candleCloseSelected);
  const timeframe=candleCloseSelected?(control?.querySelector('input:checked')?.value||''):'';
  const data=timeframe?await loadData():null,start=$('indicator-start-date').value,end=$('indicator-end-date').value;
  const rows=timeframe?(data[candleTicker]||[]).filter(row=>(!start||row.Date>=start)&&(!end||row.Date<=end)):[];
  indicatorCandleRenderContext=timeframe&&rows.length?{
    ticker:candleTicker,
    displayTicker:typeof indicatorDisplayTicker==='function'?indicatorDisplayTicker(candleTicker):candleTicker,
    timeframe,
    rows,
    displayedPairs,
  }:null;
  try{await candleIndicatorRenderer();}
  finally{
    indicatorCandleRenderContext=null;
    for(const key of autoMovingAverages)indicatorSelection.delete(key);
  }
};
const candleStateDashboardSetup=setupDashboard;
setupDashboard=async function(){
  await candleStateDashboardSetup();
  const control=$('indicator-candles');
  if(!control)return;
  const state=loadUiState(),hasSavedCandleState=Array.isArray(state.indicatorCandles);
  const selected=(hasSavedCandleState?(state.indicatorCandles||[]):['daily']).find(value=>CANDLE_TIMEFRAMES[value])||'';
  for(const input of control.querySelectorAll('input')){
    input.checked=input.value===selected;
    input.onchange=()=>{const timeframe=control.querySelector('input:checked')?.value||'';localStorage.setItem(uiStateKey,JSON.stringify({...loadUiState(),indicatorCandles:timeframe?[timeframe]:[]}));void renderIndicators();};
  }
  if(!$('indicators-view').classList.contains('offline-hidden'))await renderIndicators();
};

const tradingDayRangebreakCache=new Map();
function tradingDayRangebreaks(traces){
  const dates=[...new Set((traces||[]).flatMap(trace=>(trace.x||[]).map(value=>String(value).slice(0,10)).filter(value=>/^\d{4}-\d{2}-\d{2}$/.test(value))))].sort();
  if(dates.length<2)return [{bounds:['sat','mon']}];
  let hash=2166136261;
  for(const date of dates)for(let index=0;index<date.length;index++)hash=Math.imul(hash^date.charCodeAt(index),16777619);
  const cacheKey=`${dates[0]}:${dates.at(-1)}:${dates.length}:${hash>>>0}`;
  const cached=tradingDayRangebreakCache.get(cacheKey);
  if(cached)return cached;
  const observed=new Set(dates),missing=[];
  for(let day=new Date(`${dates[0]}T00:00:00Z`),end=new Date(`${dates.at(-1)}T00:00:00Z`);day<=end;day.setUTCDate(day.getUTCDate()+1)){
    const key=day.toISOString().slice(0,10),weekday=day.getUTCDay();
    if(weekday!==0&&weekday!==6&&!observed.has(key))missing.push(key);
  }
  const rangebreaks=missing.length?[{bounds:['sat','mon']},{values:missing}]:[{bounds:['sat','mon']}];
  if(tradingDayRangebreakCache.size>=12)tradingDayRangebreakCache.delete(tradingDayRangebreakCache.keys().next().value);
  tradingDayRangebreakCache.set(cacheKey,rangebreaks);
  return rangebreaks;
}
const tradingDayAxisReact=Plotly.react.bind(Plotly);
Plotly.react=function(target,traces,layout,...args){
  if(Array.isArray(traces)&&layout){
    const rangebreaks=tradingDayRangebreaks(traces);
    for(const [key,axis] of Object.entries(layout))if(/^xaxis\d*$/.test(key)&&axis?.type==='date')axis.rangebreaks=rangebreaks;
    for(const trace of traces)if(trace.type==='scattergl')trace.type='scatter';
  }
  return tradingDayAxisReact(target,traces,layout,...args);
};
bindVisibleYAutoscale=function(plotId){
  const plot=$(plotId);
  if(!plot||plot.dataset.visibleYAutoscaleBound)return;
  plot.dataset.visibleYAutoscaleBound='1';
  plot.on('plotly_relayout',change=>{
    if(!Object.keys(change||{}).some(key=>/^xaxis\d*\.(range|autorange)/.test(key)))return;
    const updates={};
    for(const yaxis of [...new Set((plot.data||[]).map(trace=>trace.yaxis||'y'))]){
      const values=[];
      for(const trace of (plot.data||[]).filter(item=>(item.yaxis||'y')===yaxis)){
        const xaxis=trace.xaxis||'x',range=plot._fullLayout?.[axisLayoutKey(xaxis)]?.range||plot.layout?.[axisLayoutKey(xaxis)]?.range;
        if(!range?.length)continue;
        const lower=Date.parse(range[0]),upper=Date.parse(range[1]);
        for(let index=0;index<(trace.x||[]).length;index++){
          const date=Date.parse(trace.x[index]);
          if(!Number.isFinite(date)||date<lower||date>upper)continue;
          const series=trace.type==='candlestick'?[trace.low?.[index],trace.high?.[index]]:[trace.y?.[index]];
          for(const value of series)if(Number.isFinite(Number(value)))values.push(Number(value));
        }
      }
      if(values.length)updates[`${axisLayoutKey(yaxis)}.range`]=visibleAxisRange(values);
    }
    if(Object.keys(updates).length)Plotly.relayout(plot,updates);
  });
};
const candleDateAwareTooltip=bindChartTooltip;
bindChartTooltip=function(plotId,kind){
  candleDateAwareTooltip(plotId,kind);
  if(kind!=='indicator')return;
  const plot=$(plotId);
  if(!plot||plot.dataset.candleDateBound)return;
  plot.dataset.candleDateBound='1';
  ['plotly_hover', 'plotly_click'].forEach(evt => plot.on(evt, event=>{
    const points=event.points||[],candlePoint=points.find(point=>point.data?.type==='candlestick'),candleTrace=candlePoint?.data||(plot.data||[]).find(trace=>trace.type==='candlestick');
    const heading=document.querySelector('.research-indicator-tooltip .research-custom-tooltip-date');
    const grid=heading?.parentElement?.querySelector('.research-custom-tooltip-grid');
    if(!grid)return;
    if(!candleTrace){
      const priceRows=[...grid.querySelectorAll('.research-custom-tooltip-row')].filter(row=>/종가|Close|이동평균|MA\s*\d+/i.test(row.querySelector('.research-custom-tooltip-label')?.textContent||''));
      const rank=row=>{const label=row.querySelector('.research-custom-tooltip-label')?.textContent||'';if(/종가|Close/i.test(label))return 0;const period=Number(label.match(/(?:이동평균|MA\s*)(\d+)/i)?.[1]);return Number.isFinite(period)?period:999;};
      for(const row of priceRows.sort((left,right)=>rank(left)-rank(right)))grid.append(row);
      return;
    }
    const hovered=points.find(point=>point.data?.type!=='candlestick');
    const hoverDate=String(hovered?.x||candlePoint?.x||'').slice(0,10);
    let candleIndex=candleIndexForDate(candleTrace.x||[],hoverDate);
    if(candleIndex<0)candleIndex=Number.isInteger(candlePoint?.pointNumber)?candlePoint.pointNumber:0;
    const raw=candleTrace.customdata?.[candleIndex]||{open:candleTrace.open?.[candleIndex],high:candleTrace.high?.[candleIndex],low:candleTrace.low?.[candleIndex],close:candleTrace.close?.[candleIndex]};
    const date=String(candleTrace.x?.[candleIndex]||hoverDate).slice(0,10).replaceAll('-','.');
    if(heading)heading.textContent=date;
    const candleRow=[...grid.querySelectorAll('.research-custom-tooltip-row')].find(row=>row.querySelector('.research-custom-tooltip-label')?.textContent===candleTrace.name);
    if(candleRow){const value=candleRow.querySelector('.research-custom-tooltip-value');if(value)value.textContent=`시 ${tooltipNumber(raw.open)}\n고 ${tooltipNumber(raw.high)}\n저 ${tooltipNumber(raw.low)}\n종 ${tooltipNumber(raw.close)}`;grid.prepend(candleRow);}
  }));
};
const matchedPriceHeightReact=Plotly.react.bind(Plotly);
Plotly.react=function(target,traces,layout,...args){
  const id=typeof target==='string'?target:target?.id;
  if(id==='detail-plot')layout.height=700;
  if(id==='indicator-plot'){
    const panels=Object.keys(layout||{}).filter(key=>/^yaxis\d*$/.test(key)).length;
    layout.height=700+Math.max(0,panels-1)*210;
    const plot=typeof target==='string'?$(target):target;
    if(plot){plot.style.height=`${layout.height}px`;plot.style.minHeight=`${layout.height}px`;}
  }
  return matchedPriceHeightReact(target,traces,layout,...args);
};

let indicatorPlotCommitCount=0;
let indicatorLongRangeMode=false;
const singlePassIndicatorReact=Plotly.react.bind(Plotly);
Plotly.react=function(target,traces,layout,...args){
  const id=typeof target==='string'?target:target?.id;
  if(id==='indicator-plot'){
    mergeIndicatorCandleTraces(traces,indicatorCandleRenderContext);
    indicatorLongRangeMode=Math.max(0,...(traces||[]).map(trace=>trace.x?.length||0))>1500;
    if(indicatorLongRangeMode)for(const [key,axis] of Object.entries(layout||{})){
      if(/^xaxis\d*$/.test(key)&&axis?.rangeslider)axis.rangeslider={...axis.rangeslider,visible:false};
    }
    indicatorPlotCommitCount++;
    lastIndicatorRenderKey=indicatorRenderKey();
  }
  return singlePassIndicatorReact(target,traces,layout,...args);
};
const compactIndicatorRelayout=Plotly.relayout.bind(Plotly);
Plotly.relayout=function(target,update,...args){
  const id=typeof target==='string'?target:target?.id;
  if(id==='indicator-plot'&&indicatorLongRangeMode&&update){
    update={...update};
    for(const key of Object.keys(update))if(/\.rangeslider\.visible$/.test(key))update[key]=false;
  }
  return compactIndicatorRelayout(target,update,...args);
};

let lastIndicatorRenderKey='';
const indicatorDataRequests=new Map();
function ensureSelectedIndicatorData(){
  const needsCompositeValuation=indicatorSelection.has('QQQ|VALUATION_SCORE');
  const removeFx=Boolean($('indicator-remove-fx')?.checked);
  const selectedTickers=[...new Set([...indicatorSelection].map(key=>key.split('|')[0]).filter(Boolean))].sort(),requestKey=`${selectedTickers.join('|')}:${needsCompositeValuation?'valuation':'standard'}:${removeFx?'usd':'native'}`;
  if(indicatorDataRequests.has(requestKey))return indicatorDataRequests.get(requestKey);
  const request=(async()=>{
    const data=await loadData();
    const componentTickers=selectedTickers.some(ticker=>ticker==='TDF2050_PROXY'||krwAdjustedBaseTicker(ticker)==='TDF2050_PROXY')
      ? Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS):[];
    const sourceTickers=selectedTickers.flatMap(ticker=>isKrwAdjustedTicker(ticker)?[krwAdjustedBaseTicker(ticker),'KRW=X']:[ticker]);
    const fxTickers=removeFx?selectedTickers.map(tickerFxRate).filter(Boolean):[];
    const required=[...new Set([...sourceTickers,...componentTickers,...fxTickers,...(needsCompositeValuation?['QQQ','SPY','BIL']:[])])];
    await loadCachedTickerData(data,required);
    await fetchProxyTickerData(data,required,needsCompositeValuation?'2004-01-01':null);
    if(needsCompositeValuation){
      addCompositeValuationScore(data);
      await saveCachedPrices('QQQ',data.QQQ);
    }
    await ensureTdf2050Proxy(data,selectedTickers);
    await ensureKrwAdjustedAssets(data,selectedTickers);
    return data;
  })().catch(error=>{indicatorDataRequests.delete(requestKey);throw error;});
  indicatorDataRequests.set(requestKey,request);
  return request;
}
function indicatorRenderKey(){
  const strategy=$('indicator-strategy')?.value||'';
  const candle=$('indicator-candles')?.querySelector('input:checked')?.value||'';
  const overlays=[...($('indicator-overlays')?.querySelectorAll('input:checked')||[])].map(input=>input.value).sort();
  return JSON.stringify({
    selection:[...indicatorSelection].sort(),
    start:$('indicator-start-date')?.value||'',
    end:$('indicator-end-date')?.value||'',
    strategy,
    overlays,
    candle,
    removeFx:Boolean($('indicator-remove-fx')?.checked),
    definitions:definitions.length,
  });
}
const cachedIndicatorRenderer=renderIndicators;
renderIndicators=async function(){
  const key=indicatorRenderKey(),plot=$('indicator-plot');
  if(key===lastIndicatorRenderKey&&plot?.querySelector('.main-svg')){
    requestAnimationFrame(()=>Plotly.Plots.resize(plot));
    return;
  }
  await ensureSelectedIndicatorData();
  const before=indicatorPlotCommitCount;
  await cachedIndicatorRenderer();
  if(indicatorPlotCommitCount>before)lastIndicatorRenderKey=key;
};

const immediateIndicatorTabSetup=setupDashboard;
setupDashboard=async function(){
  lastIndicatorRenderKey='';
  await immediateIndicatorTabSetup();
  void ensureSelectedIndicatorData().catch(()=>{});
  const tab=$('indicators-tab'),showIndicators=tab?.onclick;
  if(!tab||!showIndicators)return;
  tab.onclick=async()=>{
    $('analysis-view').classList.add('offline-hidden');
    $('indicators-view').classList.remove('offline-hidden');
    $('analysis-tab').classList.remove('active');
    tab.classList.add('active');
    const plot=$('indicator-plot');
    plot?.setAttribute('aria-busy','true');
    await new Promise(resolve=>requestAnimationFrame(resolve));
    try{
      if(lastIndicatorRenderKey&&plot?.querySelector('.main-svg')){
        saveUiState('indicators');
        Plotly.Plots.resize(plot);
        return;
      }
      return await showIndicators();
    }
    finally{plot?.setAttribute('aria-busy','false');}
  };
};

function installIndicatorTabFastPath(){
  const tab=$('indicators-tab');
  if(!tab||tab.dataset.fastIndicatorTabBound)return;
  tab.dataset.fastIndicatorTabBound='1';
  tab.addEventListener('click',event=>{
    $('analysis-view').classList.add('offline-hidden');
    $('indicators-view').classList.remove('offline-hidden');
    $('analysis-tab').classList.remove('active');
    tab.classList.add('active');
    const plot=$('indicator-plot');
    if(!lastIndicatorRenderKey||!plot?.querySelector('.main-svg'))return;
    event.preventDefault();
    event.stopImmediatePropagation();
    saveUiState('indicators');
    plot.setAttribute('aria-busy','false');
    requestAnimationFrame(()=>Plotly.Plots.resize(plot));
  },true);
}
installIndicatorTabFastPath();

// 지표연구의 환율 제거 옵션은 원화 환산 티커에는 원자산을 사용하고,
// 국내 상장 종목에는 같은 날의 USD/KRW 환율을 나누어 달러 기준 가격을 만든다.
function exchangeRateRemovedIndicatorRows(data,ticker){
  const baseTicker=krwAdjustedBaseTicker(ticker);
  if(baseTicker)return data[baseTicker]||data[ticker]||[];
  const fxTicker=tickerFxRate(ticker),source=data[ticker],fxRows=data[fxTicker];
  if(!fxTicker||!source?.length||!fxRows?.length)return source||[];
  const fxByDate=new Map(fxRows.map(row=>[row.Date,Number(row.Close)]));
  let lastFx=NaN;
  const rows=[];
  for(const row of source){
    const known=fxByDate.get(row.Date);
    if(Number.isFinite(known)&&known>0)lastFx=known;
    if(!Number.isFinite(lastFx)||lastFx<=0)continue;
    const copy={...row};
    for(const field of ['Open','High','Low','Close'])copy[field]=Number(copy[field])/lastFx;
    rows.push(copy);
  }
  return addStrategyIndicators(rows);
}

const indicatorFxHistories=new Map();
const exchangeRateAwareIndicatorRenderer=renderIndicators;
renderIndicators=async function(){
  const toggle=$('indicator-remove-fx'),selected=String($('indicator-strategy')?.value||''),removeFx=Boolean(toggle?.checked);
  const definition=definitions.find(def=>def.strategy.id===selected),originalEntry=dashboardResults.get(selected),data=await ensureSelectedIndicatorData();
  const restored=[];
  if(removeFx){
    for(const ticker of new Set([...indicatorSelection].map(key=>key.split('|')[0]).filter(Boolean))){
      const rows=exchangeRateRemovedIndicatorRows(data,ticker);
      if(rows!==data[ticker]){restored.push([ticker,data[ticker]]);data[ticker]=rows;}
    }
  }
  if(removeFx&&definition){
    const cacheKey=`${selected}:${definition.strategy.version||''}`;
    let history=indicatorFxHistories.get(cacheKey);
    if(!history){
      if(toggle)toggle.disabled=true;
      try{
        const strategyData=await downloadMissingData(definition);
        history=runWithData(usdValuationDefinition(definition),strategyData,definitions);
        indicatorFxHistories.set(cacheKey,history);
      }finally{if(toggle)toggle.disabled=false;}
    }
    dashboardResults.set(selected,[definition,history]);
  }
  try{return await exchangeRateAwareIndicatorRenderer();}
  finally{
    for(const [ticker,rows] of restored)data[ticker]=rows;
    if(definition){
      if(originalEntry)dashboardResults.set(selected,originalEntry);
      else dashboardResults.delete(selected);
    }
  }
};

const indicatorFxDashboardSetup=setupDashboard;
setupDashboard=async function(){
  await indicatorFxDashboardSetup();
  const toggle=$('indicator-remove-fx');
  if(!toggle)return;
  const saved=loadUiState();
  if(Object.hasOwn(saved,'indicatorRemoveFx'))toggle.checked=Boolean(saved.indicatorRemoveFx);
  toggle.onchange=()=>{
    localStorage.setItem(uiStateKey,JSON.stringify({...loadUiState(),indicatorRemoveFx:toggle.checked}));
    lastIndicatorRenderKey='';
    void renderIndicators();
  };
};

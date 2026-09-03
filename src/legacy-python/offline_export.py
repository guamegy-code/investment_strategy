"""Create a self-contained, offline HTML strategy research application."""

from __future__ import annotations

import argparse
import ast
import base64
import gzip
import hashlib
import json
from pathlib import Path
import shutil

import plotly
import pandas as pd
import yaml

from config import DATA_DIR, PROJECT_ROOT, RESULT_DIR


WEB_SOURCE_DIR = Path(__file__).resolve().parents[1] / "web"

RUNTIME = r"""
const bundle = JSON.parse(document.getElementById('strategy-bundle').textContent);
const $ = (id) => document.getElementById(id);
let marketData = null;
let definitions = bundle.strategies;
let precomputedResults = null;
const proxyUrl = bundle.data_proxy || localStorage.getItem('investment-strategy:data-proxy') || '';

function bytesFromBase64(text) {
  const binary = atob(text), bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}
function openCache() { return new Promise((resolve,reject)=>{const request=indexedDB.open('investment-strategy-data',1);request.onupgradeneeded=()=>request.result.createObjectStore('prices');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);}); }
async function cachedPrices(ticker) { const db=await openCache(); return new Promise((resolve,reject)=>{const request=db.transaction('prices').objectStore('prices').get(ticker);request.onsuccess=()=>resolve(request.result||null);request.onerror=()=>reject(request.error);}); }
async function saveCachedPrices(ticker, rows) { const db=await openCache(); return new Promise((resolve,reject)=>{const request=db.transaction('prices','readwrite').objectStore('prices').put(rows,ticker);request.onsuccess=()=>resolve();request.onerror=()=>reject(request.error);}); }
async function loadData() {
  if (marketData) return marketData;
  if (!('DecompressionStream' in window)) throw Error('이 브라우저는 압축 데이터 해제를 지원하지 않습니다. 최신 Chrome, Edge 또는 Safari를 사용해 주세요.');
  const stream = new Blob([bytesFromBase64(bundle.data)], {type:'application/gzip'})
    .stream().pipeThrough(new DecompressionStream('gzip'));
  const raw = JSON.parse(await new Response(stream).text());
  marketData = Object.fromEntries(Object.entries(raw).map(([ticker, csv]) => [ticker, parseCsv(csv)]));
  return marketData;
}
async function loadPrecomputedResults() {
  if (precomputedResults) return precomputedResults;
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
  for(const ticker of strategyTickers(def))if(!data[ticker]){const cached=await cachedPrices(ticker);if(cached)data[ticker]=cached;}
  const missing=strategyTickers(def).filter(ticker=>!data[ticker]);
  if(!missing.length)return data;
  if(!proxyUrl)throw Error('Missing data for '+missing.join(', ')+'. Configure a data proxy when creating this HTML.');
  const endpoint=new URL('/prices',proxyUrl); endpoint.searchParams.set('tickers',missing.join(',')); endpoint.searchParams.set('start','2010-01-01');
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
  const names=Object.keys(ctx.market), values=names.map(name=>ctx.market[name]);
  const helpers={abs:Math.abs,min:Math.min,max:Math.max,sum:(...v)=>v.reduce((a,b)=>a+b,0),count:(...v)=>v.filter(Boolean).length,all:(...v)=>v.every(Boolean),any:(...v)=>v.some(Boolean),round:(v,d=0)=>Number(v.toFixed(d)),clamp:(v,l,h)=>Math.max(l,Math.min(h,v))};
  return Function(...names,'state','variables','parameters','portfolio','changed','previous','target_deviation',...Object.keys(helpers),'"use strict";return ('+code+')')(...values,ctx.state,ctx.variables,ctx.parameters,ctx.portfolio,ctx.changed,ctx.previous,ctx.targetDeviation,...Object.values(helpers));
}
function period(date, check='daily') { if(check==='daily')return date; if(check==='monthly')return date.slice(0,7); if(check==='quarterly')return date.slice(0,4)+'Q'+(Math.floor((Number(date.slice(5,7))-1)/3)+1); const day=new Date(date+'T00:00:00Z'), start=new Date(Date.UTC(day.getUTCFullYear(),0,1)); return day.getUTCFullYear()+'W'+Math.floor((day-start)/604800000); }
function recordsFor(tickers, data) { const byTicker={}; for(const ticker of tickers){if(!data[ticker])throw Error('내장 데이터에 '+ticker+'가 없습니다.'); byTicker[ticker]=new Map(data[ticker].map(row=>[row.Date,row]));} const dates=[...byTicker[tickers[0]].keys()].filter(date=>tickers.every(t=>byTicker[t].has(date))); return dates.map(date=>({date,market:Object.fromEntries(tickers.map(t=>[t,marketRow(byTicker[t].get(date))]))})); }
function marketRow(row) { const out={}; for(const [key,value] of Object.entries(row)){if(key!=='Date')out[key.toLowerCase()]=value===''?NaN:Number(value);} return out; }
function expressionStrings(value,out=[]) { if(typeof value==='string')out.push(value); else if(Array.isArray(value))value.forEach(item=>expressionStrings(item,out)); else if(value&&typeof value==='object')Object.values(value).forEach(item=>expressionStrings(item,out)); return out; }
function requiredMarketFields(def) { const fields={}; for(const text of expressionStrings([def.variables,def.state,def.target,def.rebalance,def.execution]))for(const match of text.matchAll(/\b([A-Z][A-Z0-9_=X-]*)\.([A-Za-z_][\w]*)/g)){const ticker=match[1],field=match[2].toLowerCase();(fields[ticker]??=new Set()).add(field);} return fields; }
class Portfolio { constructor(commission=.00015,slippage=.0002){this.cash=1;this.positions={};this.commission=commission;this.slippage=slippage;this.costs=0;this.pending=null;this.remaining=0;this.history=[];this.rebalances=[];} value(prices){return this.cash+Object.entries(this.positions).reduce((v,[t,s])=>v+s*prices[t],0);} weights(prices){const total=this.value(prices),out={};for(const t of Object.keys(prices))out[t]=(this.positions[t]||0)*prices[t]/total;return out;} trade(ticker,shares,price,date){if(Math.abs(shares)<1e-8)return;const direction=shares>0?1:-1, execution=price*(1+direction*this.slippage);if(shares>0)shares=Math.min(shares,Math.max(this.cash,0)/(execution*(1+this.commission)));if(Math.abs(shares)<1e-8)return;const notional=shares*execution,fee=Math.abs(notional)*this.commission;this.cash-=notional+fee;this.costs+=fee+Math.abs(shares)*Math.abs(execution-price);this.positions[ticker]=(this.positions[ticker]||0)+shares;} rebalance(prices,target,date){const total=this.value(prices);for(const [t,w]of Object.entries(target)){const diff=total*w-(this.positions[t]||0)*prices[t];if(diff<0)this.trade(t,-diff/prices[t]*-1,prices[t],date);}for(const [t,w]of Object.entries(target)){const diff=total*w-(this.positions[t]||0)*prices[t];if(diff>0)this.trade(t,diff/prices[t],prices[t],date);}} start(target,days,date,reason){this.pending={...target};this.remaining=days;this.rebalances.push({Date:date,Target:{...target},ExecutionDays:days,Reason:reason});} update(prices,date){if(!this.pending)return;const current=this.weights(prices);this.rebalance(prices,Object.fromEntries(Object.entries(this.pending).map(([t,w])=>[t,current[t]+(w-current[t])/this.remaining])),date);if(--this.remaining===0)this.pending=null;} record(date,prices,state){this.history.push({date,value:this.value(prices),weights:this.weights(prices),state,costs:this.costs});} }
class Declarative { constructor(def){this.def=def;this.state={};this.previous={};this.changed=new Set;this.candidates={};this.lastState={};this.lastRebalance={};for(const [k,v]of Object.entries(def.state||{}))this.state[k]=typeof v.initial==='string'&&v.initial.endsWith('%')?pct(v.initial):v.initial;this.first=true;} context(market,portfolio,target){const weights=portfolio.weights(Object.fromEntries(Object.entries(market).map(([t,r])=>[t,r.close])));return {market,state:this.state,variables:this.variables||{},parameters:this.def.parameters||{},portfolio:{weight:weights},changed:(x)=>this.changed.has(stateName(x)),previous:(x)=>this.previous[stateName(x)],targetDeviation:()=>Math.max(...Object.entries(target||{}).map(([t,w])=>Math.abs((weights[t]||0)-w)),0)};} step(date,market,portfolio){this.variables={};let ctx=this.context(market,portfolio);for(const [k,v]of Object.entries(this.def.variables||{}))this.variables[k]=evaluate(v,ctx);this.previous={...this.state};this.changed=new Set();for(const [name,config]of Object.entries(this.def.state||{})){const p=period(date,config.check);if(this.lastState[name]===p)continue;this.lastState[name]=p;ctx=this.context(market,portfolio);let selected=(config.rules||[]).find(rule=>rule.otherwise||evaluate(rule.when,ctx));if(!selected){delete this.candidates[name];continue;}let desired=selected.set;desired=typeof desired==='string'&&desired.startsWith('=')?evaluate(desired,ctx):(typeof desired==='string'&&desired.endsWith('%')?pct(desired):desired);if(desired===this.state[name]){delete this.candidates[name];continue;}const candidate=this.candidates[name],days=candidate&&candidate.value===desired?candidate.days+1:1;if(days>=Number(selected.confirm||1)){this.state[name]=desired;this.changed.add(name);delete this.candidates[name];}else this.candidates[name]={value:desired,days};}
      ctx=this.context(market,portfolio);let weights;for(const rule of this.def.target){if(!rule.when||evaluate(rule.when,ctx)){weights={};for(const [t,v]of Object.entries(rule.weights))weights[t]=typeof v==='string'&&/^\s*\d+(\.\d+)?%\s*$/.test(v)?pct(v):Number(evaluate(v,ctx));break;}}if(!weights)throw Error('목표 비중 규칙이 없습니다.');ctx=this.context(market,portfolio,weights);let rebalance=false,days;for(let i=0;i<(this.def.rebalance||[]).length;i++){const rule=this.def.rebalance[i],p=period(date,rule.check);const prior=this.lastRebalance[i];this.lastRebalance[i]=p;if(this.first||p===prior)continue;if(evaluate(rule.when,ctx)){rebalance=true;days=Number(rule.days===undefined?1:evaluate(rule.days,ctx));break;}}this.first=false;return {target:weights,rebalance,days:days||Number(evaluate((this.def.execution||{}).days||1,ctx)),state:this.state.market_mode||this.state.defense_mode||''};} }
function stateName(value){if(typeof value==='string')return value.split('.').at(-1);return ''}
function resolve(def, all){if(!def.source)return new Declarative(def);const source=all[def.source];if(!source)throw Error('source 전략을 찾을 수 없습니다: '+def.source);const runtime=new Declarative(source);return {step(date,market,portfolio){const prices=Object.fromEntries(Object.entries(market).map(([t,r])=>[t,r.close])),actual=portfolio.weights(prices),virtual={weights:()=>Object.fromEntries((source.assets.required||[]).map(asset=>[asset,Object.entries(def.products[asset]||{[asset]:1}).reduce((s,[p])=>s+(actual[p]||0),0)]))};const signal=runtime.step(date,market,virtual),target={};for(const [asset,w]of Object.entries(signal.target))for(const [product,share]of Object.entries(def.products[asset]||{[asset]:1}))target[product]=(target[product]||0)+w*pct(share);return {...signal,target};}};}
function runWithData(def,data,availableDefinitions=definitions){const all=Object.fromEntries(availableDefinitions.map(d=>[d.strategy.id,d]));all[def.strategy.id]=def;const source=def.source?all[def.source]:def,tickers=def.source?[...new Set([...(source.assets.required||[]),...Object.values(def.products).flatMap(Object.keys)])]:def.assets.required,required=requiredMarketFields(source);const rows=recordsFor(tickers,data).filter(row=>tickers.every(t=>[...(required[t]||[])].every(field=>Number.isFinite(row.market[t][field])))),runtime=resolve(def,all),portfolio=new Portfolio();for(const row of rows){const opens=Object.fromEntries(Object.entries(row.market).map(([t,v])=>[t,v.open]));portfolio.update(opens,row.date);const signal=runtime.step(row.date,row.market,portfolio);if(portfolio.history.length===0)portfolio.start(signal.target,signal.days,row.date,'INITIAL');else if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,'RULE');const closes=Object.fromEntries(Object.entries(row.market).map(([t,v])=>[t,v.close]));portfolio.record(row.date,closes,signal.state);}return portfolio.history;}
async function run(def){return runWithData(def,await downloadMissingData(def));}
function metrics(history){const first=history[0].value,last=history.at(-1).value,years=(new Date(history.at(-1).date)-new Date(history[0].date))/31557600000,peak=Math.max(...history.map(x=>x.value)),mdd=Math.min(...history.map(x=>x.value/Math.max(...history.slice(0,history.indexOf(x)+1).map(y=>y.value))-1));return {total:last/first-1,cagr:years?Math.pow(last/first,1/years)-1:0,mdd};}
function draw(history){const canvas=$('chart'),c=canvas.getContext('2d'),w=canvas.width=canvas.clientWidth*devicePixelRatio,h=canvas.height=340*devicePixelRatio;c.scale(devicePixelRatio,devicePixelRatio);const width=canvas.clientWidth,height=340;c.clearRect(0,0,width,height);const values=history.map(x=>x.value/history[0].value-1),min=Math.min(...values,0),max=Math.max(...values,0),range=max-min||1;c.strokeStyle='#2563eb';c.lineWidth=2;c.beginPath();values.forEach((v,i)=>{const x=i/(values.length-1)*width,y=height-20-(v-min)/range*(height-40);i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
function renderList(){const selected=$('strategies');if(!selected)return;selected.innerHTML='';definitions.filter(d=>d.strategy.enabled!==false).forEach((d,i)=>{const option=document.createElement('option');option.value=String(i);option.textContent=d.strategy.name;selected.append(option);});}
$('run').onclick=async()=>{try{$('status').textContent='데이터를 준비하고 전략을 실행하고 있습니다…';const index=Number($('strategies').value),def=definitions.filter(d=>d.strategy.enabled!==false)[index],history=await run(def),m=metrics(history);$('status').textContent=`${def.strategy.name} · 누적 수익률 ${(m.total*100).toFixed(2)}% · CAGR ${(m.cagr*100).toFixed(2)}% · MDD ${(m.mdd*100).toFixed(2)}%`;draw(history);}catch(error){$('status').textContent='오류: '+error.message;console.error(error);}};
$('import').onchange=async(e)=>{try{const text=await e.target.files[0].text(),definition=parseYaml(text);if(!definition?.strategy?.id)throw Error('strategy.id가 필요합니다.');definitions=definitions.filter(d=>d.strategy.id!==definition.strategy.id);definitions.push(definition);renderList();$('status').textContent='YAML 전략을 불러왔습니다. 목록에서 선택해 실행하세요.';}catch(error){$('status').textContent='YAML 오류: '+error.message;}};
globalThis.OfflineStrategyRuntime={parseYaml,evaluate,normalizeExpr,runWithData,metrics,Portfolio,Declarative};
function fmtPercent(value){return `${(value*100).toFixed(2)}%`;}
function localizeDashboard(){document.documentElement.lang='ko';document.querySelector('.research-brand-caption').textContent='퀀트 리서치';document.querySelector('.research-eyebrow').textContent='백테스트 대시보드';document.querySelector('.research-title').textContent='투자 전략 리서치';document.querySelector('.research-subtitle').textContent='전략 성과를 비교하고 종목별 지표를 분석합니다.';document.querySelector('.research-status').textContent='● 준비 완료';$('analysis-tab').textContent='성과 분석';$('indicators-tab').textContent='지표 연구';document.querySelector('#analysis-view .form-label').textContent='분석 기간';$('strategy-list').previousElementSibling.textContent='비교 전략';$('run').textContent='실행';document.querySelector('label.btn').childNodes[0].textContent='YAML 가져오기';$('indicator-type').innerHTML='<option value="price">가격 · 추세</option><option value="oscillator">오실레이터</option><option value="risk">리스크</option>';document.querySelectorAll('.card-title').forEach((node,index)=>{const labels=['누적 수익률','낙폭 경로','전략 상세','성과 요약','지표 연구'];if(labels[index])node.textContent=labels[index];});const descriptions=[['performance-plot','동일 시작점으로 정규화한 전략별 성과'],['drawdown-plot','고점 대비 손실과 회복 구간 비교'],['detail-plot','자산 비중과 실제 리밸런싱 시점을 확인합니다.'],['summary','열을 정렬하거나 너비를 조절해 전략을 비교할 수 있습니다.'],['indicator-plot','종목과 지표를 조합해 시장 구간을 분석합니다.']];for(const [id,label] of descriptions){const card=$(id).closest('.research-card');card?.querySelector('.research-card-description')&&(card.querySelector('.research-card-description').textContent=label);}}
function chartLayout(title,{slider=false,selector=true,detail=false}={}){const buttons=[{count:1,label:'1년',step:'year',stepmode:'backward'},{count:3,label:'3년',step:'year',stepmode:'backward'},{count:5,label:'5년',step:'year',stepmode:'backward'},{label:'전체',step:'all'}];return {dragmode:'pan',margin:{l:58,r:52,t:selector?detail?180:210:48,b:slider?78:54},paper_bgcolor:'transparent',plot_bgcolor:'transparent',hovermode:'x unified',hoverdistance:-1,spikedistance:-1,hoverlabel:{bgcolor:'rgba(255,255,255,.96)',bordercolor:'#dce1e7',font:{color:'#182433',size:12,family:'-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif'},align:'left',namelength:-1},xaxis:{type:'date',tickformat:'%Y.%m',hoverformat:'%Y.%m.%d',gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false,rangeslider:{visible:slider,thickness:.09,bgcolor:'rgba(106,109,120,.06)',bordercolor:'#dce1e7',borderwidth:1},rangeselector:selector?{buttons,x:0,xanchor:'left',y:detail?1.55:1.65,yanchor:'bottom',bgcolor:'rgba(106,109,120,.08)',activecolor:'rgba(41,98,255,.18)',bordercolor:'#dce1e7',borderwidth:1,font:{size:11}}:undefined},yaxis:{gridcolor:'#e7eaf0',linecolor:'#dce1e7',zeroline:false,tickformat:'.0f',ticksuffix:'%',title:{text:title==='낙폭 경로'?'낙폭 (%)':'수익률 (%)',standoff:10}},legend:{orientation:'h',y:1.02,x:0,yanchor:'bottom',entrywidth:.5,entrywidthmode:'fraction',font:{size:12}},font:{family:'-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif',color:'#182433',size:13}};}
function bindChartTooltip(plotId,kind){const plot=$(plotId);if(!plot||plot.dataset.tooltipBound)return;plot.dataset.tooltipBound='1';let card;const hide=()=>{if(card)card.style.display='none';};plot.on('plotly_unhover',hide);plot.on('plotly_hover',event=>{const points=event.points||[];if(!points.length)return;const date=String(points[0].x||'').slice(0,10).replaceAll('-','.');const rows=points.filter(point=>point.data?.hoverinfo!=='skip').map(point=>{const value=typeof point.y==='number'?`${(point.y).toFixed(2)}%`:'';return `<div class="offline-tooltip-row"><span>${point.data?.name||''}</span><strong>${value}</strong></div>`;});const returnPoint=points.find(point=>point.data?.name==='누적 수익률')||points[0],extra=returnPoint.customdata&&kind==='detail'?`<div class="offline-tooltip-separator"></div><div class="offline-tooltip-extra">${returnPoint.customdata}</div>`:'';if(!rows.length&&!extra)return;if(!card){card=document.createElement('div');card.className='offline-chart-tooltip';document.body.append(card);}card.innerHTML=`<div class="offline-tooltip-date">${date}</div>${rows.join('')}${extra}`;card.style.display='block';const rect=plot.getBoundingClientRect(),bbox=points[0].bbox||{},x=rect.left+(bbox.x0||rect.width/2),y=rect.top+(bbox.y0||rect.height/2),above=y-rect.top>rect.height-(y-rect.top);card.style.left=`${Math.max(12,Math.min(window.innerWidth-card.offsetWidth-12,x-card.offsetWidth/2))}px`;card.style.top=`${above?Math.max(12,y-card.offsetHeight-16):Math.min(window.innerHeight-card.offsetHeight-12,y+20)}px`;});}
function selectedDefinitions(){const ids=[...document.querySelectorAll('#strategy-list input:checked')].map(input=>input.value);return definitions.filter(def=>ids.includes(def.strategy.id));}
function visibleHistory(history){const start=$('start-date').value,end=$('end-date').value;return history.filter(row=>(!start||row.date>=start)&&(!end||row.date<=end));}
function setRangeYears(plotId,dates){const plot=$(plotId),parent=plot?.parentElement;if(!plot||!parent||!dates?.length)return;let labels=parent.querySelector(`.offline-range-years[data-for="${plotId}"]`);if(!labels){labels=document.createElement('div');labels.className='offline-range-years';labels.dataset.for=plotId;plot.after(labels);}const first=new Date(dates[0]).getFullYear(),last=new Date(dates.at(-1)).getFullYear(),step=Math.max(1,Math.ceil((last-first)/6)),years=[];for(let year=first;year<=last;year+=step)years.push(year);if(years.at(-1)!==last)years.push(last);labels.innerHTML=years.map(year=>`<span>${year}</span>`).join('');}
function drawSummary(entries){const rows=entries.map(([def,allHistory])=>{const history=visibleHistory(allHistory),m=metrics(history),daily=history.slice(1).map((row,index)=>row.value/history[index].value-1),mean=daily.reduce((sum,value)=>sum+value,0)/Math.max(daily.length,1),variance=daily.reduce((sum,value)=>sum+(value-mean)**2,0)/Math.max(daily.length-1,1),vol=Math.sqrt(variance)*Math.sqrt(252),downside=Math.sqrt(daily.filter(value=>value<0).reduce((sum,value)=>sum+value**2,0)/Math.max(daily.filter(value=>value<0).length,1))*Math.sqrt(252),sharpe=vol?m.cagr/vol:0,sortino=downside?m.cagr/downside:0,calmar=m.mdd?m.cagr/Math.abs(m.mdd):0,start=history[0]?.date||'',end=history.at(-1)?.date||'';return `<tr><td>${def.strategy.name}</td><td>${fmtPercent(m.cagr)}</td><td>${fmtPercent(m.mdd)}</td><td>${fmtPercent(vol)}</td><td>${sharpe.toFixed(2)}</td><td>${sortino.toFixed(2)}</td><td>${calmar.toFixed(2)}</td><td>0.00%</td><td>${fmtPercent(m.total)}</td><td>${start}</td><td>${end}</td></tr>`;});$('summary').innerHTML=`<table><thead><tr><th>전략</th><th>CAGR</th><th>MDD</th><th>변동성</th><th>Sharpe</th><th>Sortino</th><th>Calmar</th><th>거래비용</th><th>전체 수익률</th><th>시작일</th><th>종료일</th></tr></thead><tbody>${rows.join('')}</tbody></table>`;}
function renderDetail(){const id=$('detail-strategy').value,entry=dashboardResults.get(id);if(!entry)return;const [def,allHistory]=entry,history=visibleHistory(allHistory),m=metrics(history),cards=[['CAGR',fmtPercent(m.cagr)],['MDD',fmtPercent(m.mdd)],['누적 수익률',fmtPercent(m.total)],['최종 자산',history.at(-1).value.toFixed(3)]];$('detail-kpis').innerHTML=cards.map(([label,value])=>`<div class="card"><div class="research-kpi-body"><div><div class="research-kpi-label">${label}</div><div class="research-kpi-value">${value}</div></div></div></div>`).join('');const base=history[0].value,returns=history.map(row=>(row.value/base-1)*100),tickers=Object.keys(history[0].weights),detailText=row=>`상태: ${row.state||'-'}<br>종목 비중<br>${tickers.map(ticker=>`${ticker} ${((row.weights[ticker]||0)*100).toFixed(1)}%`).join('<br>')}`,traces=[];for(const ticker of tickers)traces.push({x:history.map(row=>row.date),y:history.map((row,index)=>(row.weights[ticker]||0)*returns[index]),name:ticker,stackgroup:'portfolio-return',mode:'lines',line:{width:.4},hoverinfo:'skip'});traces.push({x:history.map(row=>row.date),y:returns,name:'누적 수익률',showlegend:false,line:{color:'#182433',width:1.2},customdata:history.map(detailText),hoverinfo:'none'});const events=history.filter((row,index)=>index>0&&tickers.some(ticker=>Math.abs((row.weights[ticker]||0)-(history[index-1].weights[ticker]||0))>=0.02));if(events.length)traces.push({x:events.map(row=>row.date),y:events.map(row=>(row.value/base-1)*100),name:'리밸런싱',mode:'markers',marker:{symbol:'diamond',size:9,color:'#d63939',line:{color:'#fff',width:1}},customdata:events.map(detailText),hoverinfo:'skip'});const layout=chartLayout('누적 수익률',{slider:true,selector:true,detail:true});Plotly.react('detail-plot',traces,layout,{responsive:true,displaylogo:false});bindChartTooltip('detail-plot','detail');setRangeYears('detail-plot',history.map(row=>row.date));}
function renderAnalysis(){const entries=[...dashboardResults.values()],performance=[],drawdown=[],colors=['#206bc4','#d63939','#2fb344','#f59f00','#ae3ec9','#17a2b8'],dashes=['solid','dash','dot','dashdot'];for(const [index,[def,allHistory]] of entries.entries()){const history=visibleHistory(allHistory);if(!history.length)continue;const base=history[0].value,returns=history.map(row=>(row.value/base-1)*100),peaks=[];let peak=-Infinity;for(const value of history.map(row=>row.value)){peak=Math.max(peak,value);peaks.push((value/peak-1)*100);}performance.push({x:history.map(row=>row.date),y:returns,name:def.strategy.name,line:{width:.9,dash:dashes[index%dashes.length]},hoverinfo:'none'});drawdown.push({x:history.map(row=>row.date),y:peaks,name:def.strategy.name,line:{width:.9,dash:dashes[index%dashes.length]},hoverinfo:'none'});}Plotly.react('performance-plot',performance,chartLayout('누적 수익률'),{responsive:true,displaylogo:false});Plotly.react('drawdown-plot',drawdown,chartLayout('낙폭 경로'),{responsive:true,displaylogo:false});bindChartTooltip('performance-plot','comparison');bindChartTooltip('drawdown-plot','comparison');document.querySelectorAll('.offline-range-years[data-for="performance-plot"],.offline-range-years[data-for="drawdown-plot"]').forEach(node=>node.remove());drawSummary(entries);const detail=$('detail-strategy'),previous=detail.value;detail.innerHTML=entries.map(([def])=>`<option value="${def.strategy.id}">${def.strategy.name}</option>`).join('');detail.value=dashboardResults.has(previous)?previous:entries[0]?.[0].strategy.id||'';renderDetail();}
async function runDashboard(){try{$('status').textContent='선택한 전략을 실행하고 있습니다…';const selected=selectedDefinitions();if(!selected.length)throw Error('전략을 하나 이상 선택하세요.');const cached=await loadPrecomputedResults(),settled=await Promise.allSettled(selected.map(async def=>[def,cached[def.strategy.id]||await run(def)])),completed=settled.filter(result=>result.status==='fulfilled').map(result=>result.value),failed=settled.filter(result=>result.status==='rejected');if(!completed.length)throw failed[0].reason;dashboardResults=new Map(completed.map(entry=>[entry[0].strategy.id,entry]));renderAnalysis();$('status').textContent=failed.length?`${completed.length}개 전략 완료 · ${failed.length}개 실패: ${failed.map(item=>item.reason.message).join(' | ')}`:`${completed.length}개 전략 실행을 완료했습니다.`; }catch(error){$('status').textContent=`오류: ${error.message}`;console.error(error);}}
function indicatorFields(type){return type==='oscillator'?['RSI14','MACD','MACD_SIGNAL']:type==='risk'?['DRAWDOWN120','VOL60','ATR60']:['Close','EMA20','EMA55','EMA200','MA20','MA55'];}
var indicatorSelection=new Set();
async function renderIndicators(){try{const data=await loadData(),type=$('indicator-type').value,start=$('indicator-start-date').value,end=$('indicator-end-date').value,pairs=[...indicatorSelection].map(key=>key.split('|')).filter(([,field])=>indicatorFields(type).includes(field)),palette=['#206bc4','#2fb344','#f59f00','#ae3ec9','#d63939','#17a2b8'],traces=[];for(const [index,[ticker,name]] of pairs.entries()){const rows=(data[ticker]||[]).filter(row=>(!start||row.Date>=start)&&(!end||row.Date<=end)),values=rows.map(row=>Number(row[name]??row[name.toLowerCase()]));traces.push({x:rows.map(row=>row.Date),y:values,name:`${ticker} · ${name}`,line:{width:.9},hoverinfo:'none'});}const layout=chartLayout(`지표 연구 · ${type}`,{slider:true,selector:true,detail:true});if(type==='price'){layout.yaxis.tickformat=undefined;layout.yaxis.title={text:'기준=100'};const selectedStrategy=$('indicator-strategy').value,cached=await loadPrecomputedResults(),history=(cached[selectedStrategy]||[]).filter(row=>(!start||row.date>=start)&&(!end||row.date<=end));if(history.length){const base=history[0].value,weightText=row=>Object.entries(row.weights||{}).filter(([,weight])=>weight>0.001).map(([ticker,weight])=>`${ticker} ${(weight*100).toFixed(1)}%`).join('<br>');traces.push({x:history.map(row=>row.date),y:history.map(row=>row.value/base-1),name:'전략 누적 수익률',yaxis:'y2',line:{color:'#182433',width:1},customdata:history.map(weightText),hoverinfo:'none'});const assets=Object.keys(history[0].weights||{}),events=history.filter((row,index)=>index>0&&assets.some(asset=>Math.abs((row.weights[asset]||0)-(history[index-1].weights[asset]||0))>=0.02));if(events.length&&[...$('indicator-overlays').querySelectorAll('input:checked')].some(input=>input.value==='rebalances'))traces.push({x:events.map(row=>row.date),y:events.map(row=>row.value/base-1),name:'리밸런싱',yaxis:'y2',mode:'markers',marker:{symbol:'diamond',size:8,color:'#d63939'},customdata:events.map(weightText),hoverinfo:'skip'});layout.yaxis2={title:'전략 수익률',overlaying:'y',side:'right',tickformat:'.0%',showgrid:false};}}Plotly.react('indicator-plot',traces,layout,{responsive:true,displaylogo:false});bindChartTooltip('indicator-plot','indicator');}catch(error){$('status').textContent=`오류: ${error.message}`;}}
async function setupDashboard(){const enabled=definitions.filter(def=>def.strategy.enabled!==false);$('strategy-count').textContent=`${enabled.length}개 전략`;$('strategy-list').innerHTML=enabled.map(def=>`<label><input type="checkbox" value="${def.strategy.id}" checked>${def.strategy.name}</label>`).join('');const data=await loadData(),cached=await loadPrecomputedResults(),dates=Object.values(cached).flatMap(history=>history.map(row=>row.date));$('start-date').value=dates.length?dates.sort()[0]:Object.values(data)[0][0].Date;$('end-date').value=dates.length?dates.sort().at(-1):Object.values(data)[0].at(-1).Date;$('indicator-ticker').innerHTML=Object.keys(data).map(ticker=>`<option>${ticker}</option>`).join('');if(data.QQQ)$('indicator-ticker').value='QQQ';$('run').onclick=runDashboard;$('detail-strategy').onchange=renderDetail;$('start-date').onchange=renderAnalysis;$('end-date').onchange=renderAnalysis;$('analysis-tab').onclick=()=>{$('analysis-view').classList.remove('offline-hidden');$('indicators-view').classList.add('offline-hidden');$('analysis-tab').classList.add('active');$('indicators-tab').classList.remove('active');};$('indicators-tab').onclick=()=>{$('analysis-view').classList.add('offline-hidden');$('indicators-view').classList.remove('offline-hidden');$('analysis-tab').classList.remove('active');$('indicators-tab').classList.add('active');renderIndicators();};$('import').onchange=async event=>{try{const def=parseYaml(await event.target.files[0].text());definitions=definitions.filter(item=>item.strategy.id!==def.strategy.id);definitions.push(def);await setupDashboard();$('status').textContent='YAML 전략을 불러왔습니다. 목록에서 선택해 실행하세요.';}catch(error){$('status').textContent=`YAML 오류: ${error.message}`;}};runDashboard();}
function setupIndicatorControls(){const tickers=$('indicator-ticker'),type=$('indicator-type'),strategy=$('indicator-strategy'),fields=$('indicator-fields'),matrix=$('indicator-matrix'),tabs=$('indicator-panel-tabs'),summary=$('indicator-selection-summary'),data=marketData,labels={price:'가격 · 추세',oscillator:'오실레이터',risk:'리스크',Close:'종가',EMA20:'EMA 20',EMA55:'EMA 55',EMA200:'EMA 200',MA20:'이동평균 20',MA55:'이동평균 55',RSI14:'RSI 14',MACD:'MACD',MACD_SIGNAL:'MACD 신호',DRAWDOWN120:'120일 낙폭',VOL60:'60일 변동성',ATR60:'ATR 60'},escapeHtml=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));tickers.innerHTML=Object.keys(data).map(ticker=>`<option value="${ticker}">${ticker}</option>`).join('');fields.innerHTML=['Close','EMA20','EMA55'].map(field=>`<option value="${field}" selected>${field}</option>`).join('');if(!indicatorSelection.size)for(const field of ['Close','EMA20','EMA55'])indicatorSelection.add(`QQQ|${field}`);const enabled=definitions.filter(def=>def.strategy.enabled!==false);strategy.innerHTML='<option value="">전략 표시 안 함</option>'+enabled.map(def=>`<option value="${escapeHtml(def.strategy.id)}">${escapeHtml(def.strategy.name)}</option>`).join('');if(enabled.length)strategy.value=enabled[0].strategy.id;const dates=Object.values(data).flat().map(row=>row.Date).sort();$('indicator-start-date').value=dates[0]||'';$('indicator-end-date').value=dates.at(-1)||'';const refreshSummary=()=>{const pairs=[...indicatorSelection];summary.innerHTML=pairs.length?pairs.map(key=>{const [ticker,field]=key.split('|');return `<span class="research-indicator-selection-badge">${ticker} · ${labels[field]||field}</span>`;}).join(''):'<span class="research-indicator-selection-empty">선택된 조합 없음</span>';};const drawMatrix=()=>{const panel=type.value,available=Object.keys(data),rows=indicatorFields(panel);tabs.innerHTML=['price','oscillator','risk'].map(value=>`<button type="button" class="btn btn-sm ${value===panel?'btn-primary':'btn-ghost-secondary'}" data-panel="${value}">${labels[value]}</button>`).join('');matrix.innerHTML=`<div class="research-indicator-matrix-scroll"><div class="research-indicator-matrix-table" style="--indicator-ticker-count:${available.length}"><div class="research-indicator-matrix-header"><div class="research-indicator-matrix-corner">지표 / 종목</div>${available.map(ticker=>`<button type="button" class="research-indicator-column-toggle" data-ticker="${ticker}">${ticker}</button>`).join('')}</div>${rows.map(field=>`<div class="research-indicator-matrix-row"><button type="button" class="research-indicator-row-toggle" data-field="${field}">${labels[field]||field}</button>${available.map(ticker=>`<label><input class="indicator-matrix-cell" type="checkbox" value="${ticker}|${field}" ${indicatorSelection.has(`${ticker}|${field}`)?'checked':''}></label>`).join('')}</div>`).join('')}</div></div>`;tabs.querySelectorAll('button').forEach(button=>button.onclick=()=>{type.value=button.dataset.panel;drawMatrix();renderIndicators();});matrix.querySelectorAll('.indicator-matrix-cell').forEach(input=>input.onchange=()=>{input.checked?indicatorSelection.add(input.value):indicatorSelection.delete(input.value);refreshSummary();renderIndicators();});matrix.querySelectorAll('.research-indicator-column-toggle').forEach(button=>button.onclick=()=>{const cells=[...matrix.querySelectorAll(`.indicator-matrix-cell[value^="${button.dataset.ticker}|"]`)],checked=cells.some(input=>input.checked);cells.forEach(input=>{input.checked=!checked;input.checked?indicatorSelection.add(input.value):indicatorSelection.delete(input.value);});refreshSummary();renderIndicators();});matrix.querySelectorAll('.research-indicator-row-toggle').forEach(button=>button.onclick=()=>{const cells=[...matrix.querySelectorAll(`.indicator-matrix-cell[value$="|${button.dataset.field}"]`)],checked=cells.some(input=>input.checked);cells.forEach(input=>{input.checked=!checked;input.checked?indicatorSelection.add(input.value):indicatorSelection.delete(input.value);});refreshSummary();renderIndicators();});};strategy.onchange=renderIndicators;$('indicator-start-date').onchange=renderIndicators;$('indicator-end-date').onchange=renderIndicators;$('indicator-overlays').onchange=renderIndicators;refreshSummary();drawMatrix();}
const baseSetupDashboard=setupDashboard;
setupDashboard=async()=>{await baseSetupDashboard();setupIndicatorControls();};
function localizeIndicatorView(){const cards=document.querySelectorAll('#indicators-view .research-card'),labels=document.querySelectorAll('#indicators-view .form-label');cards[0]?.querySelector('.card-title')&&(cards[0].querySelector('.card-title').textContent='지표 연구');cards[0]?.querySelector('.research-card-description')&&(cards[0].querySelector('.research-card-description').textContent='종목과 지표를 조합해 시장 구간을 분석합니다.');cards[1]?.querySelector('.card-title')&&(cards[1].querySelector('.card-title').textContent='표시 지표');cards[1]?.querySelector('.research-card-description')&&(cards[1].querySelector('.research-card-description').textContent='선택된 종목과 지표 조합');if(labels[0])labels[0].textContent='분석 기간';if(labels[1])labels[1].textContent='전략 표시';if(labels[2])labels[2].textContent='전략 이벤트';const editor=$('indicator-editor');editor?.querySelector('summary span:nth-child(2)')&&(editor.querySelector('summary span:nth-child(2)').textContent='종목 · 지표 편집');const events=$('indicator-overlays');if(events){const eventLabels=events.querySelectorAll('label');if(eventLabels[0])eventLabels[0].lastChild.textContent='상태 구간';if(eventLabels[1])eventLabels[1].lastChild.textContent='리밸런싱';}}
localizeDashboard();
localizeIndicatorView();
setupDashboard().catch(error=>{$('status').textContent=`Error: ${error.message}`;});
"""


HTML = """<!doctype html>
<html lang=\"ko\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Investment Strategy Offline</title>
<style>body{{margin:0;background:#f5f7fb;color:#182433;font:14px system-ui,-apple-system,'Noto Sans KR',sans-serif}}main{{max-width:1100px;margin:auto;padding:32px}}.card{{background:#fff;border:1px solid #dce1e7;border-radius:12px;padding:20px;margin-top:16px;box-shadow:0 2px 5px #1824330d}}h1{{margin:0}}p{{color:#667085}}select,button,input{{font:inherit;padding:9px;border-radius:7px;border:1px solid #cbd5e1}}button{{background:#2563eb;color:#fff;border:0;cursor:pointer}}.row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}#status{{min-height:20px;margin-top:14px;font-weight:600}}canvas{{width:100%;height:340px;margin-top:8px}}</style>
<main><h1>Investment Strategy · Offline</h1><p>모든 데이터와 기본 전략이 이 HTML에 포함되어 있습니다. YAML 파일을 불러와 브라우저 안에서 실행합니다.</p>
<section class=\"card\"><div class=\"row\"><select id=\"strategies\"></select><button id=\"run\">전략 실행</button><label>YAML 가져오기 <input id=\"import\" type=\"file\" accept=\".yaml,.yml,text/yaml\"></label></div><div id=\"status\">전략을 선택해 실행하세요.</div></section>
<section class=\"card\"><canvas id=\"chart\"></canvas></section></main>
<script id=\"strategy-bundle\" type=\"application/json\">__BUNDLE__</script><script>__RUNTIME__</script></html>"""


BROWSER_HTML = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Investment Strategy Research</title><style>__CSS__
.offline-hidden{display:none!important}.offline-main{padding-bottom:48px}.offline-toolbar-actions{display:flex;gap:8px;align-items:center}.offline-date-range{width:342px}.offline-date-range .form-control{min-width:0;font-size:14px;font-weight:600}.offline-summary{overflow:auto;padding:0}.offline-summary table{width:max-content;min-width:100%;border-collapse:collapse}.offline-summary th,.offline-summary td{min-width:84px;padding:10px 12px;border-bottom:1px solid var(--research-border);text-align:right;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}.offline-summary th{height:42px;background:var(--research-bg);color:var(--research-muted);font-weight:700}.offline-summary td{height:38px}.offline-summary th:first-child,.offline-summary td:first-child{position:sticky;left:0;z-index:1;min-width:260px;text-align:left;background:var(--research-surface)}.offline-summary th:first-child{background:var(--research-bg);z-index:2}.offline-plot{height:487px}.offline-detail-plot{height:520px}.research-card-icon svg{width:21px;height:21px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}.offline-range-years{display:flex;justify-content:space-between;padding:0 28px 12px 58px;color:#667085;font-size:11px;line-height:1}.offline-chart-tooltip{position:fixed;z-index:2200;display:none;min-width:184px;max-width:300px;padding:10px 12px;border:1px solid #dce1e7;border-radius:9px;background:rgba(255,255,255,.98);box-shadow:0 10px 24px rgba(24,36,51,.14);color:#182433;font-size:12px;pointer-events:none}.offline-tooltip-date{margin-bottom:6px;color:#667382;font-size:11px}.offline-tooltip-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:16px;padding:2px 0}.offline-tooltip-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.offline-tooltip-row strong{font-variant-numeric:tabular-nums;font-weight:600}.offline-tooltip-separator{height:1px;margin:7px 0;background:#e7eaf0}.offline-tooltip-extra{line-height:1.55}.offline-indicator-controls{display:flex;gap:12px;flex-wrap:wrap;padding:16px 20px}.offline-indicator-controls select{min-width:160px}.offline-file{display:none}.research-view-switch{margin-bottom:20px}.offline-tab{border:0!important;background:transparent!important}.offline-tab.active{background:var(--tbl-primary)!important;color:#fff!important;box-shadow:var(--tbl-box-shadow-sm)}@media(max-width:760px){.research-container{width:min(100% - 24px,1500px)}.research-page-header{align-items:start;flex-direction:column}.research-chart-grid{grid-template-columns:1fr}.offline-plot{height:410px}}</style><script>__PLOTLY__</script>
<body class="research-page"><nav class="research-navbar"><div class="research-container"><div class="research-brand"><span class="research-brand-mark">⌁</span><div><div class="research-brand-name">Investment Strategy</div><div class="research-brand-caption">Quantitative research</div></div></div><div class="research-navbar-actions"><span class="research-status">● Ready</span><button id="theme-toggle" class="btn">◐</button></div></div></nav>
<main class="research-container research-content offline-main"><header class="research-page-header"><div><div class="research-eyebrow">BACKTEST DASHBOARD</div><h1 class="research-title">Strategy Research</h1><p class="research-subtitle">Compare strategy performance and explore indicators in the browser.</p></div><span id="strategy-count" class="badge bg-primary-lt research-count">0 strategies</span></header><div class="nav nav-pills research-view-switch"><button id="analysis-tab" class="nav-link offline-tab active">Performance analysis</button><button id="indicators-tab" class="nav-link offline-tab">Indicator research</button></div>
<section id="analysis-view"><section class="card research-toolbar"><div class="research-toolbar-body"><div class="research-control"><label class="form-label">Analysis period</label><div class="input-group input-group-flat offline-date-range"><input id="start-date" class="form-control" type="date"><span class="input-group-text">—</span><input id="end-date" class="form-control" type="date"></div></div><div class="research-control research-strategy-control"><label class="form-label">Compare strategies</label><div id="strategy-list" class="research-checklist"></div></div><div class="offline-toolbar-actions"><button id="run" class="btn btn-primary">Run</button><label class="btn btn-outline-primary">Import YAML<input id="import" class="offline-file" type="file" accept=".yaml,.yml,text/yaml"></label></div></div><div id="status" class="research-card-description"></div></section><div class="research-chart-grid"><section class="card research-card"><div class="card-header"><div class="research-card-heading"><span class="research-card-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 16l5-5 4 3 5-7 2 2"/></svg></span><div><h2 class="card-title">Cumulative return</h2><p class="research-card-description">Performance normalized to the same starting value</p></div></div></div><div id="performance-plot" class="offline-plot"></div></section><section class="card research-card"><div class="card-header"><div class="research-card-heading"><span class="research-card-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7l4 4 4-3 4 5 4-8"/><path d="M4 18h16"/></svg></span><div><h2 class="card-title">Drawdown path</h2><p class="research-card-description">Decline and recovery from each peak</p></div></div></div><div id="drawdown-plot" class="offline-plot"></div></section></div><section class="card research-card research-detail-card"><div class="card-header research-detail-header"><div><h2 class="card-title">Strategy detail</h2><p class="research-card-description">Cumulative return, allocation and rebalancing events</p></div><div class="research-detail-select"><select id="detail-strategy" class="form-select research-detail-dropdown"></select></div></div><div id="detail-kpis" class="research-kpi-grid research-detail-kpi-grid"></div><div id="detail-plot" class="offline-detail-plot"></div></section><section class="card research-card research-summary"><div class="card-header research-summary-header"><div><h2 class="card-title">Performance summary</h2><p class="research-card-description">Metrics for the selected period</p></div></div><div id="summary" class="offline-summary research-summary-body"></div></section></section>
<section id="indicators-view" class="offline-hidden"><section class="card research-card research-indicator-controls"><div class="card-header"><h2 class="card-title">Indicator research</h2><p class="research-card-description">Price and technical indicators for a selected ticker</p></div><div class="research-indicator-toolbar"><div class="research-indicator-control"><label class="form-label">Analysis period</label><div class="input-group input-group-flat offline-date-range"><input id="indicator-start-date" class="form-control" type="date"><span class="input-group-text">—</span><input id="indicator-end-date" class="form-control" type="date"></div></div><div class="research-indicator-control"><label class="form-label">Strategy display</label><select id="indicator-strategy" class="form-select research-indicator-strategy"></select></div><div class="research-indicator-control"><label class="form-label">Strategy events</label><div id="indicator-overlays" class="research-indicator-checklist"><label><input type="checkbox" value="states" checked>State zones</label><label><input type="checkbox" value="rebalances" checked>Rebalancing</label></div></div></div></section><section class="card research-card research-indicator-selector"><div class="card-header research-indicator-selector-header"><div class="research-indicator-selector-title"><h2 class="card-title">Displayed indicators</h2><p class="research-card-description">Selected ticker and indicator combinations</p></div><div id="indicator-selection-summary" class="research-indicator-selection-summary"></div></div><details id="indicator-editor" class="research-indicator-editor"><summary class="research-indicator-editor-summary"><span class="research-indicator-editor-icon">☷</span><span>Ticker · indicator editor</span><span class="research-indicator-editor-chevron">⌄</span></summary><div id="indicator-panel-tabs" class="research-indicator-editor-toolbar"></div><div id="indicator-matrix" class="research-indicator-matrix-body"></div></details><select id="indicator-ticker" class="offline-hidden" multiple></select><select id="indicator-type" class="offline-hidden"><option value="price">Price and trend</option><option value="oscillator">Oscillator</option><option value="risk">Risk</option></select><select id="indicator-fields" class="offline-hidden" multiple></select></section><section class="card research-card research-indicator-chart-card"><div id="indicator-plot" class="offline-detail-plot"></div></section></section></main><script id="strategy-bundle" type="application/json">__BUNDLE__</script><script>__RUNTIME__</script></body></html>"""


def _compressed_market_data(data_dir: Path) -> bytes:
    data = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(data_dir.glob("*.csv"))
    }
    return gzip.compress(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        compresslevel=9,
    )


def build_bundle(
    data_dir: Path,
    strategy_dir: Path,
    *,
    data_proxy: str | None = None,
    market_data_url: str | None = None,
) -> dict[str, object]:
    strategies = []
    definition_paths: dict[str, Path] = {}
    for path in sorted((*strategy_dir.glob("*.yaml"), *strategy_dir.glob("*.yml"))):
        definition = yaml.safe_load(path.read_text(encoding="utf-8"))
        definition["_yaml_file"] = path.name
        strategies.append(definition)
        strategy_id = str((definition.get("strategy") or {}).get("id") or "")
        if strategy_id:
            definition_paths[strategy_id] = path
    compressed = _compressed_market_data(data_dir)
    precomputed = {}
    for definition in strategies:
        metadata = definition.get("strategy", {})
        name = metadata.get("name")
        strategy_id = metadata.get("id")
        if not name or not strategy_id:
            continue
        history_path = RESULT_DIR / f"{name}_history.csv"
        if not history_path.exists():
            continue
        source_path = definition_paths.get(str(definition.get("source") or ""))
        latest_definition_mtime = max(
            definition_paths[strategy_id].stat().st_mtime,
            source_path.stat().st_mtime if source_path else 0,
        )
        if history_path.stat().st_mtime < latest_definition_mtime:
            continue
        frame = pd.read_csv(history_path, usecols=lambda column: column in {
            "Date", "Portfolio", "Weights", "StrategyState", "TransactionCosts",
        })
        if not {"Date", "Portfolio", "Weights"}.issubset(frame.columns):
            continue
        rows = []
        rebalance_path = RESULT_DIR / f"{name}_rebalances.json"
        try:
            rebalance_events = json.loads(rebalance_path.read_text(
                encoding="utf-8"
            )) if rebalance_path.exists() else []
        except (json.JSONDecodeError, OSError):
            rebalance_events = []
        events_by_date = {
            str(event.get("ExecutionDate") or event.get("Date", ""))[:10]: event
            for event in rebalance_events
            if isinstance(event, dict) and (event.get("ExecutionDate") or event.get("Date"))
        }
        for row in frame.itertuples(index=False):
            values = row._asdict()
            try:
                weights = ast.literal_eval(values["Weights"])
            except (ValueError, SyntaxError):
                continue
            date = str(values["Date"])[:10]
            event = events_by_date.get(date)
            rows.append({
                "date": date,
                "value": float(values["Portfolio"]),
                "weights": weights,
                "state": (
                    "" if pd.isna(values.get("StrategyState"))
                    else str(values.get("StrategyState", ""))
                ),
                "costs": float(values.get("TransactionCosts", 0) or 0),
                "target": event.get("Target") if event else None,
                "preWeights": event.get("PreWeights") if event else None,
                "executionDays": event.get("ExecutionDays") if event else None,
            })
        if rows:
            precomputed[strategy_id] = rows
    compressed_results = gzip.compress(
        json.dumps(precomputed, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        compresslevel=9,
    )
    return {
        "strategies": strategies,
        "data": (
            "" if market_data_url
            else base64.b64encode(compressed).decode("ascii")
        ),
        "data_url": market_data_url or "",
        "market_data_version": hashlib.sha256(compressed).hexdigest()[:16],
        "precomputed_results": base64.b64encode(compressed_results).decode("ascii"),
        "data_proxy": data_proxy or "",
    }


def export_html(
    output: Path,
    *,
    data_dir: Path = DATA_DIR,
    strategy_dir: Path = PROJECT_ROOT / "strategies",
    data_proxy: str | None = None,
    external_market_data: bool = False,
) -> Path:
    """Write an HTML file that does not require a Python server or network."""
    market_data_path = output.with_name(f"{output.stem}.market-data.json.gz")
    bundle = json.dumps(
        build_bundle(
            data_dir,
            strategy_dir,
            data_proxy=data_proxy,
            market_data_url=market_data_path.name if external_market_data else None,
        ),
        ensure_ascii=False,
    )
    assets_dir = Path(__file__).with_name("assets")
    # Tabler provides the dashboard primitives (cards, controls, badges and
    # responsive layout).  Keep it embedded so the generated file is fully
    # usable without a CDN or internet connection.
    css = (assets_dir / "vendor" / "tabler.min.css").read_text(encoding="utf-8")
    css += "\n\n/* Investment Strategy Dash-compatible refinements */\n"
    css += (assets_dir / "research_web.css").read_text(encoding="utf-8")
    plotly_js = (
        Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    ).read_text(encoding="utf-8")
    template = (WEB_SOURCE_DIR / "index.html").read_text(encoding="utf-8")
    runtime = (WEB_SOURCE_DIR / "app.js").read_text(encoding="utf-8")
    document = (
        template.replace("__BUNDLE__", bundle)
        .replace("__CSS_LINK__", "")
        .replace("__RUNTIME__", runtime)
        .replace("__CSS__", css)
        .replace("__PLOTLY__", plotly_js)
        .replace('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/flatpickr.min.css">', '')
        .replace('<script src="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13"></script>', '')
        .replace('<script src="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/l10n/ko.js"></script>', '')
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    if external_market_data:
        market_data_path.write_bytes(_compressed_market_data(data_dir))
    return output


def export_static_site(
    output_dir: Path,
    *,
    strategy_dir: Path = PROJECT_ROOT / "strategies",
    data_proxy: str | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Write a small Pages bundle; market-data fallback is explicit opt-in."""
    strategies_dir = output_dir / "strategies"
    assets_dir = output_dir / "assets"
    strategies_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for path in sorted((*strategy_dir.glob("*.yaml"), *strategy_dir.glob("*.yml"))):
        definition = yaml.safe_load(path.read_text(encoding="utf-8"))
        metadata = definition.get("strategy") or {}
        strategy_id = str(metadata.get("id") or "")
        if not strategy_id:
            raise ValueError(f"strategy.id is required: {path}")
        target = strategies_dir / path.name
        shutil.copy2(path, target)
        manifest.append({
            "id": strategy_id,
            "path": path.name,
            "version": metadata.get("version"),
        })
    (strategies_dir / "manifest.json").write_text(
        json.dumps({"version": 1, "strategies": manifest}, ensure_ascii=False),
        encoding="utf-8",
    )

    source_assets_dir = Path(__file__).with_name("assets")
    css = (source_assets_dir / "vendor" / "tabler.min.css").read_text(
        encoding="utf-8"
    )
    css += "\n\n/* Investment Strategy web application */\n"
    css += (source_assets_dir / "research_web.css").read_text(encoding="utf-8")
    def write_hashed_asset(stem: str, suffix: str, content: bytes) -> str:
        name = f"{stem}.{hashlib.sha256(content).hexdigest()[:12]}{suffix}"
        (assets_dir / name).write_bytes(content)
        return name

    css_name = write_hashed_asset("app", ".css", css.encode("utf-8"))
    plotly_name = write_hashed_asset(
        "plotly", ".min.js",
        (Path(plotly.__file__).parent / "package_data" / "plotly.min.js").read_bytes(),
    )
    runtime_bytes = (WEB_SOURCE_DIR / "app.js").read_bytes()
    runtime_name = write_hashed_asset("app", ".js", runtime_bytes)
    # Keep this source-named copy for downloadable/offline compatibility.
    (output_dir / "app.js").write_bytes(runtime_bytes)
    legacy_tdf_proxy = output_dir / "data" / "TDF2050_PROXY.csv"
    if legacy_tdf_proxy.is_file():
        legacy_tdf_proxy.unlink()
    legacy_strategy_archive = output_dir / "strategies.zip"
    if legacy_strategy_archive.is_file():
        legacy_strategy_archive.unlink()

    market_data_file = output_dir / "market-data.json.gz"
    fallback_dir = output_dir / "market-data"
    data_url = ""
    market_data_version = "on-demand-v2"
    if data_dir is not None:
        compressed = _compressed_market_data(data_dir)
        market_data_file.write_bytes(compressed)
        if fallback_dir.exists():
            shutil.rmtree(fallback_dir)
        fallback_dir.mkdir()
        for source in sorted(data_dir.glob("*.csv")):
            slug = base64.urlsafe_b64encode(source.stem.encode("ascii")).decode("ascii").rstrip("=")
            with gzip.open(fallback_dir / f"{slug}.csv.gz", "wb", compresslevel=9) as target:
                target.write(source.read_bytes())
        data_url = "./market-data.json.gz"
        market_data_version = hashlib.sha256(compressed).hexdigest()[:16]
    else:
        # Hosted Pages deployments use the data proxy.  Remove fallback files
        # left by an earlier offline export so they cannot bloat a deployment.
        if market_data_file.exists():
            market_data_file.unlink()
        if fallback_dir.exists():
            shutil.rmtree(fallback_dir)

    bundle = json.dumps({
        "strategies": [],
        "precomputed_results": "",
        "data": "",
        "data_url": data_url,
        "market_data_version": market_data_version,
        "data_proxy": data_proxy or "",
        "strategy_manifest_url": "./strategies/manifest.json",
        "static_site": True,
    }, ensure_ascii=False)
    template = (WEB_SOURCE_DIR / "index.html").read_text(encoding="utf-8")
    document = (
        template.replace("__BUNDLE__", bundle)
        .replace("__CSS_LINK__", f'<link rel="stylesheet" href="./assets/{css_name}">')
        .replace("__CSS__", "")
        .replace('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/flatpickr.min.css">', '')
        .replace('<script src="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13"></script>', '')
        .replace('<script src="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/l10n/ko.js"></script>', '')
        .replace("<script>__PLOTLY__</script>", f'<script defer src="./assets/{plotly_name}"></script>')
        .replace("<script>__RUNTIME__</script>", f'<script defer src="./assets/{runtime_name}"></script>')
    )
    index = output_dir / "index.html"
    index.write_text(document, encoding="utf-8")
    (output_dir / "_headers").write_text(
        "/assets/*\n  Cache-Control: public, max-age=31536000, immutable\n"
        "/strategies/*\n  Cache-Control: public, max-age=3600\n"
        "/index.html\n  Cache-Control: no-cache\n"
        "/app.js\n  Cache-Control: no-cache\n",
        encoding="utf-8",
    )
    return index


def materialize_web_sources() -> None:
    """Bootstrap editable browser sources from the former embedded templates.

    This one-time migration keeps the complete browser application in regular
    HTML/JavaScript files.  Subsequent UI work belongs in ``src/web``; the
    exporter only injects data and makes the optional single-file package.
    """
    WEB_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_SOURCE_DIR / "index.html").write_text(BROWSER_HTML, encoding="utf-8")
    (WEB_SOURCE_DIR / "app.js").write_text(RUNTIME, encoding="utf-8")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "dist" / "investment-strategy.html")
    parser.add_argument(
        "--materialize-web-sources",
        action="store_true",
        help="create editable HTML and JavaScript source files under src/web",
    )
    parser.add_argument(
        "--data-proxy",
        help="optional deployed data-proxy base URL for downloading missing tickers",
    )
    parser.add_argument(
        "--external-market-data",
        action="store_true",
        help="write market data beside the HTML for faster dashboard startup",
    )
    parser.add_argument(
        "--static-site",
        type=Path,
        help="write a small Pages-ready web bundle to this directory",
    )
    parser.add_argument(
        "--include-market-data-fallback",
        action="store_true",
        help="include full market-data fallback files in a static-site export",
    )
    args = parser.parse_args(argv)
    if args.materialize_web_sources:
        materialize_web_sources()
        print(f"Created editable web sources: {WEB_SOURCE_DIR}")
        return
    if args.static_site:
        path = export_static_site(
            args.static_site,
            data_proxy=args.data_proxy,
            data_dir=DATA_DIR if args.include_market_data_fallback else None,
        )
        print(f"Created static strategy site: {path}")
        return
    path = export_html(
        args.output,
        data_proxy=args.data_proxy,
        external_market_data=args.external_market_data,
    )
    print(f"Created strategy app: {path} ({path.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()

// Pure strategy runtime shared by notification infrastructure.  It has no DOM,
// storage, or network dependencies so it can run in a Cloudflare Worker.
export const TDF2050_PROXY_COMPONENT_WEIGHTS={SPY:.4081,VXUS:.3339,BND:.258};

const scalar=value=>{value=value.trim();if(!value)return null;if((value[0]==='"'&&value.at(-1)==='"')||(value[0]==="'"&&value.at(-1)==="'"))return value.slice(1,-1);if(value==='true')return true;if(value==='false')return false;if(value==='null')return null;if(/^-?\d+(\.\d+)?$/.test(value))return Number(value);if(value[0]==='['&&value.at(-1)===']')return splitInline(value.slice(1,-1)).filter(Boolean).map(scalar);if(value[0]==='{'&&value.at(-1)==='}'){const out={};for(const item of splitInline(value.slice(1,-1))){const at=colonAt(item);out[item.slice(0,at).trim()]=scalar(item.slice(at+1));}return out;}return value;};
function splitInline(text){let out=[],start=0,depth=0,quote='';for(let i=0;i<text.length;i++){const c=text[i];if(quote){if(c===quote&&text[i-1]!=='\\')quote='';}else if(c==='"'||c==="'")quote=c;else if(c==='['||c==='{')depth++;else if(c===']'||c==='}')depth--;else if(c===','&&depth===0){out.push(text.slice(start,i));start=i+1;}}out.push(text.slice(start));return out;}
function colonAt(text){let quote='',depth=0;for(let i=0;i<text.length;i++){const c=text[i];if(quote){if(c===quote&&text[i-1]!=='\\')quote='';}else if(c==='"'||c==="'")quote=c;else if(c==='['||c==='{')depth++;else if(c===']'||c==='}')depth--;else if(c===':'&&depth===0)return i;}return-1;}
function cleanLine(line){let quote='';for(let i=0;i<line.length;i++){const c=line[i];if(quote){if(c===quote&&line[i-1]!=='\\')quote='';}else if(c==='"'||c==="'")quote=c;else if(c==='#')return line.slice(0,i).trimEnd();}return line.trimEnd();}
export function parseYaml(text){const raw=text.replace(/\r/g,'').split('\n').map(cleanLine),lines=[];for(let i=0;i<raw.length;i++){if(!raw[i].trim())continue;const indent=raw[i].match(/^ */)[0].length,body=raw[i].trim();if(body.endsWith('>')||body.endsWith('|')){const key=body.slice(0,-1).trimEnd(),pieces=[];let contentIndent=null;while(i+1<raw.length){const next=raw[i+1],nextIndent=next.match(/^ */)[0].length;if(!next.trim()){i++;continue;}if(nextIndent<=indent)break;if(contentIndent===null)contentIndent=nextIndent;if(nextIndent<contentIndent)break;i++;pieces.push(raw[i].trim());}lines.push({indent,text:key+JSON.stringify(pieces.join(' '))});}else lines.push({indent,text:body});}function node(index,indent){if(index>=lines.length||lines[index].indent<indent)return[null,index];const array=lines[index].indent===indent&&lines[index].text.startsWith('- '),out=array?[]:{};while(index<lines.length&&lines[index].indent===indent&&(array===lines[index].text.startsWith('- '))){let text=lines[index].text;if(array){text=text.slice(2).trim();if(!text){let child;[child,index]=node(index+1,lines[index+1]?.indent);out.push(child);continue;}const at=colonAt(text);if(at>0&&!text.startsWith('{')){const obj={},key=text.slice(0,at).trim(),tail=text.slice(at+1).trim();index++;if(tail)obj[key]=scalar(tail);else{let child;[child,index]=node(index,lines[index]?.indent);obj[key]=child;}if(index<lines.length&&lines[index].indent>indent){let extra;[extra,index]=node(index,lines[index].indent);Object.assign(obj,extra);}out.push(obj);continue;}out.push(scalar(text));index++;continue;}const at=colonAt(text);if(at<1)throw Error(`Invalid YAML: ${text}`);const key=text.slice(0,at).trim(),tail=text.slice(at+1).trim();index++;if(tail)out[key]=scalar(tail);else{let child;[child,index]=node(index,lines[index]?.indent);out[key]=child;}}return[out,index];}return node(0,lines[0]?.indent||0)[0];}
const pct=value=>typeof value==='string'&&/^\s*-?\d+(\.\d+)?%\s*$/.test(value)?Number(value.replace('%',''))/100:Number(value);
// Workers prohibit eval() and new Function().  This deliberately small parser
// supports the strategy DSL's arithmetic, comparisons, booleans, arrays, and
// helper calls without compiling a user-controlled expression.
function expressionTokens(text){const source=String(text).replace(/^\s*=\s*/,''),tokens=[];let index=0;while(index<source.length){const rest=source.slice(index),space=rest.match(/^\s+/);if(space){index+=space[0].length;continue;}const string=rest.match(/^(?:'([^']*)'|"([^"]*)")/);if(string){tokens.push({type:'value',value:string[1]??string[2]??''});index+=string[0].length;continue;}const number=rest.match(/^\d+(?:\.\d+)?%?/);if(number){tokens.push({type:'value',value:number[0].endsWith('%')?Number(number[0].slice(0,-1))/100:Number(number[0])});index+=number[0].length;continue;}const op=rest.match(/^(===|!==|==|!=|>=|<=|&&|\|\||[()+\-*/!,\[\]<>])/);if(op){tokens.push({type:op[0],value:op[0]});index+=op[0].length;continue;}const name=rest.match(/^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*/);if(name){const value=name[0];tokens.push({type:value==='and'?'&&':value==='or'?'||':value==='not'?'!':value==='in'?'in':'name',value});index+=value.length;continue;}throw Error(`Unsupported DSL token near: ${rest.slice(0,16)}`);}tokens.push({type:'end'});return tokens;}
function expressionValue(name,ctx){if(name==='true')return true;if(name==='false')return false;if(name==='null')return null;const parts=name.split('.');let value=Object.prototype.hasOwnProperty.call(ctx.market,parts[0])?ctx.market:ctx;for(const part of parts){if(value===null||value===undefined)return undefined;value=value[part];}return value;}
function evaluate(expression,ctx){if(typeof expression!=='string')return expression;const tokens=expressionTokens(expression);let index=0,peek=()=>tokens[index],take=()=>tokens[index++],accept=type=>peek().type===type?(take(),true):false;const helpers={abs:Math.abs,min:Math.min,max:Math.max,sum:(...values)=>values.reduce((sum,value)=>sum+value,0),count:(...values)=>values.filter(Boolean).length,all:(...values)=>values.every(Boolean),any:(...values)=>values.some(Boolean),round:(value,digits=0)=>Number(value.toFixed(digits)),clamp:(value,low,high)=>Math.max(low,Math.min(high,value)),changed:ctx.changed,previous:ctx.previous,target_deviation:ctx.targetDeviation};function primary(){const token=take();if(token.type==='value')return token.value;if(token.type==='('){const value=parse(0);if(!accept(')'))throw Error('Expected )');return value;}if(token.type==='['){const values=[];if(!accept(']')){do{values.push(parse(0));}while(accept(','));if(!accept(']'))throw Error('Expected ]');}return values;}if(token.type==='name'){if(accept('(')){if((token.value==='changed'||token.value==='previous')&&peek().type==='name'){const field=take().value.split('.').at(-1);if(!accept(')'))throw Error('Expected )');return helpers[token.value](field);}const args=[];if(!accept(')')){do{args.push(parse(0));}while(accept(','));if(!accept(')'))throw Error('Expected )');}const helper=helpers[token.value];if(!helper)throw Error(`Unknown DSL function: ${token.value}`);return helper(...args);}return expressionValue(token.value,ctx);}if(token.type==='-'||token.type==='!')return token.type==='-'?-primary():!primary();throw Error(`Unexpected DSL token: ${token.type}`);}const precedence={'||':1,'&&':2,'==':3,'===':3,'!=':3,'!==':3,'>':3,'>=':3,'<':3,'<=':3,in:3,'+':4,'-':4,'*':5,'/':5};function parse(minimum){let left=primary();while(precedence[peek().type]>=minimum){const op=take().type,right=parse(precedence[op]+1);if(op==='||')left=left||right;else if(op==='&&')left=left&&right;else if(op==='in')left=Array.isArray(right)&&right.includes(left);else if(op==='=='||op==='===')left=left===right;else if(op==='!='||op==='!==')left=left!==right;else if(op==='>')left=left>right;else if(op==='>=')left=left>=right;else if(op==='<')left=left<right;else if(op==='<=')left=left<=right;else if(op==='+')left=left+right;else if(op==='-')left=left-right;else if(op==='*')left=left*right;else left=left/right;}return left;}const result=parse(0);if(peek().type!=='end')throw Error(`Unexpected DSL token: ${peek().type}`);return result;}
const period=(date,check='daily')=>check==='daily'?date:check==='monthly'?date.slice(0,7):check==='quarterly'?date.slice(0,4)+'Q'+(Math.floor((Number(date.slice(5,7))-1)/3)+1):date.slice(0,4)+'W'+Math.floor((Date.parse(date+'T00:00:00Z')-Date.UTC(Number(date.slice(0,4)),0,1))/604800000);
const marketRow=row=>Object.fromEntries(Object.entries(row).filter(([key])=>key!=='Date').map(([key,value])=>[key.toLowerCase(),value===''?NaN:Number(value)]));
function recordsFor(tickers,data){const maps=Object.fromEntries(tickers.map(ticker=>{if(!data[ticker])throw Error(`Missing market data: ${ticker}`);return[ticker,new Map(data[ticker].map(row=>[row.Date,row]))];})),dates=[...maps[tickers[0]].keys()].filter(date=>tickers.every(ticker=>maps[ticker].has(date)));return dates.map(date=>({date,market:Object.fromEntries(tickers.map(ticker=>[ticker,marketRow(maps[ticker].get(date))]))}));}
function addBasicIndicators(rows){const close=rows.map(row=>Number(row.Close)),high=rows.map(row=>Number(row.High)),low=rows.map(row=>Number(row.Low)),mean=(values)=>values.reduce((sum,value)=>sum+value,0)/values.length,rolling=(values,index,period,fn)=>index<period-1?NaN:fn(values.slice(index-period+1,index+1)),ema=span=>{const out=[],alpha=2/(span+1);close.forEach((value,index)=>out.push(index?value*alpha+out[index-1]*(1-alpha):value));return out;},ema20=ema(20),ema55=ema(55),ema200=ema(200),changes=close.map((value,index)=>index?value-close[index-1]:NaN);rows.forEach((row,index)=>{const average=period=>rolling(close,index,period,mean),roc=period=>index<period?NaN:(close[index]/close[index-period]-1)*100,gains=changes.slice(index-13,index+1).map(value=>Math.max(value,0)),losses=changes.slice(index-13,index+1).map(value=>Math.max(-value,0)),avgGain=index<14?NaN:mean(gains),avgLoss=index<14?NaN:mean(losses),ma60=average(60);Object.assign(row,{EMA20:ema20[index],EMA55:ema55[index],EMA200:ema200[index],ROC1:roc(1),ROC5:roc(5),ROC20:roc(20),ROC40:roc(40),ROC60:roc(60),RSI14:Number.isFinite(avgGain)&&Number.isFinite(avgLoss)?(avgLoss===0?100:100-100/(1+avgGain/avgLoss)):NaN,DISPARITY60:index<59?NaN:close[index]/ma60*100,DRAWDOWN120:index<119?NaN:close[index]/rolling(close,index,120,values=>Math.max(...values))-1,EMA20_SLOPE5:index<5?NaN:(ema20[index]/ema20[index-5]-1)*100,EMA200_SLOPE20:index<20?NaN:(ema200[index]/ema200[index-20]-1)*100});});return rows;}
function applyValuation(def,data){const valuation=def.valuation;if(!valuation?.foreign_assets?.length)return data;const fx=data[valuation.fx_ticker];if(!fx)throw Error(`Missing FX data: ${valuation.fx_ticker}`);const rates=new Map(fx.map(row=>[row.Date,Number(row.Close)])),out={...data};for(const ticker of valuation.foreign_assets){const rows=data[ticker];if(!rows)throw Error(`Missing market data: ${ticker}`);let rate=NaN;out[ticker]=rows.map(row=>{if(Number.isFinite(rates.get(row.Date)))rate=rates.get(row.Date);if(!Number.isFinite(rate))throw Error(`Missing FX history for ${ticker}: ${row.Date}`);const copy={...row};for(const field of ['Open','High','Low','Close'])copy[field]=Number(copy[field])*rate;return copy;});addIndicators(out[ticker]);}return out;}
export function addIndicators(rows){
  addBasicIndicators(rows);
  const close=rows.map(row=>Number(row.Close)),high=rows.map(row=>Number(row.High)),low=rows.map(row=>Number(row.Low)),returns=close.map((value,index)=>index?value/close[index-1]-1:NaN);
  const rolling=(values,index,period,fn)=>index<period-1?NaN:fn(values.slice(index-period+1,index+1));
  const sampleStd=values=>{if(values.length<2)return NaN;const mean=values.reduce((sum,value)=>sum+value,0)/values.length;return Math.sqrt(values.reduce((sum,value)=>sum+(value-mean)**2,0)/(values.length-1));};
  const tr=close.map((value,index)=>index===0?high[index]-low[index]:Math.max(high[index]-low[index],Math.abs(high[index]-close[index-1]),Math.abs(low[index]-close[index-1])));
  const atr=tr.map((_,index)=>rolling(tr,index,14,items=>items.reduce((sum,value)=>sum+value,0)/14));
  rows.forEach((row,index)=>Object.assign(row,{
    DRAWDOWN20:index<19?NaN:close[index]/rolling(close,index,20,items=>Math.max(...items))-1,
    DRAWDOWN60:index<59?NaN:close[index]/rolling(close,index,60,items=>Math.max(...items))-1,
    TR:tr[index], ATR:atr[index], ATR_PCT:atr[index]/close[index],
    VOL20:rolling(returns,index,20,items=>sampleStd(items)*Math.sqrt(252)),
    VOL60:rolling(returns,index,60,items=>sampleStd(items)*Math.sqrt(252))
  }));
  return rows;
}
class Portfolio{constructor(commission=.00015,slippage=.0002){this.cash=1;this.positions={};this.commission=commission;this.slippage=slippage;this.pending=null;this.remaining=0;this.history=[];this.rebalances=[];this.active=null;}value(prices){return this.cash+Object.entries(this.positions).reduce((value,[ticker,shares])=>value+shares*prices[ticker],0);}weights(prices){const total=this.value(prices);return Object.fromEntries(Object.keys(prices).map(ticker=>[ticker,(this.positions[ticker]||0)*prices[ticker]/total]));}sameTarget(target,tolerance=1e-6){if(!this.pending)return false;const tickers=new Set([...Object.keys(this.pending),...Object.keys(target)]);return[...tickers].every(ticker=>Math.abs((this.pending[ticker]||0)-(target[ticker]||0))<=tolerance);}trade(ticker,shares,price){if(Math.abs(shares)<1e-8)return;const direction=shares>0?1:-1,execution=price*(1+direction*this.slippage);if(shares>0)shares=Math.min(shares,Math.max(this.cash,0)/(execution*(1+this.commission)));if(Math.abs(shares)<1e-8)return;const notional=shares*execution,fee=Math.abs(notional)*this.commission;this.cash-=notional+fee;this.positions[ticker]=(this.positions[ticker]||0)+shares;}rebalance(prices,target){const total=this.value(prices);for(const[ticker,weight]of Object.entries(target)){const diff=total*weight-(this.positions[ticker]||0)*prices[ticker];if(diff<0)this.trade(ticker,-diff/prices[ticker]*-1,prices[ticker]);}for(const[ticker,weight]of Object.entries(target)){const diff=total*weight-(this.positions[ticker]||0)*prices[ticker];if(diff>0)this.trade(ticker,diff/prices[ticker],prices[ticker]);}}start(target,days,date,reason){if(this.sameTarget(target))return false;this.pending={...target};this.remaining=days;this.active={Date:date,Target:{...target},ExecutionDays:days,Reason:reason};this.rebalances.push(this.active);return true;}update(prices,date){if(!this.pending)return;const current=this.weights(prices);if(this.active&&!this.active.ExecutionDate)this.active.ExecutionDate=date;this.rebalance(prices,Object.fromEntries(Object.entries(this.pending).map(([ticker,weight])=>[ticker,current[ticker]+(weight-current[ticker])/this.remaining])));if(--this.remaining===0){this.pending=null;this.active=null;}}record(date,prices,state){this.history.push({date,value:this.value(prices),weights:this.weights(prices),state});}}
function restorePortfolio(snapshot){const portfolio=new Portfolio();if(snapshot)Object.assign(portfolio,{cash:Number(snapshot.cash),positions:{...(snapshot.positions||{})},pending:snapshot.pending?{...snapshot.pending}:null,remaining:Number(snapshot.remaining||0),active:snapshot.active?{...snapshot.active,Target:{...(snapshot.active.Target||{})}}:null});return portfolio;}
function portfolioSnapshot(portfolio){return{cash:portfolio.cash,positions:{...portfolio.positions},pending:portfolio.pending?{...portfolio.pending}:null,remaining:portfolio.remaining,active:portfolio.active?{...portfolio.active,Target:{...(portfolio.active.Target||{})}}:null};}
function rotationNumber(value){const number=Number(value);return Number.isFinite(number)?number:null;}
function sameRotationMix(left,right){const keys=new Set([...Object.keys(left||{}),...Object.keys(right||{})]);return[...keys].every(key=>Math.abs((left?.[key]||0)-(right?.[key]||0))<=1e-12);}
function selectRotation(rotation,market){
  const sleeve=String(rotation.sleeve),cash=market[sleeve]||{},cashValues=Object.fromEntries(['roc60','roc120','roc252'].map(field=>[field,rotationNumber(cash[field])])),details={};
  if(Object.values(cashValues).some(value=>value===null))return{mix:{[sleeve]:1},selected:[],details};
  const winners=new Map();
  for(const candidate of rotation.candidates||[]){
    const ticker=String(candidate.ticker),row=market[ticker]||{},values=Object.fromEntries(['close','ema200','roc60','roc120','roc252','vol60'].map(field=>[field,rotationNumber(row[field])])),reasons=[];
    if(Object.values(values).some(value=>value===null))reasons.push('필수 추세 지표 부족');
    else if(values.close<=values.ema200)reasons.push('가격이 EMA200 아래');
    else if(values.roc60<=cashValues.roc60)reasons.push('3개월 수익률이 현금 슬리브 이하');
    else if(values.vol60<=0)reasons.push('변동성 계산 불가');
    if(reasons.length){details[ticker]={eligible:false,reasons};continue;}
    const score=.5*(values.roc60-cashValues.roc60)+.3*(values.roc120-cashValues.roc120)+.2*(values.roc252-cashValues.roc252);
    details[ticker]={eligible:true,score,volatility:values.vol60,roc60_excess:values.roc60-cashValues.roc60,roc120_excess:values.roc120-cashValues.roc120,roc252_excess:values.roc252-cashValues.roc252,reasons:['EMA200 상단','3개월 수익률 현금 초과']};
    const prior=winners.get(String(candidate.group));if(!prior||score>prior.score)winners.set(String(candidate.group),{...candidate,score});
  }
  const selectedConfig=[...winners.values()].sort((left,right)=>right.score-left.score).slice(0,Number(rotation.top_n??2)),selectedTickers=new Set(selectedConfig.map(candidate=>String(candidate.ticker))),winnerTickers=new Set([...winners.values()].map(candidate=>String(candidate.ticker)));
  for(const[ticker,detail]of Object.entries(details))if(detail.eligible)detail.selection_status=selectedTickers.has(ticker)?'선택':winnerTickers.has(ticker)?'상위 선택 수 밖':'같은 그룹 내 점수 열위';
  if(!selectedConfig.length)return{mix:{[sleeve]:1},selected:[],details};
  const inverse=Object.fromEntries(selectedConfig.map(candidate=>[candidate.ticker,1/details[candidate.ticker].volatility])),total=Object.values(inverse).reduce((sum,value)=>sum+value,0),maxSingle=pct(rotation.max_single_sleeve_share??.5),maxGold=pct(rotation.max_gold_sleeve_share??.3),maxEquity=pct(rotation.max_equity_sleeve_share??.3),mix={};
  let allocated=0,equity=0;
  for(const candidate of selectedConfig){const ticker=String(candidate.ticker),assetClass=String(candidate.asset_class),capacity=assetClass==='GOLD'?Math.min(maxSingle,maxGold):assetClass==='EQUITY'?Math.min(maxSingle,Math.max(0,maxEquity-equity)):maxSingle,share=Math.min(inverse[ticker]/total,capacity);if(share<=1e-12)continue;mix[ticker]=share;allocated+=share;if(assetClass==='EQUITY')equity+=share;}
  mix[sleeve]=Math.max(0,1-allocated);return{mix,selected:selectedConfig.map(candidate=>String(candidate.ticker)).filter(ticker=>ticker in mix),details};
}
function rotationExplanation(sleeve,previous,selected,mix,details){
  if(!selected.length)return `자동 자산 검토: 적격 후보 없음 — ${sleeve} 유지 (EMA200 상단 및 3개월 현금 초과 조건 필요)`;
  const weights=selected.map(ticker=>`${ticker} ${(mix[ticker]*100).toFixed(1)}%`).join(', '),scores=selected.map(ticker=>`${ticker} 점수 ${details[ticker].score.toFixed(2)}`).join(', ');
  return `자동 자산 교체: ${(previous.length?previous:[sleeve]).join(', ')} → ${selected.join(', ')}; 배분=${weights}; 근거=EMA200 상단·3개월 현금 초과·복합 모멘텀 (${scores})`;
}
class Declarative{constructor(def){this.def=def;this.state={};this.previous={};this.changed=new Set;this.candidates={};this.lastState={};this.lastRebalance={};this.rotationMix={};this.rotationSelected=[];this.rotationLastPeriod=null;this.rotationActive=false;this.rotationDecision=null;for(const[k,v]of Object.entries(def.state||{}))this.state[k]=typeof v.initial==='string'&&v.initial.endsWith('%')?pct(v.initial):v.initial;this.first=true;}snapshot(){return{state:{...this.state},previous:{...this.previous},candidates:{...this.candidates},lastState:{...this.lastState},lastRebalance:{...this.lastRebalance},rotationMix:{...this.rotationMix},rotationSelected:[...this.rotationSelected],rotationLastPeriod:this.rotationLastPeriod,rotationActive:this.rotationActive,rotationDecision:this.rotationDecision,first:this.first};}restore(snapshot){if(!snapshot)return;this.state={...snapshot.state};this.previous={...snapshot.previous};this.candidates={...snapshot.candidates};this.lastState={...snapshot.lastState};this.lastRebalance={...snapshot.lastRebalance};this.rotationMix={...(snapshot.rotationMix||{})};this.rotationSelected=[...(snapshot.rotationSelected||[])];this.rotationLastPeriod=snapshot.rotationLastPeriod??null;this.rotationActive=Boolean(snapshot.rotationActive);this.rotationDecision=snapshot.rotationDecision||null;this.first=Boolean(snapshot.first);}context(market,portfolio,target){const prices=Object.fromEntries(Object.entries(market).map(([t,r])=>[t,r.close])),weights=portfolio.weights(prices);return{market,state:this.state,variables:this.variables||{},parameters:this.def.parameters||{},portfolio:{weight:weights},changed:x=>this.changed.has(String(x).split('.').at(-1)),previous:x=>this.previous[String(x).split('.').at(-1)],targetDeviation:()=>Math.max(...Object.entries(target||{}).map(([t,w])=>Math.abs((weights[t]||0)-w)),0)};}step(date,market,portfolio){this.variables={};let ctx=this.context(market,portfolio);for(const[k,v]of Object.entries(this.def.variables||{}))this.variables[k]=evaluate(v,ctx);this.previous={...this.state};this.changed=new Set;for(const[name,config]of Object.entries(this.def.state||{})){const p=period(date,config.check);if(this.lastState[name]===p)continue;this.lastState[name]=p;ctx=this.context(market,portfolio);const selected=(config.rules||[]).find(rule=>rule.otherwise||evaluate(rule.when,ctx));if(!selected){delete this.candidates[name];continue;}let desired=selected.set;desired=typeof desired==='string'&&desired.startsWith('=')?evaluate(desired,ctx):typeof desired==='string'&&desired.endsWith('%')?pct(desired):desired;if(desired===this.state[name]){delete this.candidates[name];continue;}const candidate=this.candidates[name],days=candidate?.value===desired?candidate.days+1:1;if(days>=Number(selected.confirm||1)){this.state[name]=desired;this.changed.add(name);delete this.candidates[name];}else this.candidates[name]={value:desired,days};}ctx=this.context(market,portfolio);let weights;for(const rule of this.def.target||[]){if(!rule.when||evaluate(rule.when,ctx)){weights=Object.fromEntries(Object.entries(rule.weights).map(([t,v])=>[t,typeof v==='string'&&/^\s*\d+(\.\d+)?%\s*$/.test(v)?pct(v):Number(evaluate(v,ctx))]));break;}}if(!weights)throw Error('No target rule matched');ctx=this.context(market,portfolio,weights);let rebalance=false,days,reason=null;for(let i=0;i<(this.def.rebalance||[]).length;i++){const rule=this.def.rebalance[i],p=period(date,rule.check),prior=this.lastRebalance[i];this.lastRebalance[i]=p;if(this.first||p===prior)continue;if(evaluate(rule.when,ctx)){rebalance=true;days=Number(rule.days===undefined?1:evaluate(rule.days,ctx));reason=`DECLARATIVE_RULE_${i+1}`;break;}}const rotation=this.def.rotation;if(rotation){const sleeve=String(rotation.sleeve),sleeveWeight=Number(weights[sleeve]||0),active=sleeveWeight>1e-12;if(active){const currentPeriod=period(date,rotation.check||'monthly');if(!this.rotationActive||currentPeriod!==this.rotationLastPeriod){const previous=[...this.rotationSelected],selection=selectRotation(rotation,market),changed=!sameRotationMix(selection.mix,this.rotationMix),explanation=rotationExplanation(sleeve,previous,selection.selected,selection.mix,selection.details);this.rotationMix=selection.mix;this.rotationSelected=selection.selected;this.rotationLastPeriod=currentPeriod;this.rotationActive=true;this.rotationDecision={date,reviewed:true,sleeve,sleeve_weight:sleeveWeight,previous_selected:previous,selected:selection.selected,mix:{...selection.mix},candidates:selection.details,explanation};if(changed){rebalance=true;reason=reason?`${reason} | ${explanation}`:explanation;}}}else if(this.rotationActive){const previous=[...this.rotationSelected],changed=Object.entries(this.rotationMix).some(([ticker,weight])=>ticker!==sleeve&&weight>1e-12);this.rotationActive=false;this.rotationLastPeriod=null;this.rotationMix={[sleeve]:1};this.rotationSelected=[];const explanation=`자동 자산 교체 종료: 전략 기본 목표에 따라 ${sleeve} 슬리브가 0%`;this.rotationDecision={date,reviewed:false,sleeve,sleeve_weight:sleeveWeight,previous_selected:previous,selected:[],mix:{[sleeve]:1},candidates:{},explanation};if(changed){rebalance=true;reason=reason?`${reason} | ${explanation}`:explanation;}}if(active){weights={...weights,[sleeve]:sleeveWeight*(this.rotationMix[sleeve]||0)};for(const candidate of rotation.candidates||[]){const ticker=String(candidate.ticker);weights[ticker]=sleeveWeight*(this.rotationMix[ticker]||0);}}}this.first=false;return{target:weights,rebalance,days:days||Number(evaluate((this.def.execution||{}).days||1,ctx)),reason,state:this.state.market_mode||this.state.defense_mode||''};}}
function dependencies(def,all,seen=new Set()){const source=def.source?all.get(def.source):def;if(!source)throw Error(`Unknown source strategy: ${def.source}`);if(seen.has(def.strategy.id))throw Error('Circular source strategy');const base=def.source?dependencies(source,all,new Set([...seen,def.strategy.id])):[...(source.assets?.required||[]),...(source.assets?.observations||[])],products=Object.values(def.products||{}).flatMap(item=>Object.keys(item||{})),tickers=[...new Set([...base,...products])];if(source.valuation?.fx_ticker)tickers.push(source.valuation.fx_ticker);for(const ticker of [...tickers]){if(ticker.endsWith('_KRW')){tickers.push(ticker.slice(0,-4),'KRW=X');}if(/\.(KS|KQ)$/i.test(ticker))tickers.push('KRW=X');}return[...new Set(tickers)];}
function expressionStrings(value){if(value===null||value===undefined)return[];if(typeof value==='string')return[value];if(Array.isArray(value))return value.flatMap(expressionStrings);if(typeof value==='object')return Object.values(value).flatMap(expressionStrings);return[];}
function requiredMarketFields(def){const fields={};const add=(ticker,...names)=>{fields[ticker]??=new Set();for(const name of names)fields[ticker].add(name);};for(const text of expressionStrings([def.variables,def.state,def.target,def.rebalance,def.execution]))for(const match of String(text).matchAll(/\b([A-Z][A-Z0-9_=X-]*)\.([A-Za-z_][\w]*)/g))add(match[1],match[2].toLowerCase());if(def.rotation){add(def.rotation.sleeve,'roc60','roc120','roc252');for(const candidate of def.rotation.candidates||[])add(candidate.ticker,'close','ema200','roc60','roc120','roc252','vol60');}return fields;}
export function mapProductTarget(sourceTarget,def,source,actual){const target={},riskAssets=new Set(source.assets?.risk||[]),riskCap=Number(source.parameters?.canonical_risk_weight??.70);for(const[asset,weight]of Object.entries(sourceTarget)){const products=def.products?.[asset]||{[asset]:1},items=Object.entries(products),currentWeight=items.reduce((sum,[product])=>sum+(actual[product]||0),0),preserveMix=items.length>1&&riskAssets.has(asset)&&currentWeight>riskCap+1e-8&&weight>riskCap+1e-8;let allocated=0;for(const[index,[product,configuredShare]]of items.entries()){const share=preserveMix?(actual[product]||0)/currentWeight:pct(configuredShare),productWeight=index===items.length-1?weight-allocated:weight*share;target[product]=(target[product]||0)+productWeight;allocated+=productWeight;}}return target;}
function preparedRows(calculation,tickers,data,fields,afterDate=''){const valued=applyValuation(calculation,data),signals=calculation.valuation?.signal_currency==='LOCAL'?data:valued,valuedByDate=new Map(recordsFor(tickers,valued).map(row=>[row.date,row.market]));return recordsFor(tickers,signals).filter(row=>row.date>afterDate&&valuedByDate.has(row.date)&&tickers.every(ticker=>[...(fields[ticker]||[])].every(field=>Number.isFinite(Number(row.market[ticker][field]))))).map(row=>({...row,valuationMarket:valuedByDate.get(row.date)}));}
const restoreDeclarativeSnapshot=Declarative.prototype.restore;
Declarative.prototype.restore=function(snapshot){
  if(!snapshot)return;
  const initialState={...this.state};
  restoreDeclarativeSnapshot.call(this,{
    ...snapshot,
    state:{...initialState,...(snapshot.state||{})},
    previous:{...initialState,...(snapshot.previous||{})},
    candidates:{...(snapshot.candidates||{})},
    lastState:{...(snapshot.lastState||{})},
    lastRebalance:{...(snapshot.lastRebalance||{})},
  });
};
function resolve(def,all){
  if(!def.source)return new Declarative(def);
  const source=all.get(def.source),runtime=new Declarative(source);
  return{
    snapshot:()=>runtime.snapshot(),
    restore:snapshot=>runtime.restore(snapshot),
    step(date,market,portfolio){
      const prices=Object.fromEntries(Object.entries(market).map(([ticker,row])=>[ticker,row.close]));
      const actual=portfolio.weights(prices);
      const virtual={weights:()=>Object.fromEntries((source.assets.required||[]).map(asset=>[
        asset,Object.keys(def.products?.[asset]||{[asset]:1}).reduce((sum,product)=>sum+(actual[product]||0),0)
      ]))};
      const signal=runtime.step(date,market,virtual),target=mapProductTarget(signal.target,def,source,actual);
      const sourceContext=signal.notificationContext||{};
      const currentWeights=Object.fromEntries(Object.keys(target).map(ticker=>[ticker,Number(actual[ticker]||0)]));
      const notificationContext={
        ...sourceContext,
        mapped_products:true,
        source_strategy_id:String(def.source),
        source_current_weights:{...(sourceContext.current_weights||{})},
        source_target_weights:{...(sourceContext.target_weights||signal.target)},
        current_weights:currentWeights,
        previous_target_weights:Object.keys(sourceContext.previous_target_weights||{}).length
          ? mapProductTarget(sourceContext.previous_target_weights,def,source,actual)
          : {},
        target_weights:{...target},
        weight_changes:Object.fromEntries(Object.entries(target).map(([ticker,weight])=>[ticker,weight-(currentWeights[ticker]||0)])),
        // The source runtime receives a virtual portfolio whose product weights
        // are aggregated by source asset.  Keep that strategic deviation here;
        // an individual product split must not create a portfolio rebalance.
        target_deviation:Number(sourceContext.target_deviation||0),
      };
      return{...signal,target,notificationContext};
    }
  };
}
function installRotationStability(StrategyClass){
  const baseStep=StrategyClass.prototype.step;
  if(baseStep.__rotationStabilityInstalled)return StrategyClass;
  function step(date,market,portfolio){
    const rotation=this.def.rotation,previousSelected=[...(this.rotationSelected||[])],previousMix={...(this.rotationMix||{})},previousHold=Number(this.rotationHoldPeriods||0),signal=baseStep.call(this,date,market,portfolio),decision=this.rotationDecision;
    if(!rotation||!decision?.reviewed||decision.date!==date){if(rotation&&!this.rotationActive)this.rotationHoldPeriods=0;return signal;}
    let selected=[...decision.selected],mix={...decision.mix};
    const sameAssets=selected.length===previousSelected.length&&selected.every(ticker=>previousSelected.includes(ticker));
    if(sameAssets)selected=[...previousSelected];
    let selectionChanged=selected.length!==previousSelected.length||selected.some(ticker=>!previousSelected.includes(ticker));
    const allIncumbentsEligible=previousSelected.length>0&&previousSelected.every(ticker=>decision.candidates?.[ticker]?.eligible);
    if(selectionChanged&&allIncumbentsEligible){
      const minimumHold=Number(rotation.minimum_hold_periods||0),margin=pct(rotation.switch_score_margin||0),entrants=selected.filter(ticker=>!previousSelected.includes(ticker)),departures=previousSelected.filter(ticker=>!selected.includes(ticker)),bestEntrant=Math.max(...entrants.map(ticker=>Number(decision.candidates?.[ticker]?.score??-Infinity))),weakestDeparture=Math.min(...departures.map(ticker=>Number(decision.candidates?.[ticker]?.score??Infinity)));
      if(previousHold<minimumHold||bestEntrant-weakestDeparture<=margin){selected=[...previousSelected];mix={...previousMix};selectionChanged=false;}
    }
    const minimumWeightChange=pct(rotation.minimum_weight_change||0),tickers=[...new Set([...Object.keys(previousMix),...Object.keys(mix)])],weightChange=Math.max(...tickers.map(ticker=>Math.abs(Number(mix[ticker]||0)-Number(previousMix[ticker]||0))),0);
    const changed=selectionChanged||weightChange>Math.max(minimumWeightChange,1e-12);
    if(!selectionChanged&&!changed&&Object.keys(previousMix).length)mix={...previousMix};
    this.rotationSelected=selected;this.rotationMix=mix;this.rotationHoldPeriods=!selectionChanged&&previousSelected.length?previousHold+1:selected.length?1:0;
    const sleeve=String(rotation.sleeve),sleeveWeight=Number(decision.sleeve_weight||0),explanation=changed?rotationExplanation(sleeve,previousSelected,selected,mix,decision.candidates):`자동 자산 교체 검토: 변경 폭이 기준(${(minimumWeightChange*100).toFixed(1)}%p) 이하여서 기존 구성 유지`;
    this.rotationDecision={...decision,selected:[...selected],mix:{...mix},explanation};
    const target={...signal.target,[sleeve]:sleeveWeight*(mix[sleeve]||0)};
    for(const candidate of rotation.candidates||[])target[String(candidate.ticker)]=sleeveWeight*(mix[String(candidate.ticker)]||0);
    const declarative=String(signal.reason||'').split(/\s*\|\s*/).find(part=>/^DECLARATIVE_RULE_\d+$/.test(part))||null,reason=[declarative,changed?explanation:null].filter(Boolean).join(' | ')||null;
    return{...signal,target,rebalance:Boolean(declarative)||changed,reason};
  }
  step.__rotationStabilityInstalled=true;StrategyClass.prototype.step=step;
  const baseSnapshot=StrategyClass.prototype.snapshot,baseRestore=StrategyClass.prototype.restore;
  if(baseSnapshot)StrategyClass.prototype.snapshot=function(){return{...baseSnapshot.call(this),rotationHoldPeriods:Number(this.rotationHoldPeriods||0)};};
  if(baseRestore)StrategyClass.prototype.restore=function(snapshot){baseRestore.call(this,snapshot);this.rotationHoldPeriods=Number(snapshot?.rotationHoldPeriods||0);};
  return StrategyClass;
}
function installFinalTargetRebalance(StrategyClass){
  const baseStep=StrategyClass.prototype.step;
  if(baseStep.__finalTargetRebalanceInstalled)return StrategyClass;
  function step(date,market,portfolio){
    const signal=baseStep.call(this,date,market,portfolio),match=String(signal.reason||'').match(/^DECLARATIVE_RULE_(\d+)(?:\s*\|\s*(.*))?$/);
    if(!match)return signal;
    const rule=this.def.rebalance?.[Number(match[1])-1];
    if(!rule||evaluate(rule.when,this.context(market,portfolio,signal.target)))return signal;
    const remainingReason=match[2]||null;
    return{...signal,rebalance:Boolean(remainingReason),reason:remainingReason};
  }
  step.__finalTargetRebalanceInstalled=true;
  StrategyClass.prototype.step=step;
  return StrategyClass;
}

const finiteNumber=value=>Number.isFinite(Number(value))?Number(value):null;

function inferredExecutionThreshold(definition){
  for(const rule of definition.rebalance||[]){const match=String(rule.when||'').match(/target_deviation\(\)\s*>=\s*(\d+(?:\.\d+)?)%/);if(match)return Number(match[1])/100;}
  return null;
}

function notificationPolicy(definition){
  if(definition.notifications)return definition.notifications;
  const threshold=inferredExecutionThreshold(definition),states={};
  for(const[name,config]of Object.entries(definition.state||{})){
    const values=[...new Set((config.rules||[]).map(rule=>rule.set).filter(value=>typeof value!=='string'||!value.startsWith('=')))];
    if(values.length)states[name]={label:name};
  }
  const variables=Object.fromEntries(Object.keys(definition.variables||{}).map(name=>[name,{label:name}]));
  const representative=(Array.isArray(definition.assets?.risk)?definition.assets.risk[0]:definition.assets?.risk)||(definition.assets?.required||[])[0];
  return{
    weekly:true,states,variables,
    market:representative?[
      {ticker:representative,field:'roc1',label:'1일',format:'percent'},
      {ticker:representative,field:'roc5',label:'5일',format:'percent'},
      {ticker:representative,field:'roc20',label:'20일',format:'percent'},
      {ticker:representative,field:'drawdown120',label:'120일 고점 대비',format:'ratio_percent'},
    ]:[],
    prealerts:threshold?[{id:'target-deviation',when:`target_deviation() >= ${threshold*2/3}`,reset_when:`target_deviation() < ${threshold*8/15}`,message:`목표 비중 괴리가 ${((threshold*2/3)*100).toFixed(1)}%p에 도달 (리밸런싱 조건 ${threshold*100}%p)`}]:[],
  };
}

function notificationDisplay(policy,stateValues,variables,market){
  return{
    weekly:policy.weekly!==false,
    states:Object.entries(policy.states||{}).filter(([name])=>Object.prototype.hasOwnProperty.call(stateValues,name)).map(([name,item])=>({name,label:item.label,value:stateValues[name]})),
    variables:Object.entries(policy.variables||{}).filter(([name])=>Object.prototype.hasOwnProperty.call(variables,name)).map(([name,item])=>({name,label:item.label,value:variables[name],max:item.max??null,decimals:item.decimals??0})),
    market:(policy.market||[]).map(item=>({ticker:item.ticker,field:item.field,label:item.label,format:item.format||'number',decimals:item.decimals??1,value:finiteNumber(market[item.ticker]?.[String(item.field).toLowerCase()])})).filter(item=>item.value!==null),
  };
}

function requiredConfirmationDays(definition,name,value){
  const rules=definition.state?.[name]?.rules||[];
  return Math.max(1,...rules.filter(rule=>String(rule.set)===String(value)).map(rule=>Number(rule.confirm||1)));
}

function notificationAlertTargets(alert){return Array.isArray(alert?.to)?alert.to:[alert?.to];}
function notificationAlertMatches(alert,event,value){
  return String(alert?.on||'changed')===event&&notificationAlertTargets(alert).some(target=>target==='*'||String(target)===String(value));
}

function transitionExplanation(change,policy){
  const configured=policy?.states?.[change.name],alerts=configured?.alerts||[];
  const changed=alerts.filter(item=>notificationAlertMatches(item,'changed',change.current));
  const exact=changed.find(item=>Object.prototype.hasOwnProperty.call(item,'from')&&String(item.from)===String(change.previous));
  const general=changed.find(item=>!Object.prototype.hasOwnProperty.call(item,'from'));
  const transition=`${configured?.label||change.name}: ${change.previous} → ${change.current}`,message=(exact||general)?.message;
  return message?`${transition} · ${message}`:transition;
}

function installNotificationContext(StrategyClass){
  const baseStep=StrategyClass.prototype.step;
  if(baseStep.__notificationContextInstalled)return StrategyClass;
  function step(date,market,portfolio){
    const previousState={...(this.state||{})},previousCandidates={...(this.candidates||{})};
    const previousTargetWeights={...(this.notificationTargetWeights||{})};
    const signal=baseStep.call(this,date,market,portfolio);
    const holdingTickers=this.def.assets?.required||Object.keys(signal.target||{});
    const prices=Object.fromEntries(holdingTickers.filter(ticker=>market[ticker]).map(ticker=>[ticker,market[ticker].close]));
    const currentWeights=portfolio.weights(prices),targetWeights={...(signal.target||{})};
    const targetDeviation=Math.max(...Object.entries(targetWeights).map(([ticker,weight])=>Math.abs(Number(currentWeights[ticker]||0)-Number(weight))),0);
    const stateChanges=Object.keys(this.state||{}).filter(name=>previousState[name]!==this.state[name]).map(name=>({name,previous:previousState[name],current:this.state[name]}));
    const confirmations=Object.entries(this.candidates||{}).map(([name,candidate])=>({
      name,desired:candidate.value,days:Number(candidate.days||0),required_days:requiredConfirmationDays(this.def,name,candidate.value),
    }));
    const confirmationStarted=confirmations.filter(item=>{
      const previous=previousCandidates[item.name];
      return item.days===1&&(!previous||previous.value!==item.desired);
    });
    const qqq=market.QQQ||{},spy=market.SPY||{};
    const marketSummary={
      qqq:{
        close:finiteNumber(qqq.close),roc1:finiteNumber(qqq.roc1),roc5:finiteNumber(qqq.roc5),
        roc20:finiteNumber(qqq.roc20),roc60:finiteNumber(qqq.roc60),drawdown120:finiteNumber(qqq.drawdown120),
        ema20:finiteNumber(qqq.ema20),ema55:finiteNumber(qqq.ema55),ema200:finiteNumber(qqq.ema200),
        valuation_score:finiteNumber(qqq.valuation_score),
      },
      spy:{close:finiteNumber(spy.close),roc5:finiteNumber(spy.roc5),ema20:finiteNumber(spy.ema20)},
    };
    const policy=notificationPolicy(this.def);
    const reasons=stateChanges.filter(change=>policy.states?.[change.name]).map(change=>transitionExplanation(change,policy));
    const materialStateChange=stateChanges.some(change=>allocationRelevantStateChange(change,policy));
    if(signal.rebalance&&!materialStateChange){
      if(this.variables?.structural_bear)reasons.push('구조적 약세 조건이 충족되어 방어 목표 적용');
      else if(String(signal.reason||'').includes('자동 자산'))reasons.push(String(signal.reason).replace(/^DECLARATIVE_RULE_\d+\s*\|\s*/,''));
      else reasons.push(`목표 비중 괴리가 실행 기준에 도달 (${(targetDeviation*100).toFixed(1)}%p)`);
    }
    const ctx=this.context(market,portfolio,targetWeights);
    const prealerts=(policy.prealerts||[]).map(rule=>({id:String(rule.id),message:String(rule.message),matched:Boolean(evaluate(rule.when,ctx)),reset:Boolean(evaluate(rule.reset_when,ctx))}));
    const notificationContext={
      date,
      state_values:{...(this.state||{})},
      state_changes:stateChanges,
      variables:Object.fromEntries(Object.entries(this.variables||{}).filter(([,value])=>['number','boolean','string'].includes(typeof value))),
      confirmations,
      confirmation_started:confirmationStarted,
      market:marketSummary,
      current_weights:Object.fromEntries(Object.keys(targetWeights).map(ticker=>[ticker,Number(currentWeights[ticker]||0)])),
      previous_target_weights:previousTargetWeights,
      target_weights:targetWeights,
      weight_changes:Object.fromEntries(Object.entries(targetWeights).map(([ticker,weight])=>[ticker,Number(weight)-Number(currentWeights[ticker]||0)])),
      target_deviation:targetDeviation,
      target_changed:Object.keys(previousTargetWeights).length>0&&![...new Set([...Object.keys(previousTargetWeights),...Object.keys(targetWeights)])].every(ticker=>Math.abs(Number(previousTargetWeights[ticker]||0)-Number(targetWeights[ticker]||0))<=1e-12),
      rebalance_required:Boolean(signal.rebalance),
      execution_days:Number(signal.days||1),
      reason_text:reasons.join(' · '),
      reason_details:reasons,
      notification_policy:policy,
      notification_display:notificationDisplay(policy,this.state||{},this.variables||{},market),
      prealerts,
      mapped_products:false,
    };
    this.notificationTargetWeights={...targetWeights};
    return{...signal,notificationContext};
  }
  step.__notificationContextInstalled=true;
  StrategyClass.prototype.step=step;
  const baseSnapshot=StrategyClass.prototype.snapshot,baseRestore=StrategyClass.prototype.restore;
  if(baseSnapshot)StrategyClass.prototype.snapshot=function(){return{...baseSnapshot.call(this),notificationTargetWeights:{...(this.notificationTargetWeights||{})}};};
  if(baseRestore)StrategyClass.prototype.restore=function(snapshot){baseRestore.call(this,snapshot);this.notificationTargetWeights={...(snapshot?.notificationTargetWeights||{})};};
  return StrategyClass;
}

installRotationStability(Declarative);
installFinalTargetRebalance(Declarative);
installNotificationContext(Declarative);

function valuationWeightPortfolio(portfolio,valuationMarket){
  const prices=Object.fromEntries(Object.entries(valuationMarket).map(([ticker,row])=>[ticker,row.close]));
  return{weights:()=>portfolio.weights(prices)};
}

export function strategyTickers(definitions,definition){return dependencies(definition,new Map(definitions.map(def=>[def.strategy.id,def])));}
export function runStrategy(definitions,definition,data){
  const all=new Map(definitions.map(def=>[def.strategy.id,def])),tickers=dependencies(definition,all),calculation=definition.source?all.get(definition.source):definition,fields=requiredMarketFields(calculation),rows=preparedRows(calculation,tickers,data,fields);
  if(!rows.length)throw Error('No common market-data period remains after indicator warm-up.');
  const runtime=resolve(definition,all),portfolio=new Portfolio();
  for(const row of rows){
    const opens=Object.fromEntries(Object.entries(row.valuationMarket).map(([t,v])=>[t,v.open]));
    portfolio.update(opens,row.date);
    const signal=runtime.step(row.date,row.market,valuationWeightPortfolio(portfolio,row.valuationMarket));
    if(!portfolio.history.length)portfolio.start(signal.target,signal.days,row.date,signal.reason||'INITIAL');
    else if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,signal.reason||'RULE');
    portfolio.record(row.date,Object.fromEntries(Object.entries(row.valuationMarket).map(([t,v])=>[t,v.close])),signal.state);
  }
  const events=new Map(portfolio.rebalances.map(event=>[event.ExecutionDate||event.Date,event]));
  return portfolio.history.map(row=>events.has(row.date)?{...row,target:events.get(row.date).Target,executionDays:events.get(row.date).ExecutionDays,reason:events.get(row.date).Reason}:row);
}
export function strategySnapshot(definitions,definition,data){
  const all=new Map(definitions.map(def=>[def.strategy.id,def])),tickers=dependencies(definition,all),calculation=definition.source?all.get(definition.source):definition,fields=requiredMarketFields(calculation),rows=preparedRows(calculation,tickers,data,fields);
  if(!rows.length)throw Error('No common market-data period remains after indicator warm-up.');
  const runtime=resolve(definition,all),portfolio=new Portfolio();let last;
  for(const row of rows){
    const opens=Object.fromEntries(Object.entries(row.valuationMarket).map(([ticker,value])=>[ticker,value.open]));
    portfolio.update(opens,row.date);
    const signal=runtime.step(row.date,row.market,valuationWeightPortfolio(portfolio,row.valuationMarket));
    if(!last)portfolio.start(signal.target,signal.days,row.date,'INITIAL');else if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,'RULE');
    const prices=Object.fromEntries(Object.entries(row.valuationMarket).map(([ticker,value])=>[ticker,value.close]));
    portfolio.record(row.date,prices,signal.state);last={date:row.date,prices,notification_context:signal.notificationContext};
  }
  return{...last,portfolio:portfolioSnapshot(portfolio),runtime:runtime.snapshot()};
}
export function runStrategyIncremental(definitions,definition,data,snapshot){
  const all=new Map(definitions.map(def=>[def.strategy.id,def])),tickers=dependencies(definition,all),calculation=definition.source?all.get(definition.source):definition,fields=requiredMarketFields(calculation),rows=preparedRows(calculation,tickers,data,fields,snapshot.date),runtime=resolve(definition,all),portfolio=restorePortfolio(snapshot.portfolio);
  runtime.restore(snapshot.runtime);const history=[];
  for(const row of rows){
    const opens=Object.fromEntries(Object.entries(row.valuationMarket).map(([ticker,value])=>[ticker,value.open]));
    portfolio.update(opens,row.date);
    const signal=runtime.step(row.date,row.market,valuationWeightPortfolio(portfolio,row.valuationMarket));
    if(signal.rebalance)portfolio.start(signal.target,signal.days,row.date,signal.reason||'RULE');
    const prices=Object.fromEntries(Object.entries(row.valuationMarket).map(([ticker,value])=>[ticker,value.close]));
    portfolio.record(row.date,prices,signal.state);
    history.push({date:row.date,weights:portfolio.weights(prices),state:signal.state,target:signal.rebalance?signal.target:null,executionDays:signal.rebalance?signal.days:null,reason:signal.rebalance?(signal.reason||'RULE'):null,notificationContext:signal.notificationContext});
    snapshot={...snapshot,date:row.date,prices,portfolio:portfolioSnapshot(portfolio),runtime:runtime.snapshot(),notification_context:signal.notificationContext};
  }
  return{history,snapshot};
}

function allocationRelevantStateChange(change,policy){
  const configured=policy?.states?.[change.name];
  if(configured&&!Object.prototype.hasOwnProperty.call(configured,'alerts'))return true;
  if(configured)return(configured.alerts||[]).some(item=>notificationAlertMatches(item,'changed',change.current)&&(!Object.prototype.hasOwnProperty.call(item,'from')||String(item.from)===String(change.previous)));
  if(change.name==='defense_mode')return true;
  if(change.name!=='trend_mode'&&change.name!=='market_mode')return false;
  return [change.previous,change.current].some(value=>value==='BEAR'||value==='RECOVERY');
}

function criticalConfirmation(item,stateValues,policy){
  if(policy?.states)return(policy.states[item.name]?.alerts||[]).some(alert=>notificationAlertMatches(alert,'confirmation_started',item.desired));
  if(['BEAR','DEFENSE','RECOVERY','NORMAL'].includes(String(item.desired)))return true;
  return String(item.desired)==='BULL'&&String(stateValues.trend_mode||stateValues.market_mode)==='RECOVERY';
}

function confirmationExplanation(item,policy){
  const configured=policy?.states?.[item.name],alerts=configured?.alerts||[];
  const alert=alerts.find(rule=>notificationAlertMatches(rule,'confirmation_started',item.desired));
  const confirmation=`${configured?.label||item.name}: ${item.desired} 확인 시작 (${item.days}/${item.required_days}일)`;
  return alert?.message?`${confirmation} · ${alert.message}`:confirmation;
}

export function selectNotificationAlerts(history,notificationState={}){
  let deviationArmed=Boolean(notificationState.deviation_armed),armedRules={...(notificationState.armed_rules||{})},latestContext=notificationState.latest_context||null;
  const alerts=[];
  for(const row of history){
    const context=row.notificationContext||{},deviation=Number(context.target_deviation||0),policy=context.notification_policy;
    const ruleDetails=[];
    if(context.prealerts){for(const rule of context.prealerts){if(rule.reset)armedRules[rule.id]=false;if(rule.matched&&!armedRules[rule.id]){ruleDetails.push(rule.message);armedRules[rule.id]=true;}}}
    else if(notificationState.deviation_armed!==undefined){deviationArmed=Boolean(notificationState.deviation_armed);}
    const relevantChanges=(context.state_changes||[]).filter(change=>allocationRelevantStateChange(change,policy));
    const confirmationStarts=(context.confirmation_started||[]).filter(item=>criticalConfirmation(item,context.state_values||{},policy));
    const details=[];
    for(const change of relevantChanges)details.push(transitionExplanation(change,policy));
    for(const item of confirmationStarts)details.push(confirmationExplanation(item,policy));
    details.push(...ruleDetails);
    const actionable=Boolean(row.target);
    if(actionable||details.length){
      const reasonDetails=[...(context.reason_details||[]),...details].filter((value,index,items)=>value&&items.indexOf(value)===index);
      alerts.push({
        type:actionable?'REBALANCE':'PREALERT',
        market_data_at:row.date,
        state_values:{...(context.state_values||{})},
        state_changes:context.state_changes||[],
        reason:row.reason||null,
        reason_text:reasonDetails.join(' · ')||context.reason_text||'',
        execution_days:actionable?row.executionDays:null,
        current_weights:{...(context.current_weights||{})},
        previous_target_weights:{...(context.previous_target_weights||{})},
        target_weights:{...(actionable?row.target:context.target_weights||{})},
        weight_changes:{...(context.weight_changes||{})},
        target_deviation:deviation,
        target_changed:Boolean(context.target_changed),
        variables:{...(context.variables||{})},
        confirmations:context.confirmations||[],
        market:context.market||{},
        notification_display:context.notification_display||null,
        mapped_products:Boolean(context.mapped_products),
        source_strategy_id:context.source_strategy_id||null,
        source_current_weights:context.source_current_weights||null,
        source_target_weights:context.source_target_weights||null,
      });
    }
    latestContext=context;
  }
  return{alerts,state:{deviation_armed:deviationArmed,armed_rules:armedRules,latest_context:latestContext}};
}
export function buildTdf2050Proxy(components){const tickers=Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS),maps=Object.fromEntries(tickers.map(t=>[t,new Map(components[t].map(row=>[row.Date,row]))])),dates=[...maps[tickers[0]].keys()].filter(date=>tickers.every(t=>maps[t].has(date))).sort(),weights={...TDF2050_PROXY_COMPONENT_WEIGHTS},target={...weights},out=[];let previous=null,value=100,month='';for(let i=1;i<dates.length;i++){const date=dates[i],market=Object.fromEntries(tickers.map(t=>[t,maps[t].get(dates[i-1])])),nextMonth=date.slice(0,7);if(!previous){out.push({Date:date,Open:100,High:100,Low:100,Close:100,Volume:0});previous=market;month=nextMonth;continue;}if(month!==nextMonth)Object.assign(weights,target);const price=field=>value*tickers.reduce((sum,t)=>sum+weights[t]*Number(market[t][field])/Number(previous[t].Close),0),open=price('Open'),close=price('Close');out.push({Date:date,Open:open,High:Math.max(price('High'),open,close),Low:Math.min(price('Low'),open,close),Close:close,Volume:0});const contributions=Object.fromEntries(tickers.map(t=>[t,weights[t]*Number(market[t].Close)/Number(previous[t].Close)])),total=Object.values(contributions).reduce((a,b)=>a+b,0);for(const t of tickers)weights[t]=contributions[t]/total;previous=market;value=close;month=nextMonth;}return addIndicators(out);}

import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const source=readFileSync(new URL('../src/web/app.js',import.meta.url),'utf8');
const template=readFileSync(new URL('../src/web/index.html',import.meta.url),'utf8');

function extractedRecordsFor(){
  const marketRow=source.match(/function marketRow\(row\) \{[^\n]+\}/)?.[0];
  const recordsFor=source.match(/recordsFor=function\(tickers,data,optionalTickers=new Set\(\)\)\{[\s\S]*?\n\};/)?.[0];
  assert.ok(marketRow&&recordsFor,'recordsFor runtime source was not found');
  return Function(`let recordsFor;${marketRow};${recordsFor};return recordsFor;`)();
}

function extractedMetrics(){
  const metrics=source.match(/function metrics\(history\)\{[^\n]+\}/)?.[0];
  assert.ok(metrics,'metrics runtime source was not found');
  return Function(`${metrics};return metrics;`)();
}

function extractedFinalTargetGuard(){
  const install=source.match(/function installFinalTargetRebalance\(StrategyClass\)\{[^\n]+\}/)?.[0];
  assert.ok(install,'final target rebalance guard source was not found');
  return Function(`const evaluate=(expression,context)=>context.finalDeviation;${install};return installFinalTargetRebalance;`)();
}

function extractedRotationInstaller(){
  const install=source.match(/function installRotation\(StrategyClass\)\{[^\n]+\}/)?.[0];
  assert.ok(install,'rotation installer source was not found');
  return Function(`${install};return installRotation;`)();
}

function extractedRotationSuspensionInstaller(){
  const install=source.match(/function installRotationSuspension\(StrategyClass\)\{[^\n]+\}/)?.[0];
  assert.ok(install,'rotation suspension installer source was not found');
  return Function('evaluate','period',`${install};return installRotationSuspension;`)(
    (expression)=>Boolean(expression),
    (date)=>String(date).slice(0,7),
  );
}

function extractedVisibleYAutoscale(){
  const axisKey=source.match(/function axisLayoutKey\(axis\)\{[^\n]+\}/)?.[0];
  const range=source.match(/function visibleAxisRange\(values\)\{[^\n]+\}/)?.[0];
  const bind=source.match(/function bindVisibleYAutoscale\(plotId\)\{[^\n]+\}/)?.[0];
  assert.ok(axisKey&&range&&bind,'visible y-axis autoscale source was not found');
  return Function('$','Plotly',`${axisKey};${range};${bind};return bindVisibleYAutoscale;`);
}

function extractedQqqCandleRows(){
  const helper=source.match(/function qqqCandleRows\(rows,timeframe\)\{[\s\S]*?\n\}/)?.[0];
  assert.ok(helper,'QQQ candle aggregation source was not found');
  return Function(`${helper};return qqqCandleRows;`)();
}

function extractedTooltipIndicatorValue(){
  const number=source.match(/const tooltipNumber=[^\n]+/)?.[0];
  const formatter=source.match(/const tooltipIndicatorValue=[^\n]+/)?.[0];
  assert.ok(number&&formatter,'indicator tooltip formatter source was not found');
  return Function(`${number};${formatter};return tooltipIndicatorValue;`)();
}

test('candlestick tooltip shows OHLC and suppresses the native hover popup',()=>{
  const format=extractedTooltipIndicatorValue();
  const value=format({
    data:{type:'candlestick'},customdata:{open:100,high:103,low:99,close:102},
  });

  assert.equal(value,'시 100.00\n고 103.00\n저 99.00\n종 102.00');
  assert.match(source,/type:'candlestick'.*hoverinfo:'none'/);
});

test('QQQ candle controls live in the indicator chart header',()=>{
  const chartStart=template.indexOf('research-indicator-chart-card');
  const candleControl=template.indexOf('id="indicator-candles"');
  assert.ok(chartStart>=0&&candleControl>chartStart);
  assert.match(template,/research-indicator-chart-header/);
  assert.match(template,/type="radio" name="indicator-candle-timeframe" value="daily"/);
  assert.match(template,/표시 안 함/);
  assert.doesNotMatch(template,/QQQ 봉 표시/);
});

test('candlestick mode hides the duplicate QQQ close line and owns tooltip date',()=>{
  assert.match(source,/const closeIndex=plot\.data\.indexOf\(priceTrace\);/);
  assert.match(source,/await Plotly\.deleteTraces\(plot,closeIndex\)/);
  assert.match(source,/const candle=\(event\.points\|\|\[\]\)\.find\(point=>point\.data\?\.type==='candlestick'\)/);
  assert.match(source,/const candleIndex=Number\.isInteger\(candle\.pointNumber\)\?candle\.pointNumber:candle\.pointIndex/);
  assert.match(source,/candle\.data\?\.x\?\.\[candleIndex\]\|\|candle\.x/);
  assert.match(source,/heading\.textContent=date/);
  assert.match(source,/if\(!candle\)\{/);
  assert.match(source,/if\(\/종가\|Close\/i\.test\(label\)\)return 0/);
  assert.match(source,/1000-period/);
  assert.match(source,/name:`\$\{displayTicker\} · \$\{label\}`,showlegend:false/);
  assert.match(source,/trace\.type==='candlestick'\?\[trace\.low\?\.\[index\],trace\.high\?\.\[index\]\]/);
});

test('QQQ candles aggregate daily rows into weekly and monthly OHLC',()=>{
  const aggregate=extractedQqqCandleRows(),rows=[
    {Date:'2024-01-29',Open:100,High:103,Low:99,Close:102},
    {Date:'2024-01-30',Open:102,High:105,Low:101,Close:104},
    {Date:'2024-02-01',Open:104,High:106,Low:100,Close:101},
    {Date:'2024-02-05',Open:101,High:108,Low:100,Close:107},
  ];

  assert.equal(aggregate(rows,'daily'),rows);
  assert.deepEqual(aggregate(rows,'monthly'),[
    {Date:'2024-01-30',Open:100,High:105,Low:99,Close:104},
    {Date:'2024-02-05',Open:104,High:108,Low:100,Close:107},
  ]);
  assert.equal(aggregate(rows,'weekly').length,2);
});

test('single-ticker charts add default moving averages and a daily candle',()=>{
  assert.match(source,/const AUTO_QQQ_MOVING_AVERAGES=\['MA20','MA55','MA120','MA200'\]/);
  assert.match(source,/const candleTicker=selectedTickers\.length===1\?selectedTickers\[0\]:''/);
  assert.match(source,/candleTicker&&!explicitlySelectedMovingAverages\.length/);
  assert.match(source,/hasSavedCandleState\?\(state\.indicatorCandles\|\|\[\]\):\['daily'\]/);
  assert.match(source,/if\(candleRow\)grid\.prepend\(candleRow\)/);
});

test('restored candle selection redraws an initially visible indicator view',()=>{
  assert.match(source,/if\(!\$\('indicators-view'\)\.classList\.contains\('offline-hidden'\)\)await renderIndicators\(\)/);
});

test('all charts compress non-trading dates with one shared rangebreak list',()=>{
  assert.match(source,/function tradingDayRangebreaks\(traces\)/);
  assert.match(source,/bounds:\['sat','mon'\]/);
  assert.match(source,/axis\.rangebreaks=rangebreaks/);
  assert.match(source,/if\(trace\.type==='scattergl'\)trace\.type='scatter'/);
  assert.match(source,/Plotly\.react=function\(target,traces,layout/);
});

test('detail and indicator price charts share the enlarged base height',()=>{
  assert.match(source,/if\(id==='detail-plot'\)layout\.height=700/);
  assert.match(source,/layout\.height=700\+Math\.max\(0,panels-1\)\*210/);
});

test('hosted strategy refresh preserves locally imported definitions',()=>{
  assert.match(source,/importedDefinitions\|\|\[\]/);
  assert.match(source,/definitions=\[\.\.\.loaded\.filter\(definition=>!importedIds\.has/);
  assert.match(source,/indicatorCandles:timeframe\?\[timeframe\]:\[\]/);
});

test('rotation candidates carry their last price across a different market holiday',()=>{
  const recordsFor=extractedRecordsFor(),data={
    QQQ:[
      {Date:'2024-01-02',Close:100},
      {Date:'2024-01-03',Close:101},
      {Date:'2024-01-04',Close:102},
    ],
    '069500.KS':[
      {Date:'2024-01-02',Close:300},
      {Date:'2024-01-04',Close:303},
    ],
  };
  const rows=recordsFor(['QQQ','069500.KS'],data,new Set(['069500.KS']));
  assert.equal(rows[1].market['069500.KS'].close,300);
  assert.equal(rows[2].market['069500.KS'].close,303);
});

test('a rotation candidate is absent before its first observation',()=>{
  const recordsFor=extractedRecordsFor(),data={
    QQQ:[{Date:'2024-01-02',Close:100},{Date:'2024-01-03',Close:101}],
    VWO:[{Date:'2024-01-03',Close:40}],
  };
  const rows=recordsFor(['QQQ','VWO'],data,new Set(['VWO']));
  assert.equal(rows[0].market.VWO,undefined);
  assert.equal(rows[1].market.VWO.close,40);
});

test('MDD is calculated from one running peak and rejects invalid history',()=>{
  const metrics=extractedMetrics(),history=[
    {date:'2024-01-02',value:100},
    {date:'2024-01-03',value:120},
    {date:'2024-01-04',value:90},
    {date:'2024-01-05',value:108},
  ];
  assert.equal(metrics(history).mdd,-.25);
  assert.throws(()=>metrics([...history,{date:'2024-01-08',value:NaN}]),/성과 계산 값/);
});

test('rotation deviation is checked again against the final rotated target',()=>{
  const install=extractedFinalTargetGuard();
  class Strategy {
    constructor(finalDeviation){
      this.finalDeviation=finalDeviation;
      this.def={rotation:{sleeve:'BIL'},rebalance:[{when:'target_deviation() >= 7.5%'}]};
    }
    context(){return {finalDeviation:this.finalDeviation};}
    step(){return {target:{BIL:0,SHY:.8,TDF2050_PROXY:.2},rebalance:true,reason:null};}
  }
  install(Strategy);
  assert.equal(new Strategy(false).step('2022-04-28',{},{}).rebalance,false);
  assert.equal(new Strategy(true).step('2022-06-02',{},{}).rebalance,true);
});

test('omitted rotation candidates receive liquidation targets internally',()=>{
  const install=extractedRotationInstaller();
  class Strategy {
    constructor(){
      this.def={rotation:{sleeve:'BIL',candidates:[{ticker:'GLD'},{ticker:'VWO'}]}};
      this.rotationActive=true;
      this.rotationMix={BIL:.5,GLD:.5};
      this.rotationSelected=['GLD'];
    }
    step(){return {target:{QQQ:.7,TDF2050_PROXY:.3,BIL:0},rebalance:false,reason:null};}
  }
  install(Strategy);
  const signal=new Strategy().step('2024-01-02',{},{});
  assert.equal(signal.target.GLD,0);
  assert.equal(signal.target.VWO,0);
  assert.equal(signal.rebalance,true);
});

test('rotation suspension restores the full sleeve to BIL',()=>{
  const install=extractedRotationSuspensionInstaller();
  class Strategy {
    constructor(){
      this.def={rotation:{sleeve:'BIL',suspend_when:true,candidates:[{ticker:'GLD'},{ticker:'VEA'}]}};
      this.rotationMix={BIL:.4,GLD:.3,VEA:.3};
      this.rotationSelected=['GLD','VEA'];
      this.rotationActive=true;
    }
    context(){return {};}
    step(){return {target:{QQQ:0,TDF:0,BIL:this.rotationMix.BIL||0,GLD:this.rotationMix.GLD||0,VEA:this.rotationMix.VEA||0},rebalance:false,reason:null};}
  }
  install(Strategy);
  const strategy=new Strategy(),signal=strategy.step('2024-01-02',{},{});
  assert.equal(signal.target.BIL,1);
  assert.equal(signal.target.GLD,0);
  assert.equal(signal.target.VEA,0);
  assert.equal(strategy.rotationDecision.suspended,true);
  assert.equal(signal.rebalance,true);
  assert.equal(strategy.step('2024-01-03',{},{}).rebalance,false);
});

test('date picker supports the Windows-style day, month, and year navigation views',()=>{
  assert.match(source,/const win11Months=/);
  assert.match(source,/view==='day'/);
  assert.match(source,/data-view="month"/);
  assert.match(source,/data-view="year"/);
  assert.match(source,/data-month=/);
  assert.match(source,/data-year=/);
  assert.match(source,/input\.dispatchEvent\(new Event\('change',\{bubbles:true\}\)\)/);
});

test('comparison charts bind visible-period y-axis autoscaling',()=>{
  assert.match(source,/bindVisibleYAutoscale\('performance-plot'\)/);
  assert.match(source,/bindVisibleYAutoscale\('drawdown-plot'\)/);
});

test('visible x-axis zoom derives a new y-axis range from only visible points',()=>{
  let handler,updates;
  const plot={
    dataset:{},
    data:[{x:['2024-01-01','2024-01-02','2024-01-03'],y:[1,100,3]}],
    _fullLayout:{xaxis:{range:['2024-01-01','2024-01-03']}},
    on:(event,listener)=>{handler=listener;},
  };
  const bind=extractedVisibleYAutoscale()(id=>id==='performance-plot'?plot:null,{relayout:(_,next)=>{updates=next;}});
  bind('performance-plot');
  plot._fullLayout.xaxis.range=['2024-01-01','2024-01-01T12:00:00Z'];
  handler({'xaxis.range[0]':'2024-01-01'});
  assert.ok(updates['yaxis.range'][1]<10);
});

test('detail and indicator charts retain zoom until their selected period changes',()=>{
  assert.match(source,/layout\.uirevision=`detail:\$\{\$\('start-date'\)\.value\}:\$\{\$\('end-date'\)\.value\}`/);
  assert.match(source,/indicatorZoomPeriod===period/);
  assert.match(source,/\{'xaxis\.range':previousRange\}/);
});

test('detail chart keeps the original stacked asset order',()=>{
  assert.match(source,/for\(const item of items\)traces\.push\(\{x:history\.map/);
  assert.doesNotMatch(source,/legendrank:index,stackgroup:'portfolio-return'/);
});

test('dashboard defaults foreign strategies to KRW and detail can switch to USD',()=>{
  assert.match(source,/function normalizeAutomaticValuation\(def\)\{\s*if\(!def\|\|def\.valuation\|\|def\.source\)return def;/);
  assert.match(source,/valuation:\{currency:'KRW',fx_ticker:'KRW=X',foreign_assets:foreign,signal_currency:'LOCAL'\}/);
  assert.match(source,/function usdValuationDefinition\(def\)\{return \{\.\.\.def,valuation:\{currency:'USD',foreign_assets:\[\],signal_currency:'USD'\}\};\}/);
  assert.match(source,/calculation\.valuation\?\.currency==='USD'\?true:calculation\.valuation\?false/);
  assert.match(source,/bundle\.precomputed_results_currency==='KRW'\?await loadPrecomputedResults\(\):\{\}/);
  assert.match(source,/detailUsdToggle\.nextElementSibling\.textContent='달러 기준으로 보기'/);
});

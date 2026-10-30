import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {addIndicators, parseYaml, strategySnapshot, runStrategyIncremental} from '../src/strategy-runtime.js';

const load = name => parseYaml(readFileSync(new URL(`../../strategies/${name}`, import.meta.url), 'utf8'));
const personal = load('33_qqq_guarded_reentry_buy_acceleration.yaml');
const retirement = load('34_band_7030_tdf_credit_guard_reentry.yaml');
const mapped = load('34P_band_7030_tdf_credit_guard_reentry_kodex_koact.yaml');

function data() {
  const dates = [];
  for (const d = new Date('2020-01-01T00:00:00Z'); dates.length < 330; d.setUTCDate(d.getUTCDate()+1))
    if (![0,6].includes(d.getUTCDay())) dates.push(d.toISOString().slice(0,10));
  return Object.fromEntries(['QQQ','SPY','XLF','BAA10Y','BIL','TDF2050_PROXY','KRW=X','379810.KS','0015B0.KS','434060.KS','488770.KS'].map(ticker => {
    const rows = dates.map((Date, i) => {
      const close = ticker === 'BAA10Y' ? 1.5 : ['QQQ','SPY','XLF','379810.KS','0015B0.KS'].includes(ticker) ? 100+i*.01 : 100;
      return {Date, Open:close, High:close, Low:close, Close:close, Volume:1};
    });
    addIndicators(rows);
    if (ticker === 'QQQ') rows.forEach((row, i) => row.VALUATION_SCORE = i < 270 ? 80 : 50);
    return [ticker, rows];
  }));
}

test('33 runs from raw prices and incremental evaluation matches a full replay', () => {
  const prices = data();
  const full = strategySnapshot([personal], personal, prices);
  const seed = strategySnapshot([personal], personal, Object.fromEntries(Object.entries(prices).map(([k,v]) => [k,v.slice(0,300)])));
  const incremental = runStrategyIncremental([personal], personal, prices, seed).snapshot;
  assert.deepEqual(incremental.runtime.state, full.runtime.state);
  assert.deepEqual(incremental.notification_context.target_weights, full.notification_context.target_weights);
  assert.equal(full.notification_context.state_values.guarded_reentry, 'TRUE');
  assert.equal(full.notification_context.target_weights.QQQ, .85);
});

test('34P maps limited reentry to 30/30 equity, 30 TDF and 10 cash', () => {
  const snapshot = strategySnapshot([retirement,mapped], mapped, data());
  const target = snapshot.notification_context.target_weights;
  for (const [ticker, weight] of Object.entries({'379810.KS':.3,'0015B0.KS':.3,'434060.KS':.3,'488770.KS':.1}))
    assert.ok(Math.abs(target[ticker]-weight) < 1e-10, ticker);
  assert.equal(snapshot.notification_context.state_values.guarded_reentry, 'TRUE');
  assert.ok(!('recovery_turn_days' in retirement.state));
});

test('31 and 31P retain the original allocation and separate source identity', () => {
  const original = load('31_band_7030_tdf_valuation_credit_guard_no_topup.yaml');
  const originalMapped = load('31P_band_7030_tdf_valuation_credit_guard_no_topup_kodex_koact.yaml');
  const snapshot = strategySnapshot([original,originalMapped], originalMapped, data());
  const target = snapshot.notification_context.target_weights;
  for (const [ticker, weight] of Object.entries({'379810.KS':.25,'0015B0.KS':.25,'434060.KS':.3,'488770.KS':.2}))
    assert.ok(Math.abs(target[ticker]-weight) < 1e-10, ticker);
  assert.ok(!('guarded_reentry' in original.state));
  assert.equal(originalMapped.source, original.strategy.id);
  assert.equal(mapped.source, retirement.strategy.id);
  assert.notEqual(mapped.source, originalMapped.source);
});

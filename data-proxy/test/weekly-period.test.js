import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {addIndicators} from '../src/strategy-runtime.js';

test('annual momentum uses exactly 252 completed observations and no future price', () => {
  const rows = Array.from({length: 260}, (_, i) => ({Open: 100+i, High: 100+i, Low: 100+i, Close: 100+i}));
  const full = addIndicators(structuredClone(rows));
  const prefix = addIndicators(structuredClone(rows.slice(0, 253)));
  assert.ok(Number.isNaN(full[251].ROC252));
  assert.equal(full[252].ROC252, (352/100-1)*100);
  assert.equal(full[252].ROC252, prefix[252].ROC252);
});

for (const path of ['../src/strategy-runtime.js', '../../src/web/app.js']) {
  test(`${path}: weekly state checks use Monday boundaries across years and holidays`, () => {
    const source = readFileSync(new URL(path, import.meta.url), 'utf8');
    const fn = source.match(/^function period\(.*$/m)[0];
    const period = vm.runInNewContext(`(${fn})`);
    assert.equal(period('2020-12-31', 'weekly'), period('2021-01-01', 'weekly'));
    assert.notEqual(period('2021-01-01', 'weekly'), period('2021-01-04', 'weekly'));
    assert.equal(period('2021-01-04', 'weekly'), period('2021-01-08', 'weekly'));
    // Monday holiday: Tuesday is the first evaluated date of the next week.
    assert.notEqual(period('2021-01-15', 'weekly'), period('2021-01-19', 'weekly'));
    assert.equal(period('2021-01-19', 'weekly'), period('2021-01-22', 'weekly'));
    assert.equal(period('2021-01-19', 'daily'), '2021-01-19');
    assert.equal(period('2021-01-19', 'monthly'), '2021-01');
    assert.equal(period('2021-01-19', 'quarterly'), '2021Q1');
  });
}

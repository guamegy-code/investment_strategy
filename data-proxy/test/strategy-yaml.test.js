import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";

import {parseYaml} from "../src/strategy-runtime.js";

test("Worker parses every strategy advertised by the deployed manifest", () => {
  const manifest = JSON.parse(readFileSync(new URL("../../dist/web/strategies/manifest.json", import.meta.url), "utf8"));
  for (const entry of manifest.strategies) {
    const yaml = readFileSync(new URL(`../../dist/web/strategies/${entry.path}`, import.meta.url), "utf8");
    assert.equal(parseYaml(yaml).strategy.id, entry.id, entry.path);
  }
});

test("Worker folds YAML block scalars with a chomp indicator", () => {
  const definition = parseYaml("rules:\n  - when: >-\n      state.stage == 3\n      and QQQ.close > 0\n    set: 2\n");
  assert.equal(definition.rules[0].when, "state.stage == 3 and QQQ.close > 0");
  assert.equal(definition.rules[0].set, 2);
});

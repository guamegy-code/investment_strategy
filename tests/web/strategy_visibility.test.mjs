import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source=readFileSync(new URL('../../src/web/app.js',import.meta.url),'utf8');
const loader=source.slice(source.indexOf('async function loadHostedStrategies(){'),source.indexOf("const DEFAULT_WEB_START_DATE="));

async function load({useBundle=true}={}){
  const hiddenSource={strategy:{id:'hidden-source',enabled:true,version:1}};
  const visible={strategy:{id:'visible',enabled:true},source:'hidden-source'};
  const manifest={hidden_strategy_ids:['hidden-source','hidden-import'],
    strategies:[{id:'visible',path:'visible.yaml',enabled:true},{id:'hidden-source',path:'hidden.yaml',enabled:true}],
    ...(useBundle?{enabled_bundle:'enabled.json'}:{})};
  const replies=new Map([['/manifest.json',manifest],['/enabled.json',[visible,hiddenSource]],['/visible.yaml',visible],['/hidden.yaml',hiddenSource]]);
  const context=vm.createContext({URL,Set,Array,String,Promise,
    bundle:{strategy_manifest_url:'https://fixture.test/manifest.json'},window:{location:{href:'https://fixture.test/'}},
    loadUiState:()=>({importedDefinitions:[{strategy:{id:'hidden-source',version:999}},{strategy:{id:'hidden-import'}},{strategy:{id:'custom'}}]}),
    migrateStrategyDefinition:value=>value,parseYaml:JSON.parse,definitions:[],
    fetch:async url=>({ok:true,json:async()=>structuredClone(replies.get(new URL(url).pathname)),text:async()=>JSON.stringify(replies.get(new URL(url).pathname))}),
  });
  await vm.runInContext(loader+'\nloadHostedStrategies();',context);
  return JSON.parse(JSON.stringify(context.definitions));
}

for(const useBundle of [true,false])test(`hidden definitions remain usable as sources and cannot reappear from saved imports (${useBundle?'bundle':'YAML'})`,async()=>{
  const definitions=await load({useBundle});
  assert.equal(definitions.find(d=>d.strategy.id==='visible').source,'hidden-source');
  assert.equal(definitions.find(d=>d.strategy.id==='hidden-source').strategy.enabled,false);
  assert.equal(definitions.find(d=>d.strategy.id==='hidden-source').strategy.version,1);
  assert.equal(definitions.some(d=>d.strategy.id==='hidden-import'),false);
  assert.equal(definitions.some(d=>d.strategy.id==='custom'),true);
});

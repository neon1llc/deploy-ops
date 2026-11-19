// tests/artifacts_ui.cjs - Qualify retained drafts and exact native collection requests without claiming a browser.
"use strict";
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let document;

// Model only the native nodes, validation and events used by the shipped module
class Node {
  constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this.listeners={}; this.value=''; this.isConnected=true; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(key,value) { this[key]=value; }
  addEventListener(name,work) { this.listeners[name]=work; }
  setCustomValidity(value) { this.validationMessage=value; }
  reportValidity() { return !this.validationMessage && (!this.required || !!this.value) && this.children.every(node=>node.reportValidity()); }
  querySelectorAll(selector) { return this.children.flatMap(node=>[node,...node.querySelectorAll(selector)]).filter(node=>selector==='[data-key]' ? !!node.dataset.key : selector==='button' ? node.tagName==='button' : true); }
  focus() { document.activeElement=this; }
  set innerHTML(value) { throw new Error('Unsafe artifact markup'); }
}
const root = new Node('div');
document={body:new Node('body'),createElement:tag=>new Node(tag)};document.activeElement=document.body;
const release='a'.repeat(32),component='web';const calls=[];
const report={format:'deploy-ops.artifact-observation',version:1,release_id:release,component,
  subject_sha256:'b'.repeat(64),subject_file_count:2,subject_bytes:100,captured_at:1800000000,inventory:{status:'collected',package_count:1,reason:'Inventory only'},
  vulnerabilities:{status:'not_requested',package_count:null,database_freshness:'not_applicable',severity_counts:{},reason:'Not selected'},
  authenticity:'not_checked',trusted_signer:'not_checked',policy:'not_evaluated'};
const result={verified:true,directory:'/private/evidence',report,meaning:'Observation retained; no release policy evaluated.'};
const context=vm.createContext({document,Map,Set,Date,Number,Object,token:'fixture',selected:release,detailKey:'',
  text:(parent,tag,value)=>{const node=new Node(tag);node.textContent=value;parent.append(node);return node;},
  reviewFacts:(parent,rows)=>{parent.facts=rows;},reviewEvidence:()=>{},element:id=>id==='detail'?root:null,
  api:async(...args)=>{calls.push(args);return result;}});
vm.runInContext(fs.readFileSync('src/deploy_ops/ui/artifacts.js','utf8'),context);
context.show=async()=>render();
function render(){root.children=[];context.artifactControls(root,release,component,new Set());return root.children[0];}
function nodes(tag){return root.querySelectorAll('*').filter(node=>node.tagName===tag);}
function input(name){return nodes('input').find(node=>node.name===name);}
function button(label){return nodes('button').find(node=>node.textContent===label);}
function form(){return nodes('form')[0];}
const event={preventDefault(){}};

// Delayed suggestions, repeated submits, refresh and failure cannot erase authored choices or a good report
(async()=>{
  render();let releaseOptions;
  context.api=()=>new Promise(resolve=>{releaseOptions=resolve;});
  const pendingOptions=button('Suggest a new private directory').onclick();
  input('directory').value='/private/owner-choice';input('directory').listeners.input();
  releaseOptions({output_directory:'/private/suggestion'});await pendingOptions;
  assert.equal(input('directory').value,'/private/owner-choice');
  render();assert.equal(input('directory').value,'/private/owner-choice');
  input('network').checked=true;input('network').listeners.input();
  context.api=async(...args)=>{calls.push(args);return result;};
  await form().onsubmit(event);assert.equal(calls.length,0);
  input('vulnerabilities').checked=true;input('vulnerabilities').listeners.input();
  let finish;
  context.api=(...args)=>{calls.push(args);return new Promise(resolve=>{finish=resolve;});};
  const first=form().onsubmit(event);assert(nodes('button').every(node=>node.disabled));
  await form().onsubmit(event);assert.equal(calls.length,1);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),['/releases/'+release+'/artifacts/collect','POST',{
    component,output_directory:'/private/owner-choice',inventory:true,vulnerabilities:true,allow_network:true,timeout_seconds:60}]);
  input('directory').value='/private/next-choice';input('directory').listeners.input();
  render();assert.equal(input('directory').value,'/private/next-choice');assert(nodes('button').every(node=>node.disabled));
  finish(result);await first;
  assert.equal(input('directory').value,'/private/next-choice');assert(nodes('button').every(node=>!node.disabled));
  assert(root.children[0].facts.some(row=>row[0]==='Release policy'&&row[1]==='Not evaluated'));
  const previous=root.children[0].facts;
  context.api=async()=>{throw new Error('refused');};await form().onsubmit(event);
  assert.equal(input('directory').value,'/private/next-choice');assert.deepEqual(root.children[0].facts,previous);
  calls.length=0;
  context.api=async(...args)=>{calls.push(args);return {...result,report:{...report,release_id:'c'.repeat(32)}};};
  await button('Inspect evidence directory').onclick();
  assert.equal(calls[0][0],'/releases/'+release+'/artifacts/inspect');
  render();assert.deepEqual(root.children[0].facts,previous);
  console.log('Artifact DOM contract passed: exact subject, profile, late paths, duplicates, refresh and retained good results; browser/native validation/AT separate');
})().catch(error=>{console.error(error);process.exitCode=1;});

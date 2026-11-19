// tests/signature_ui.cjs - Execute selected-key draft, request and late-response boundaries without claiming a browser.
"use strict";
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
let document;
class Node {
  constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this.listeners={}; this.value=""; }
  append(...nodes) { this.children.push(...nodes); }
  contains(node) { return this===node || this.children.some(child=>child.contains(node)); }
  setAttribute(key,value) { this[key]=value; }
  addEventListener(name,work) { this.listeners[name]=work; }
  reportValidity() { return (!this.required || !!this.value) && this.children.every(node=>node.reportValidity()); }
  querySelectorAll(selector) { return this.children.flatMap(node=>[node,...node.querySelectorAll("*")]).filter(node=>selector==="*" || selector==="[data-key]" && node.dataset.key || node.tagName===selector); }
  focus() { document.activeElement=this; }
  set innerHTML(value) { throw new Error("Unsafe signature markup"); }
}
const root=new Node("div");document={body:new Node("body"),createElement:tag=>new Node(tag)};document.activeElement=document.body;
const release="a".repeat(32), component="web", prepared="b".repeat(64), calls=[];
const report={format:"deploy-ops.file-signature-observation",version:1,release_id:release,component,prepared_sha256:prepared,
  selected_file:"release.tar",selected_file_sha256:"c".repeat(64),selected_key_sha256:"e".repeat(64),tool_version:"v3.1.4",
  profile:{timeout_seconds:60,expected_key_sha256:"e".repeat(64)},captured_at:1800000000,status:"verified",reason:"Selected file/key only"};
const result={integrity_verified:true,report};
const context=vm.createContext({document,Map,Set,Date,Number,Object,encodeURIComponent,token:"fixture",selected:release,detailKey:"",
  text:(parent,tag,value)=>{const node=new Node(tag);node.textContent=value;parent.append(node);return node;},
  reviewFacts:(parent,rows)=>{parent.facts=rows;},reviewEvidence:()=>{},element:id=>id==="detail"?root:null,
  api:async(...args)=>{calls.push(args);return result;}});
vm.runInContext(fs.readFileSync("src/deploy_ops/ui/signatures.js","utf8"),context);
context.show=async()=>render();
function render(){if(root.contains(document.activeElement))document.activeElement=document.body;root.children=[];context.signatureControls(root,release,component,prepared,new Set());}
function nodes(tag){return root.querySelectorAll(tag);}
function input(name){return nodes("input").find(node=>node.name===name);}
function set(name,value){const node=input(name);node.value=value;node.listeners.input();}
function button(label){return nodes("button").find(node=>node.textContent===label);}
function form(){return nodes("form").find(node=>node.method==="post");}
const event={preventDefault(){}};
(async()=>{
  render();set("key","/private/first.pub");let resolveKey;
  context.api=()=>new Promise(resolve=>{resolveKey=resolve;});
  const readingKey=button("Read selected key fingerprint").onclick();
  set("key","/private/newer.pub");set("pin","e".repeat(64));set("bundle","/private/newer.bundle.json");render();
  resolveKey({sha256:"d".repeat(64)});await readingKey;
  assert.equal(input("key").value,"/private/newer.pub");assert.equal(input("pin").value,"e".repeat(64));
  assert.equal(input("bundle").value,"/private/newer.bundle.json");
  let resolvePath;context.api=()=>new Promise(resolve=>{resolvePath=resolve;});
  const suggesting=button("Suggest a new private directory").onclick();
  set("directory","/private/owner-choice");set("file","release.tar");render();
  resolvePath({output_directory:"/private/suggested"});await suggesting;
  assert.equal(input("directory").value,"/private/owner-choice");assert.equal(input("file").value,"release.tar");
  let resolveSearch;context.api=()=>new Promise(resolve=>{resolveSearch=resolve;});
  let query=nodes("input").find(node=>node.type==="search");query.value="release";query.oninput();
  const find=button("Find frozen files");find.focus();
  const searching=nodes("form").find(node=>node.method==="get").onsubmit({...event,submitter:find});
  document.activeElement=document.body;
  query.value="index";query.oninput();render();
  resolveSearch({release_id:release,component,prepared_sha256:prepared,total:1,files:[{name:"release.tar",size:12}]});await searching;
  query=nodes("input").find(node=>node.type==="search");assert.equal(query.value,"index");
  assert(nodes("label").some(node=>node.textContent==="Matches for “release”"));
  assert.equal(document.activeElement,button("Find frozen files"));
  // A response cannot steal focus from another control while the same named query completes.
  let otherSearch;context.api=()=>new Promise(resolve=>{otherSearch=resolve;});
  const secondFind=button("Find frozen files");secondFind.focus();
  const pendingSearch=nodes("form").find(node=>node.method==="get").onsubmit({...event,submitter:secondFind});
  const other=new Node("button");other.focus();
  otherSearch({release_id:release,component,prepared_sha256:prepared,total:1,files:[{name:"release.tar",size:12}]});await pendingSearch;
  assert.equal(document.activeElement,other);
  const match=nodes("select")[0];match.value="release.tar";match.onchange();
  let complete;context.api=(...args)=>{calls.push(args);return new Promise(resolve=>{complete=resolve;});};
  const first=form().onsubmit(event);assert(form().querySelectorAll("button").every(node=>node.disabled));
  await form().onsubmit(event);assert.equal(calls.length,1);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),["/releases/"+release+"/signatures/verify","POST",{
    component,output_directory:"/private/owner-choice",selected_file:"release.tar",key_file:"/private/newer.pub",bundle_file:"/private/newer.bundle.json",
    expected_key_sha256:"e".repeat(64),timeout_seconds:60}]);
  set("file","other.tar");set("key","/private/next.pub");render();
  assert(form().querySelectorAll("button").every(node=>node.disabled));complete(result);await first;
  assert.equal(input("file").value,"other.tar");assert.equal(input("key").value,"/private/next.pub");
  assert(root.children[0].facts.some(row=>row[0]==="Selected frozen file"&&row[1]==="release.tar"));
  assert(root.children[0].facts.some(row=>row[0]==="Release policy"&&row[1]==="Not evaluated"));
  const previous=root.children[0].facts;
  context.api=async()=>{throw new Error("fixture refusal");};await form().onsubmit(event);
  assert.deepEqual(root.children[0].facts,previous);
  context.api=async()=>({...result,report:{...report,release_id:"f".repeat(32)}});
  await button("Inspect an existing signature report").onclick();assert.deepEqual(root.children[0].facts,previous);
  set("file","");set("key","");set("bundle","");calls.length=0;
  context.api=async(...args)=>{calls.push(args);return result;};
  await button("Inspect an existing signature report").onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),["/releases/"+release+"/signatures/inspect","POST",{component,output_directory:"/private/owner-choice"}]);
  console.log("Signature DOM contract passed: named file/key scope, stale inputs kept, exact requests, independent inspection, duplicates and retained good report; browser/AT separate");
})().catch(error=>{console.error(error);process.exitCode=1;});

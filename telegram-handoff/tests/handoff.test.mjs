import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { decodeHandoff, copyFullPrompt, nativeLink, mount } from '../dist/app.mjs';

const prompt = 'Use the skill selected below to create a draft about this news item.\n\nTitle: 模型 🤖 café & <news>\nDescription: Line 1\nLine 2\nSource: https://example.com/?a=1&b=2\n\nSkill: [CHOOSE AN INSTALLED SKILL]';

test('current and historical fragments preserve complete Unicode and line breaks', () => {
  for (const action of ['copy','codex','claude']) {
    assert.deepEqual(decodeHandoff('#'+new URLSearchParams({v:'1',action,prompt})),{prompt,action});
  }
});
test('missing and malformed links fail without inventing text', () => {
  for (const hash of ['', '#prompt=hi', '#v=2&action=copy&prompt=hi', '#v=1&action=execute&prompt=hi']) {
    assert.throws(()=>decodeHandoff(hash));
  }
});
test('very long prompts remain copyable without truncation', async () => {
  const long = '模型 🤖\n'.repeat(5000);
  assert.equal(decodeHandoff('#'+new URLSearchParams({v:'1',action:'copy',prompt:long})).prompt,long);
  let copied;
  await copyFullPrompt(long,{async writeText(text){copied=text;}});
  assert.equal(copied,long);
});

function setup({clipboard,action='copy',assign}={}) {
  const nodes = new Map();
  for (const name of ['prompt','copy','status','heading','codex','claude','app-limit']) {
    nodes.set(name,{value:'',textContent:'',disabled:true,handlers:{},focus(){this.focused=true;},select(){this.selected=true;},
      addEventListener(event,handler){this.handlers[event]=handler;}});
  }
  const requested = [];
  const browser = {location:{hash:'#'+new URLSearchParams({v:'1',action,prompt}),assign:assign??(url=>requested.push(url))},navigator:{clipboard}};
  mount({getElementById:id=>nodes.get(id)},browser);
  return {nodes,requested};
}
test('loading is inert and edited prompts are copied exactly', async () => {
  let copied;
  const {nodes,requested} = setup({clipboard:{async writeText(text){copied=text;}}});
  assert.deepEqual(requested,[]);
  const edited=prompt.replace('[CHOOSE AN INSTALLED SKILL]','my-installed-skill');
  nodes.get('prompt').value=edited;nodes.get('prompt').handlers.input();
  await nodes.get('copy').handlers.click();assert.equal(copied,edited);
  assert.match(nodes.get('status').textContent,/Full prompt copied/);
  assert.deepEqual(requested,[]);
});
test('historical app links remain inert until an explicit app click', async () => {
  for (const action of ['codex','claude']) {
    let copied;
    const {nodes,requested}=setup({action,clipboard:{async writeText(text){copied=text;}}});
    assert.equal(nodes.get('heading').textContent,'Copy your complete prompt.');
    await nodes.get('copy').handlers.click();assert.equal(copied,prompt);
    assert.deepEqual(requested,[]);
  }
});
test('copy success is not announced until the clipboard promise succeeds', async () => {
  let resolve;
  const {nodes}=setup({clipboard:{writeText(){return new Promise(r=>{resolve=r;});}}});
  const pending=nodes.get('copy').handlers.click();
  assert.equal(nodes.get('copy').disabled,true);
  assert.equal(nodes.get('status').textContent,'Copying…');
  resolve();await pending;assert.match(nodes.get('status').textContent,/Full prompt copied/);
});
test('denied or unavailable clipboard never claims success and selects complete text', async () => {
  for (const clipboard of [undefined,{async writeText(){throw new Error('denied');}}]) {
    const {nodes}=setup({clipboard});
    await nodes.get('copy').handlers.click();
    assert.match(nodes.get('status').textContent,/Copy failed/);
    assert.equal(nodes.get('prompt').value,prompt);
    assert.equal(nodes.get('prompt').selected,true);
    assert.equal(nodes.get('copy').disabled,false);
  }
});
test('public page exposes exactly the three approved actions and manual fallback', async () => {
  const html=await readFile(new URL('../dist/index.html',import.meta.url),'utf8');
  assert.equal((html.match(/<button\b/g)??[]).length,3);
  for (const label of ['Copy full prompt','Open with Codex','Open with Claude']) assert(html.includes(label));
  assert.match(html,/⌘C or Ctrl\+C/);
});
test('native links preserve full Unicode text and only prefill a new chat', () => {
  for (const app of ['codex','claude']) {
    const link=new URL(nativeLink(app,prompt));
    assert.equal(link.protocol,app+':');
    assert.equal(link.searchParams.size,1);
    assert.equal(link.searchParams.get(app==='codex'?'prompt':'q'),prompt);
    assert(!link.searchParams.has('submit'));
  }
  assert.throws(()=>nativeLink('unsupported',prompt));
});
test('both app buttons use the current edited field only on explicit click', () => {
  const {nodes,requested}=setup();
  assert.deepEqual(requested,[]);
  const edited=prompt.replace('[CHOOSE AN INSTALLED SKILL]','my-installed-skill')+'\nEdited: नमस्ते 🤖';
  nodes.get('prompt').value=edited;nodes.get('prompt').handlers.input();
  assert.deepEqual(requested,[]);
  for (const app of ['codex','claude']) nodes.get(app).handlers.click();
  assert.equal(requested.length,2);
  for (const url of requested) assert.equal([...new URL(url).searchParams.values()][0],edited);
  assert.match(nodes.get('status').textContent,/Opening requested/);
  assert(!/opened successfully/i.test(nodes.get('status').textContent));
});
test('empty or whitespace-only text disables app actions without launching', () => {
  for (const value of ['', ' \n\t']) {
    const {nodes,requested}=setup();
    nodes.get('prompt').value=value;nodes.get('prompt').handlers.input();
    for (const app of ['codex','claude']) {
      assert.equal(nodes.get(app).disabled,true);
      nodes.get(app).handlers.click();
      assert.throws(()=>nativeLink(app,value));
    }
    assert.deepEqual(requested,[]);
  }
});
test('Claude link limit never truncates and complete copying stays available', async () => {
  assert.equal(new URL(nativeLink('claude','a'.repeat(14000))).searchParams.get('q').length,14000);
  for (const long of ['a'.repeat(14001),'🤖'.repeat(7001)]) {
    let copied;
    const {nodes,requested}=setup({clipboard:{async writeText(text){copied=text;}}});
    nodes.get('prompt').value=long;nodes.get('prompt').handlers.input();
    assert.equal(nodes.get('claude').disabled,true);
    assert.equal(nodes.get('app-limit').hidden,false);
    assert.match(nodes.get('app-limit').textContent,/will not be truncated/);
    assert.throws(()=>nativeLink('claude',long));
    assert.equal(new URL(nativeLink('codex',long)).searchParams.get('prompt'),long);
    nodes.get('claude').handlers.click();assert.deepEqual(requested,[]);
    await nodes.get('copy').handlers.click();assert.equal(copied,long);
    nodes.get('prompt').value=prompt;nodes.get('prompt').handlers.input();
    assert.equal(nodes.get('claude').disabled,false);
    assert.equal(nodes.get('app-limit').hidden,true);
  }
});
test('app launch failure keeps the full text and copying without claiming success', async () => {
  let copied;
  const {nodes}=setup({assign(){throw new Error('No app handler');},clipboard:{async writeText(text){copied=text;}}});
  for (const app of ['codex','claude']) {
    nodes.get(app).handlers.click();
    assert.match(nodes.get('status').textContent,/Could not request opening/);
    assert.equal(nodes.get('prompt').value,prompt);
  }
  await nodes.get('copy').handlers.click();assert.equal(copied,prompt);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeHandoff, nativeLink, copyFullPrompt, mount } from '../dist/app.mjs';

const prompt = 'Use the skill selected below.\n\nTitle: 模型 🤖 café & <news>\nDescription: Line 1\nLine 2\nSource: https://example.com/?a=1&b=2\n\nSkill: [CHOOSE AN INSTALLED SKILL]';
test('fragment and native URLs preserve complete Unicode and line breaks', () => {
  for (const action of ['copy','codex','claude']) {
    assert.deepEqual(decodeHandoff('#'+new URLSearchParams({v:'1',action,prompt})),{prompt,action});
  }
  for (const app of ['codex','claude']) {
    const url = new URL(nativeLink(app,prompt));
    assert.equal(url.searchParams.get(app === 'codex' ? 'prompt' : 'q'),prompt);
    assert.equal(url.searchParams.size,1);
    assert(!url.searchParams.has('submit'));
  }
});
test('missing and malformed handoffs fail without inventing text', () => {
  for (const hash of ['', '#prompt=hi', '#v=2&action=copy&prompt=hi', '#v=1&action=execute&prompt=hi']) {
    assert.throws(()=>decodeHandoff(hash));
  }
});
test('very long prompts remain copyable; Claude link never silently truncates', async () => {
  const long = '模型 🤖\n'.repeat(5000);
  assert.throws(()=>nativeLink('claude',long),/Copy the full prompt/);
  assert.throws(()=>nativeLink('claude','🤖'.repeat(7001)),/Copy the full prompt/);
  assert.equal(new URL(nativeLink('codex',long)).searchParams.get('prompt'),long);
  let copied;
  await copyFullPrompt(long,{async writeText(text){copied=text;}});
  assert.equal(copied,long);
  assert.throws(()=>nativeLink('unsupported',prompt));
});

function setup({clipboard,assign}={}) {
  const nodes = new Map();
  for (const name of ['prompt','copy','status','heading','codex','claude','app-limit']) {
    const attrs = new Map([['aria-disabled','true']]);
    nodes.set(name,{value:'',textContent:'',disabled:true,handlers:{},focus(){this.focused=true;},select(){this.selected=true;},
      addEventListener(event,handler){this.handlers[event]=handler;},removeAttribute(key){attrs.delete(key);},
      setAttribute(key,value){attrs.set(key,value);},getAttribute(key){return attrs.get(key);}});
  }
  const requested = [];
  const browser = {location:{hash:'#'+new URLSearchParams({v:'1',action:'codex',prompt}),assign:assign??(url=>requested.push(url))},navigator:{clipboard}};
  mount({getElementById:id=>nodes.get(id)},browser);
  return {nodes,requested};
}
test('loading is inert; edits are copied and prefilled exactly without submission', async () => {
  let copied;
  const {nodes,requested} = setup({clipboard:{async writeText(text){copied=text;}}});
  assert.deepEqual(requested,[]);
  const edited=prompt.replace('[CHOOSE AN INSTALLED SKILL]','my-installed-skill');
  nodes.get('prompt').value=edited;nodes.get('prompt').handlers.input();
  await nodes.get('copy').handlers.click();assert.equal(copied,edited);
  assert.match(nodes.get('status').textContent,/Full prompt copied/);
  for (const app of ['codex','claude']) nodes.get(app).handlers.click({preventDefault(){}});
  assert.equal(requested.length,2);
  for (const url of requested) assert.equal([...new URL(url).searchParams.values()][0],edited);
});
test('copy success is not announced until the clipboard promise succeeds', async () => {
  let resolve;
  const {nodes}=setup({clipboard:{writeText(){return new Promise(r=>{resolve=r;});}}});
  const pending=nodes.get('copy').handlers.click();
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
  }
});
test('missing app handler does not claim launch success or lose the fallback', () => {
  const {nodes}=setup({assign(){throw new Error('App not installed');}});
  nodes.get('codex').handlers.click({preventDefault(){}});
  assert.equal(nodes.get('status').textContent,'App not installed');
  assert.equal(nodes.get('copy').disabled,false);
  assert.equal(nodes.get('prompt').value,prompt);
});
test('long edited prompt shows an explicit Claude limit and retains full copying', async () => {
  let copied;
  const {nodes} = setup({clipboard:{async writeText(text){copied=text;}}});
  const long = '模型 🤖\n'.repeat(5000);
  nodes.get('prompt').value=long;nodes.get('prompt').handlers.input();
  assert.equal(nodes.get('claude').getAttribute('aria-disabled'),'true');
  assert.equal(nodes.get('app-limit').hidden,false);
  assert.match(nodes.get('app-limit').textContent,/will not be truncated/);
  await nodes.get('copy').handlers.click();assert.equal(copied,long);
});

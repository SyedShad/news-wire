import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { decodeHandoff, copyFullPrompt, mount } from '../dist/app.mjs';

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

function setup({clipboard,action='copy'}={}) {
  const nodes = new Map();
  for (const name of ['prompt','copy','status','heading']) {
    nodes.set(name,{value:'',textContent:'',disabled:true,handlers:{},focus(){this.focused=true;},select(){this.selected=true;},
      addEventListener(event,handler){this.handlers[event]=handler;}});
  }
  const requested = [];
  const browser = {location:{hash:'#'+new URLSearchParams({v:'1',action,prompt}),assign:url=>requested.push(url)},navigator:{clipboard}};
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
test('historical app links show only copying and never launch an app', async () => {
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
test('public page has only a copy action with manual fallback and no app launch code', async () => {
  const html=await readFile(new URL('../dist/index.html',import.meta.url),'utf8');
  const script=await readFile(new URL('../dist/app.mjs',import.meta.url),'utf8');
  assert.equal((html.match(/<button\b/g)??[]).length,1);
  assert(!/<a\b/.test(html));
  assert(!/codex:\/\/|claude:\/\/|location\.assign|nativeLink/.test(script));
  assert.match(html,/Copy full prompt/);
  assert.match(html,/⌘C or Ctrl\+C/);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {applicationUrl, requiresServer} = require('../web/bootstrap.js');
const origin = 'https://cyber102.94.41.87.102.sslip.io';
const source = name => fs.readFileSync(require.resolve(`../web/${name}`), 'utf8');

test('opening the hosted app retains only an allowed page and a valid serial', () => {
  for (const page of ['index', 'documents', 'checkpoint', 'materials', 'telemetry']) {
    const target = new URL(applicationUrl(`file:///Users/example/${page}.html?serial=Насос%2042&next=https://example.org#private`));
    assert.equal(target.origin, origin);
    assert.equal(target.pathname, `/${page}.html`);
    assert.equal(target.searchParams.get('serial'), 'Насос 42');
    assert.deepEqual([...target.searchParams.keys()], ['serial']);
    assert.equal(target.hash, '');
  }
  assert.equal(applicationUrl('file:///private/secret.html?redirect=https://example.org'), `${origin}/`);
  for (const serial of ['', ' ', 'a'.repeat(129), 'pump\0id', 'pump\nid']) {
    assert.equal(new URL(applicationUrl(`file:///tmp/index.html?serial=${encodeURIComponent(serial)}`)).search, '');
  }
});

test('the guard never blocks HTTP, localhost or the Android offline origin', () => {
  assert.equal(requiresServer('file:'), true);
  assert.equal(requiresServer('file:', true), false);
  for (const href of ['http://localhost:8768/telemetry.html', `${origin}/index.html`, 'https://appassets.androidplatform.net/offline/index.html']) {
    const window = {CyberOffline:{enabled:href.includes('appassets.')}};
    vm.runInNewContext(source('bootstrap.js'), {window, location:new URL(href), URL,
      document:{createElement(){throw new Error('Normal app DOM must stay untouched');}}});
    assert.equal(window.CyberWebBoot.blocked, false);
  }
});

test('every actual file page executes its scripts without API calls or touching discarded page controls', () => {
  for (const page of ['index', 'documents', 'checkpoint', 'materials', 'telemetry']) {
    const scripts = [...source(`${page}.html`).matchAll(/<script src="([^"]+)" defer><\/script>/g)].map(match => match[1].split('?')[0]);
    assert.ok(scripts.indexOf('bootstrap.js') < scripts.indexOf(`${page === 'index' ? 'app' : page}.js`));
    if (page === 'index') assert.ok(scripts.indexOf('offline.js') < scripts.indexOf('bootstrap.js'));
    let fetches = 0;
    const makeNode = tag => ({tagName:tag, children:[], append(...nodes){this.children.push(...nodes);}});
    const document = {
      createElement:makeNode,
      body:{classList:{add(){}}, replaceChildren(...nodes){this.children=nodes;}},
      getElementById(){throw new Error('Page script must stop before accessing discarded controls');},
    };
    const context = vm.createContext({window:{}, location:new URL(`file:///tmp/web/${page}.html?serial=P-102`),
      document, URL, Map, Set, Intl, fetch(){fetches++;throw new Error('No API exists on file:');}});
    for (const script of scripts) vm.runInContext(source(script), context, {filename:script});
    assert.equal(fetches, 0, page);
    assert.equal(context.window.CyberWebBoot.blocked, true);
    const screen = document.body.children[0];
    assert.equal(document.body.children.length, 1);
    assert.equal(screen.children[1].textContent, 'Это локальная копия');
    const link = screen.children.at(-1);
    assert.equal(link.textContent, 'Открыть приложение');
    assert.equal(link.href, `${origin}/${page}.html?serial=P-102`);
    assert.equal(context.location.protocol, 'file:', 'The guard must not redirect');
  }
});

async function documentsFixture(respond) {
  class Element extends EventTarget {
    constructor(){super();this.value='';this.textContent='';this.dataset={};this.children=[];}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=[...nodes];}
    get childElementCount(){return this.children.length;}
    get options(){return this.children;}
  }
  const ids = [...source('documents.html').matchAll(/id="([^"]+)"/g)].map(match => match[1]);
  const nodes = new Map(ids.map(id => [id, new Element()]));
  const window = new EventTarget(); window.CyberWebBoot = {blocked:false};
  const calls = [];
  vm.runInNewContext(source('documents.js'), {window,
    document:{getElementById:id=>nodes.get(id), createElement:()=>new Element(),
      createTextNode:text=>({nodeType:3,textContent:text})},
    Option:class extends Element {constructor(text,value){super();this.textContent=text;this.value=value;}},
    AbortController, setTimeout, clearTimeout,
    fetch:async url => {calls.push(url);return new Response(JSON.stringify(await respond(url)), {headers:{'Content-Type':'application/json'}});},
  });
  const flush = async () => {await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));};
  return {nodes, window, calls, flush, close(){window.dispatchEvent(new Event('pagehide'));}};
}
const passport = {equipment:[{id:'pump', kind:'pump', title:'Насос', fields:{}}]};
const library = {documents:[{id:'manual',title:'Руководство',filename:'manual.pdf',pages:[],pageCount:2,bytes:1024}]};

test('failed catalog keeps the count unknown, while independently loaded passports remain usable', async () => {
  const fixture = await documentsFixture(url => {
    if (url.endsWith('/documents')) throw new TypeError('Failed to fetch');
    return passport;
  });
  try {
    assert.equal(fixture.nodes.get('document-count').textContent, '—', 'Loading is not an empty catalog');
    await fixture.flush();
    assert.equal(fixture.nodes.get('document-count').textContent, 'Недоступно');
    assert.equal(fixture.nodes.get('document-results').textContent, '');
    assert.equal(fixture.nodes.get('document-search').disabled, true);
    fixture.nodes.get('document-search').dispatchEvent(new Event('input'));
    assert.equal(fixture.nodes.get('document-results').textContent, '');
    assert.equal(fixture.nodes.get('equipment-kind').disabled, false);
    assert.equal(fixture.nodes.get('equipment-grid').children.length, 1);
    assert.match(fixture.nodes.get('documents-status').textContent, /Библиотека временно недоступна/);
    assert.doesNotMatch(fixture.nodes.get('documents-status').textContent, /Failed to fetch/);
  } finally {fixture.close();}
});

test('only a successfully loaded empty catalog is rendered as zero documents', async () => {
  for (const [catalog, count] of [[{documents:[]}, '0 PDF'], [{}, 'Недоступно']]) {
    const fixture = await documentsFixture(url => url.endsWith('/documents') ? catalog : passport);
    try {
      await fixture.flush();
      assert.equal(fixture.nodes.get('document-count').textContent, count);
      assert.equal(fixture.nodes.get('document-search').disabled, count !== '0 PDF');
      assert.equal(fixture.nodes.get('document-results').textContent, count === '0 PDF' ? '0 документов' : '');
    } finally {fixture.close();}
  }
});

test('a restored failed page can recover the real catalog without stale zero counts', async () => {
  let available = false;
  const fixture = await documentsFixture(url => {
    if (!available) throw new TypeError('Failed to fetch');
    return url.endsWith('/documents') ? library : passport;
  });
  try {
    await fixture.flush();
    assert.equal(fixture.nodes.get('document-count').textContent, 'Недоступно');
    assert.equal(fixture.nodes.get('equipment-kind').disabled, true);
    available = true; fixture.window.dispatchEvent(new Event('pageshow')); await fixture.flush();
    assert.equal(fixture.nodes.get('document-count').textContent, '1 PDF');
    assert.equal(fixture.nodes.get('document-results').textContent, '1 документов');
    assert.equal(fixture.nodes.get('document-search').disabled, false);
    assert.equal(fixture.nodes.get('documents-status').textContent, '');
    assert.equal(fixture.nodes.get('document-list').children.length, 1);
    const calls = fixture.calls.length;
    fixture.window.dispatchEvent(new Event('pageshow')); await fixture.flush();
    assert.equal(fixture.calls.length, calls, 'A loaded page must not fetch again on pageshow');
  } finally {fixture.close();}
});

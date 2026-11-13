const test = require('node:test');
const assert = require('node:assert/strict');
const {chartWindow,zoomWindow,revealIndex,indexAtFraction,keyIndex,seriesSegments}=require('../web/telemetry.js');
const row=value=>({vibration:{rms_mm_s:value}});

test('all 500 rows keep their original index and measurements; missing records break segments',()=>{
  const rows=Array.from({length:500},(_,i)=>row(i/100));
  rows[122]=row(null);rows[123]=row(undefined);rows[300]=row('3.0');rows[301]=row(NaN);
  const before=structuredClone(rows);
  const parts=seriesSegments(rows,chartWindow(rows.length));
  assert.deepEqual(parts.map(part=>[part[0].index,part.at(-1).index]),[[0,121],[124,299],[302,499]]);
  assert.equal(parts.flat().length,496);
  assert.equal(parts[0][0].value,0,'Zero is an actual measurement');
  assert.equal(parts.at(-1).at(-1).value,4.99);
  assert.deepEqual(rows,before,'Exploration never changes the source records');
});

test('touch/hover mapping targets exact endpoints and zoomed record indices, never reindexed data',()=>{
  const full=chartWindow(500);
  assert.equal(indexAtFraction(-.1,full),0);
  assert.equal(indexAtFraction(0,full),0);
  assert.equal(indexAtFraction(1,full),499);
  assert.equal(indexAtFraction(1.2,full),499);
  const zoomed=chartWindow(500,200,20);
  assert.equal(indexAtFraction(0,zoomed),200);
  assert.equal(indexAtFraction(.5,zoomed),210);
  assert.equal(indexAtFraction(1,zoomed),219);
});

test('zoom remains bounded around the inspected point even near dataset edges',()=>{
  for(const anchor of [0,250,499]){
    let view=chartWindow(500);
    for(let i=0;i<8;i++){
      view=zoomWindow(view,.5,anchor,500);
      assert.ok(view.start<=anchor&&view.end>=anchor);
      assert.ok(view.start>=0&&view.end<500&&view.size>=20);
    }
    assert.equal(view.size,20);
    for(let i=0;i<8;i++)view=zoomWindow(view,2,anchor,500);
    assert.deepEqual(view,chartWindow(500));
  }
});

test('window slider and reveal preserve selected index; panning cannot overrun first or last row',()=>{
  assert.deepEqual(chartWindow(500,-20,50),{start:0,end:49,size:50});
  assert.deepEqual(chartWindow(500,499,50),{start:450,end:499,size:50});
  const view=chartWindow(500,100,50);
  assert.equal(revealIndex(view,120,500),view,'No unnecessary viewport movement for a visible record');
  const later=revealIndex(view,499,500);
  assert.deepEqual(later,{start:450,end:499,size:50});
  const first=revealIndex(later,0,500);
  assert.deepEqual(first,{start:0,end:49,size:50});
});

test('repeated keyboard navigation advances a preview without waiting for a network snapshot',()=>{
  const committed=100;let preview=committed;const view=chartWindow(500,90,20);
  for(let i=0;i<5;i++)preview=keyIndex('ArrowRight',preview,view,500);
  assert.equal(preview,105);assert.equal(committed,100);
  assert.equal(keyIndex('ArrowLeft',0,view,500),0);
  assert.equal(keyIndex('ArrowRight',499,view,500),499);
  assert.equal(keyIndex('Home',preview,view,500),0);
  assert.equal(keyIndex('End',preview,view,500),499);
  assert.equal(keyIndex('PageDown',preview,view,500),107);
});

test('empty, short and all-missing ranges stay honest; isolated readings survive zoom',()=>{
  assert.deepEqual(chartWindow(0),{start:0,end:-1,size:0});
  assert.equal(indexAtFraction(.5,chartWindow(0)),-1);
  assert.deepEqual(seriesSegments([],chartWindow(0)),[]);
  const rows=[row(null),row(0),row(null),row(2),row(null)];
  assert.deepEqual(zoomWindow(chartWindow(5),.5,2,5),chartWindow(5));
  assert.deepEqual(seriesSegments(rows,chartWindow(5)).map(part=>part.map(point=>point.index)),[[1],[3]]);
  assert.deepEqual(seriesSegments(rows,chartWindow(5,2,1)),[]);
});

// A small EventTarget fixture executes the actual handlers without a browser,
// network, layout engine, or DOM automation library.
async function interactionFixture() {
  const fs=require('node:fs'),vm=require('node:vm');
  const ids=[...fs.readFileSync(require.resolve('../web/telemetry.html'),'utf8').matchAll(/id="([^"]+)"/g)].map(match=>match[1]);
  const document=new EventTarget(),window=new EventTarget();document.hidden=false;
  class Element extends EventTarget {
    constructor(tag='div'){super();this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.textContent='';this.value='0';}
    setAttribute(name,value){this.attributes[name]=String(value);}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=[...nodes];}
    getBoundingClientRect(){return {left:10,width:640,height:240,top:0};}
    focus(){document.activeElement=this;this.dispatchEvent(new Event('focus'));}
  }
  const nodes=new Map(ids.map(id=>[id,new Element()]));nodes.get('replay-speed').value='2000';
  document.getElementById=id=>nodes.get(id)||null;
  document.createElement=tag=>new Element(tag);document.createElementNS=(_,tag)=>new Element(tag);
  const rows=Array.from({length:500},(_,i)=>({timestamp:`2026-11-13T${String(i%24).padStart(2,'0')}:00:00`,rpm:1500,
    vibration:{rms_mm_s:i/100,one_x_mm_s:.1,two_x_mm_s:.1,high_frequency_g:.1},temperature:{bearing_c:40,winding_c:50},
    assessments:{vibration:{rms_overall:{level:'below_threshold'}},temperature:{}},provenance:{sourceRow:i}}));
  const calls=[],held=new Map(),defer=new Set(),frames=new Map();let frameId=0;
  const snapshot=i=>({index:i,rowCount:500,source:'customer_replay',datasetId:'test-series',sourceTimestamp:rows[i].timestamp,
    emittedAt:'2026-11-13T00:00:00Z',row:rows[i]});
  const reply=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
  const fetcher=async(url,options={})=>{
    calls.push({url,signal:options.signal});
    if(url==='/api/telemetry/catalog')return reply({rowCount:500,dataset:{id:'test-series'}});
    if(url==='/api/customer/sensors')return reply({rows,dataset:{id:'test-series'},summary:{rowCount:500}});
    const i=Number(new URL(url,'http://fixture').searchParams.get('index'));
    if(defer.has(i))return new Promise(resolve=>held.set(i,()=>resolve(reply(snapshot(i)))));
    return reply(snapshot(i));
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../web/telemetry.js'),'utf8'),{
    document,window,fetch:fetcher,AbortController,Intl,setTimeout,clearTimeout,
    requestAnimationFrame:callback=>{frames.set(++frameId,callback);return frameId;},cancelAnimationFrame:id=>frames.delete(id),
    ResizeObserver:class{constructor(callback){this.callback=callback;}observe(){this.callback();}},
  });
  const flush=async()=>{await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));};
  await flush();
  function send(id,type,properties={}){const event=new Event(type,{cancelable:true});Object.assign(event,properties);nodes.get(id).dispatchEvent(event);return event;}
  return {nodes,calls,defer,held,send,flush,flushFrames(){for(const [id,callback]of [...frames]){frames.delete(id);callback();}},
    async close(){window.dispatchEvent(new Event('pagehide'));for(const resolve of held.values())resolve();await flush();}};
}

test('actual hover/focus handlers preview values without fetching; swipe does not select a record',async()=>{
  const fixture=await interactionFixture();
  try{
    const initial=fixture.calls.length;
    fixture.send('telemetry-trend-chart','pointermove',{pointerType:'mouse',buttons:0,clientX:330});fixture.flushFrames();
    assert.equal(fixture.calls.length,initial);
    assert.match(fixture.nodes.get('trend-record').textContent,/Просмотр/);
    assert.notEqual(fixture.nodes.get('trend-value').textContent,'0 мм/с');
    fixture.nodes.get('telemetry-trend-chart').focus();
    fixture.send('telemetry-trend-chart','keydown',{key:'Home'});
    for(let i=0;i<5;i++)fixture.send('telemetry-trend-chart','keydown',{key:'ArrowRight'});
    assert.equal(fixture.nodes.get('trend-record').textContent,'Просмотр · запись 6');
    assert.equal(fixture.calls.length,initial,'Focus and arrow navigation remain local previews');
    fixture.send('telemetry-trend-chart','pointerdown',{isPrimary:true,pointerType:'touch',pointerId:1,clientX:100,clientY:100});
    fixture.send('telemetry-trend-chart','pointerup',{pointerType:'touch',pointerId:1,clientX:100,clientY:132});
    assert.equal(fixture.calls.length,initial,'Vertical swipe must not select');
    fixture.send('telemetry-trend-chart','pointerdown',{isPrimary:true,pointerType:'touch',pointerId:2,clientX:330,clientY:100});
    fixture.send('telemetry-trend-chart','pointerup',{pointerType:'touch',pointerId:2,clientX:332,clientY:102});
    await fixture.flush();
    assert.equal(fixture.calls.length,initial+1,'A tap selects exactly one real snapshot');
    assert.match(fixture.nodes.get('trend-record').textContent,/Выбрана/);
  }finally{await fixture.close();}
});

test('Enter commits the preview and a late older snapshot cannot replace a newer selection',async()=>{
  const fixture=await interactionFixture();
  try{
    fixture.defer.add(5);fixture.nodes.get('telemetry-trend-chart').focus();
    for(let i=0;i<5;i++)fixture.send('telemetry-trend-chart','keydown',{key:'ArrowRight'});
    fixture.send('telemetry-trend-chart','keydown',{key:'Enter'});await fixture.flush();
    assert.match(fixture.nodes.get('trend-record').textContent,/Загрузка · запись 6/);
    fixture.send('telemetry-trend-chart','keydown',{key:'ArrowRight'});
    fixture.send('telemetry-trend-chart','keydown',{key:'Enter'});await fixture.flush();
    assert.equal(fixture.calls.find(call=>call.url.endsWith('index=5')).signal.aborted,true);
    assert.equal(fixture.nodes.get('replay-position').value,'6');
    assert.equal(fixture.nodes.get('trend-record').textContent,'Выбрана · запись 7');
    fixture.held.get(5)();await fixture.flush();
    assert.equal(fixture.nodes.get('replay-position').value,'6');
    assert.equal(fixture.nodes.get('trend-record').textContent,'Выбрана · запись 7');
  }finally{await fixture.close();}
});

test('a completed selection preserves a newer keyboard preview and its zoomed viewport',async()=>{
  const fixture=await interactionFixture();
  try{
    const key=key=>fixture.send('telemetry-trend-chart','keydown',{key});
    fixture.nodes.get('telemetry-trend-chart').focus();
    for(let i=0;i<3;i++)key('ArrowRight');
    key('Enter');await fixture.flush();
    fixture.send('trend-zoom-in','click');fixture.send('trend-zoom-in','click');
    fixture.nodes.get('trend-window').value='375';fixture.send('trend-window','input');
    fixture.defer.add(499);key('End');key('Enter');await fixture.flush();
    key('Home');
    assert.equal(fixture.nodes.get('trend-record').textContent,'Просмотр · запись 1');
    assert.equal(fixture.nodes.get('trend-range').textContent,'1–125 из 500');
    fixture.held.get(499)();await fixture.flush();
    assert.equal(fixture.nodes.get('replay-position').value,'499','The requested snapshot is allowed to finish');
    assert.equal(fixture.nodes.get('trend-record').textContent,'Просмотр · запись 1','A newer preview must survive that response');
    assert.equal(fixture.nodes.get('trend-range').textContent,'1–125 из 500','The response must not move the user’s viewport');
    key('Enter');await fixture.flush();
    assert.equal(fixture.nodes.get('replay-position').value,'0');
    assert.equal(fixture.nodes.get('trend-record').textContent,'Выбрана · запись 1');
    assert.equal(fixture.calls.at(-1).url,'/api/telemetry/snapshot?index=0');
  }finally{await fixture.close();}
});

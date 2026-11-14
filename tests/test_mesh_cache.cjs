const test = require('node:test');
const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const {create} = require('../web/mesh-cache.js');

function harness() {
  const values = new Map(), calls = [];
  let version = 'a'.repeat(64), missing = false;
  const storage = {getItem:key=>values.get(key), setItem:(key,value)=>values.set(key,value)};
  const request = async (path, options={}) => {
    calls.push({path, method:options.method || 'GET'});
    if (path === '/capabilities') return {json:async()=>({cacheVersion:version})};
    if (path && missing) throw new Error('Expired');
    return {json:async()=>({id:'b'.repeat(40),state:'ready'})};
  };
  return {storage,request,calls,setVersion:value=>version=value,setMissing:value=>missing=value,
    client:()=>create({request,storage,crypto:webcrypto})};
}
function file(value, name='same.mp4') {
  const data = Buffer.from(value);
  return {name,type:'video/mp4',arrayBuffer:async()=>data.buffer.slice(data.byteOffset,data.byteOffset+data.length)};
}
test('same video survives page reload without another upload, regardless of its filename', async()=>{
  const h=harness();
  await h.client().start(file('content'));
  await h.client().start(file('content','renamed.mov'));
  assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
  assert.ok(h.calls.some(row=>row.path==='/'+'b'.repeat(40)));
});
test('changed file or pipeline requires a new upload', async()=>{
  const h=harness(), client=h.client();
  await client.start(file('one'));
  await client.start(file('two'));
  h.setVersion('c'.repeat(64));
  await client.start(file('one'));
  assert.equal(h.calls.filter(row=>row.method==='POST').length,3);
});
test('missing server result falls back to upload instead of a broken cached viewer', async()=>{
  const h=harness(), client=h.client();
  await client.start(file('content'));
  h.setMissing(true);
  await client.start(file('content'));
  assert.equal(h.calls.filter(row=>row.method==='POST').length,2);
});
test('disabled browser storage still permits reconstruction', async()=>{
  const h=harness();
  const client=create({request:h.request,crypto:webcrypto,storage:{getItem(){throw new Error('Denied')},setItem(){throw new Error('Denied')}}});
  await client.start(file('content'));
  assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
});
test('cancellation during cache discovery never submits a new upload', async()=>{
  const controller=new AbortController();
  let uploads=0;
  const client=create({crypto:webcrypto,request:async(path,options)=>{
    if (options.method==='POST') uploads++;
    controller.abort(); throw new DOMException('Stopped','AbortError');
  }});
  await assert.rejects(client.start(file('content'),controller.signal),{name:'AbortError'});
  assert.equal(uploads,0);
});

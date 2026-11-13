const test = require('node:test');
const assert = require('node:assert/strict');
const {create, progressText, DEADLINE_MS} = require('../web/analysis-jobs.js');
const client = '11111111-1111-4111-8111-111111111111';
const request = '22222222-2222-4222-8222-222222222222';
const id = '33333333333343338333333333333333';
const other = '44444444-4444-4444-8444-444444444444';
const enabled = {vision:{queue:{enabled:true,pollIntervalSeconds:2}}};
const json = (data, status=200) => new Response(JSON.stringify(data), {status,headers:{'Content-Type':'application/json'}});
const job = (status, extra={}) => ({job:{id,status,position:status==='queued'?2:0,...extra}});
function setup(handler, options={}) {
  const calls=[], values=new Map(), ids=[client,request,other];
  const api=create({fetch:async(url,options={})=>{calls.push({url,options});return handler(url,options,calls);},
    uuid:()=>ids.shift(),storage:{getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)},delay:async()=>{},...options});
  return {api,calls,values};
}
function untilAbort(signal) {
  return new Promise((resolve,reject)=>{
    if(signal.aborted) reject(new DOMException('aborted','AbortError'));
    else signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true});
  });
}

test('legacy capability absence preserves original fetch response and request without queue headers', async()=>{
  const original=json({status:'clear'});
  const {api,calls}=setup(url=>url==='/api/capabilities'?json({vision:{enabled:true}}):original);
  const body=new FormData();body.append('profile','standard');
  const response=await api.request('/analyze',{method:'POST',body});
  assert.equal(response,original);assert.equal(response.analysisQueued,undefined);
  assert.equal(calls.length,2);assert.equal(calls[1].options.body,body);assert.equal(calls[1].options.headers,undefined);
});

test('one accepted job yields queue progress then original result with authenticated polling',async()=>{
  let polls=0;const updates=[],result={status:'defect',analysisId:'saved-id',imageUrl:'/api/analyses/saved/image',model:'selected'};
  const {api,calls,values}=setup((url,opts)=>{
    if(url==='/api/capabilities')return json(enabled);
    if(url==='/analyze')return json(job('queued'),202);
    polls++;return json(polls===1?job('running'):job('succeeded',{result}));
  });
  const response=await api.request('/analyze',{method:'POST',headers:{'X-Existing':'kept'},body:'frame'},value=>updates.push(value));
  assert.equal(response.ok,true);assert.equal(response.analysisQueued,true);assert.deepEqual(await response.json(),result);
  assert.deepEqual(updates,['В очереди · позиция 2','Анализируем…']);
  assert.equal(calls.filter(call=>call.url==='/analyze').length,1);
  const headers=calls[1].options.headers;
  assert.equal(headers.get('Prefer'),'respond-async');assert.equal(headers.get('X-Analysis-Client'),client);
  assert.equal(headers.get('X-Analysis-Request'),request);assert.equal(headers.get('X-Existing'),'kept');
  assert.equal(calls[2].url,`/api/analysis-jobs/${id}`);assert.equal(calls[2].options.headers['X-Analysis-Client'],client);
  assert.equal(values.get('cyber102.analysis-client.v1'),client);
});

test('failed job is returned once with original failure status and never submitted again',async()=>{
  const {api,calls}=setup(url=>url==='/api/capabilities'?json(enabled):json(job('failed',{error:'GPU busy',code:'vision_busy',errorStatus:503}),202));
  const response=await api.request('/analyze',{method:'POST'});
  assert.equal(response.status,503);assert.equal(response.analysisQueued,true);
  assert.deepEqual(await response.json(),{error:'GPU busy',code:'vision_busy'});assert.equal(calls.length,2);
});

test('abort before acceptance cancels by request token without the aborted signal',async()=>{
  const controller=new AbortController();let started;
  const ready=new Promise(resolve=>started=resolve);
  const {api,calls}=setup((url,opts)=>{
    if(url==='/api/capabilities')return json(enabled);
    if(url.endsWith('/cancel'))return json(job('cancelled'));
    started();return untilAbort(opts.signal);
  });
  const pending=api.request('/analyze',{method:'POST',signal:controller.signal});
  await ready;controller.abort();await assert.rejects(pending,{name:'AbortError'});
  const cancellation=calls.find(call=>call.url.endsWith('/cancel'));
  assert.equal(cancellation.url,`/api/analysis-jobs/by-request/${request}/cancel`);
  assert.equal(cancellation.options.keepalive,true);assert.equal(cancellation.options.signal,undefined);
  assert.equal(cancellation.options.headers['X-Analysis-Client'],client);
  assert.equal(calls.filter(call=>call.url.endsWith('/cancel')).length,1);
});

test('abort during polling cancels the same job and stops progress updates',async()=>{
  const controller=new AbortController();let polling;
  const ready=new Promise(resolve=>polling=resolve);const updates=[];
  const {api,calls}=setup((url,opts)=>{
    if(url==='/api/capabilities')return json(enabled);
    if(url==='/api/video-review')return json(job('queued'),202);
    if(url.endsWith('/cancel'))return json(job('cancelling'));
    polling();return untilAbort(opts.signal);
  });
  const pending=api.request('/api/video-review',{method:'POST',signal:controller.signal},value=>updates.push(value));
  await ready;controller.abort();await assert.rejects(pending,{name:'AbortError'});
  assert.deepEqual(updates,['В очереди · позиция 2']);assert.equal(calls.filter(call=>call.url==='/api/video-review').length,1);
  assert.equal(calls.filter(call=>call.url.endsWith('/cancel')).length,1);
});

test('malformed/mismatched job results are rejected and cancelled instead of shown',async()=>{
  const {api,calls}=setup(url=>{
    if(url==='/api/capabilities')return json(enabled);
    if(url==='/analyze')return json(job('queued'),202);
    if(url.endsWith('/cancel'))return json(job('cancelled'));
    return json({job:{id:other.replaceAll('-',''),status:'succeeded',result:{model:'wrong job'}}});
  });
  await assert.rejects(api.request('/analyze',{method:'POST'}),/некорректное состояние/);
  assert.equal(calls.filter(call=>call.url.endsWith('/cancel')).length,1);
});

test('queue full and expired responses do not silently retry with new request IDs',async()=>{
  for(const [payload,status,expected] of [[{error:'full',code:'analysis_queue_full'},503,503],[job('expired',{error:'expired',code:'analysis_expired',errorStatus:410}),202,410]]) {
    const {api,calls}=setup(url=>url==='/api/capabilities'?json(enabled):json(payload,status));
    const response=await api.request('/analyze',{method:'POST'});
    assert.equal(response.status,expected);assert.equal(response.analysisQueued,true);assert.equal(calls.length,2);
  }
});

test('transient GET failures retry only polling, never the upload',async()=>{
  let polls=0;
  const {api,calls}=setup(url=>{
    if(url==='/api/capabilities')return json(enabled);
    if(url==='/analyze')return json(job('running'),202);
    if(++polls<3)throw new TypeError('network interrupted');
    return json(job('succeeded',{result:{status:'clear'}}));
  });
  assert.equal((await api.request('/analyze',{method:'POST'})).ok,true);
  assert.equal(polls,3);assert.equal(calls.filter(call=>call.url==='/analyze').length,1);
});

test('unavailable discovery does not bypass the queue; absent old capability endpoint may fall back',async()=>{
  const unavailable=setup(()=>json({error:'unavailable'},503));
  await assert.rejects(unavailable.api.request('/analyze',{method:'POST'}),/доступность/);
  assert.equal(unavailable.calls.length,1);
  const old=setup(url=>url==='/api/capabilities'?json({},404):json({legacy:true}));
  assert.deepEqual(await (await old.api.request('/analyze',{method:'POST'})).json(),{legacy:true});
});

test('blocked storage retains one private in-memory client token across distinct jobs',async()=>{
  const {api,calls}=setup(url=>url==='/api/capabilities'?json(enabled):json(job('succeeded',{result:{status:'clear'}}),202),
    {storage:{getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}}});
  await api.request('/analyze',{method:'POST'});await api.request('/analyze',{method:'POST'});
  const posts=calls.filter(call=>call.url==='/analyze');
  assert.equal(posts[0].options.headers.get('X-Analysis-Client'),posts[1].options.headers.get('X-Analysis-Client'));
  assert.notEqual(posts[0].options.headers.get('X-Analysis-Request'),posts[1].options.headers.get('X-Analysis-Request'));
});

test('bounded deadline cancels the queued request and reports timeout rather than false success',async()=>{
  const {api,calls}=setup(url=>url==='/api/capabilities'?json(enabled):url.endsWith('/cancel')?json(job('cancelled')):json(job('queued'),202),
    {deadlineMs:10,delay:(ms,signal)=>untilAbort(signal)});
  await assert.rejects(api.request('/analyze',{method:'POST'}),/Время ожидания анализа истекло/);
  assert.equal(calls.filter(call=>call.url.endsWith('/cancel')).length,1);assert.equal(DEADLINE_MS,780000);
});

test('already aborted request never submits, and active cancellation text is explicit',async()=>{
  const {api,calls}=setup(()=>{throw new Error('must not fetch');});
  await assert.rejects(api.request('/analyze',{method:'POST',signal:AbortSignal.abort()}),{name:'AbortError'});
  assert.equal(calls.length,0);assert.equal(progressText({status:'cancelling'}),'Отменяем анализ…');
});

test('photo adapter preserves legacy busy retry but never requeues a failed async job',async()=>{
  const fs=require('node:fs'),vm=require('node:vm');
  const source=fs.readFileSync(require.resolve('../web/inspection.js'),'utf8');
  function adapter(helper,fetcher) {
    const window=helper?{CyberAnalysisJobs:helper}:{};
    vm.runInNewContext(source,{window,document:{getElementById:()=>null},fetch:fetcher,Intl,DOMException,setTimeout,clearTimeout});
    return window.CyberCamera;
  }
  let asyncCalls=0;
  const queued=json({error:'busy',code:'vision_busy'},503);
  Object.defineProperty(queued,'analysisQueued',{value:true});
  const asyncAdapter=adapter({request:async()=>{asyncCalls++;return queued;}},()=>{throw new Error('legacy fetch must not run');});
  assert.equal(await asyncAdapter.fetchAnalysis('/analyze',{},()=>{}),queued);
  assert.equal(asyncCalls,1);
  let legacyCalls=0;
  const legacyAdapter=adapter(null,async()=>++legacyCalls===1
    ? new Response(JSON.stringify({error:'busy',code:'vision_busy'}),{status:503,headers:{'Retry-After':'0.001'}})
    : json({status:'clear'}));
  assert.equal((await legacyAdapter.fetchAnalysis('/analyze',{},()=>{})).ok,true);
  assert.equal(legacyCalls,2);
});


test('progress announces changed queue positions and stages only, not every poll',async()=>{
  const updates=[],states=[job('queued',{position:3}),job('queued',{position:2}),job('queued',{position:2}),
    job('running'),job('running'),job('succeeded',{result:{status:'clear'}})];
  const {api,calls}=setup(url=>url==='/api/capabilities'?json(enabled):json(states.shift(),url==='/analyze'?202:200));
  const response=await api.request('/analyze',{method:'POST'},value=>updates.push(value));
  assert.equal(response.ok,true);
  assert.deepEqual(updates,['В очереди · позиция 3','В очереди · позиция 2','Анализируем…']);
  assert.equal(calls.filter(call=>call.url===`/api/analysis-jobs/${id}`).length,5,'Unchanged progress must still poll for completion');
});

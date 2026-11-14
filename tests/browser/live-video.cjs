const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium,webkit,devices}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=path.resolve(__dirname,'../..');
const access=JSON.parse(fs.readFileSync(process.env.CYBER102_ACCESS_FILE || root+'/var/deploy-access.json','utf8'));
const origin=new URL(access.url).origin;
const authorization='Basic '+Buffer.from(access.username+':'+access.password).toString('base64');
const out=process.env.VIDEO_SMOKE_SCREENSHOTS;
if(out)fs.mkdirSync(out,{recursive:true});
const reports=[];
(async()=>{
 for(const engine of ['chromium','webkit']){
  const browser=engine==='chromium'?await chromium.launch(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH}: {}):await webkit.launch();
  const context=await browser.newContext(engine==='webkit'?devices['iPhone 13']:{viewport:{width:1280,height:1000}});
  const page=await context.newPage(); const errors=[]; const records=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(origin+'/**',route=>route.continue({headers:{...route.request().headers(),authorization}}));
  page.on('response',async r=>{
   const pathname=new URL(r.url()).pathname;
   if(pathname==='/analyze'&&r.status()!==202){
    try{records.push({status:r.status(),body:await r.json()});}catch{}
   }else if(/^\/api\/analysis-jobs\/[0-9a-f]{32}$/.test(pathname)&&r.status()===200){
    try{const {job}=await r.json();
     if(job?.status==='succeeded') records.push({status:200,body:job.result,jobId:job.id});
     else if(['failed','expired'].includes(job?.status)) records.push({status:job.errorStatus||502,body:{error:job.error},jobId:job.id});
    }catch{}
   }
  });
  await page.goto(origin+'/index.html',{waitUntil:'networkidle'});
  const capsResponse=await page.request.get(origin+'/api/capabilities',{headers:{authorization}});
  assert.equal(capsResponse.ok(),true);
  const capabilities=(await capsResponse.json()).vision;
  assert.equal(capabilities.enabled,true);assert.equal(typeof capabilities.model,'string');
  assert.match(capabilities.revision,/^[a-f0-9]{40}$/);
  await page.locator('#example').evaluate(img=>img.decode());
  await page.evaluate(()=>{
   Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>{
    const canvas=document.createElement('canvas');canvas.width=960;canvas.height=640;
    const ctx=canvas.getContext('2d');const img=document.querySelector('#example');
    const paint=()=>ctx.drawImage(img,0,0,960,640);
    paint();window.fakeVideoTimer=setInterval(paint,100);
    window.fakeVideo=canvas.captureStream(10);return window.fakeVideo;
   }}});
  });
  await page.locator('#camera').click();
  await page.locator('#video').waitFor();
  await page.locator('#analysis-settings > summary').click();
  await page.locator('#camera-mode').selectOption('video');
  await page.getByLabel('Интервал').selectOption('2000');
  await page.getByRole('button',{name:'Начать проверку'}).click();
  const count=engine==='chromium'?3:1;
  await page.waitForFunction(n=>document.querySelector('#video-metrics').textContent.includes('Проверено кадров: '+n),count,{timeout:13*60*1000});
  await page.getByRole('button',{name:'Остановить проверку'}).click();
  await page.locator('#detection').waitFor();
  assert.equal(await page.locator('#video').isVisible(),true);
  assert.equal(await page.locator('#checked-viewfinder #snapshot').isVisible(),true);
  assert.equal(await page.locator('#viewfinder #detection').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  const successful=records.filter(record=>record.status===200);
  assert.ok(successful.length>=count);
  for(const record of successful){
   assert.equal(record.body.model,capabilities.model);
   if(record.body.revision!==undefined) assert.equal(record.body.revision,capabilities.revision);
  }
  await page.locator('.result-provenance > summary').click();
  assert.equal(await page.locator('#analysis-model').innerText(),successful.at(-1).body.model);
  await page.locator('.result-provenance > summary').click();
  const afterResponse=await page.request.get(origin+'/api/capabilities',{headers:{authorization}});
  assert.equal(afterResponse.ok(),true);
  const after=(await afterResponse.json()).vision;
  assert.equal(after.model,capabilities.model);assert.equal(after.revision,capabilities.revision);
  const metrics=await page.locator('#video-metrics').innerText();
  if(out)await page.screenshot({path:out+'/cyber102-internvl-video-'+engine+'.png',fullPage:true});
  assert.deepEqual(errors,[]);
  reports.push({engine,synthetic_camera:true,real_gpu:true,selectedModel:capabilities.model,selectedRevision:capabilities.revision,metrics,records});
  console.log(JSON.stringify({engine,metrics,statuses:records.map(r=>r.status),models:records.map(r=>r.body.model)}));
  await context.close();await browser.close();
  if(engine==='chromium')await new Promise(r=>setTimeout(r,8000));
 }
 fs.mkdirSync(root+'/var',{recursive:true});
 fs.writeFileSync(root+'/var/internvl-browser-report.json',JSON.stringify(reports,null,2)+'\n');
})().catch(e=>{console.error(e);process.exit(1)});

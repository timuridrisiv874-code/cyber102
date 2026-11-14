const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium,webkit,devices}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=path.resolve(__dirname,'../..');
const access=JSON.parse(fs.readFileSync(process.env.CYBER102_ACCESS_FILE || root+'/var/deploy-access.json','utf8'));
const origin=new URL(access.url).origin;
const authorization='Basic '+Buffer.from(access.username+':'+access.password).toString('base64');
(async()=>{
 const reports=[];
 for(const engine of ['chromium','webkit']){
  const browser=engine==='chromium'?await chromium.launch(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{}):await webkit.launch();
  try{
   const page=await browser.newPage(engine==='webkit'?devices['iPhone 13']:{viewport:{width:1280,height:900}});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route(origin+'/**',route=>route.continue({headers:{...route.request().headers(),authorization}}));
   await page.goto(origin+'/materials.html');
   await page.getByText('О подборке',{exact:true}).click();
   await page.locator('#catalog-provenance').getByText(/Уникальных фото: 53/).waitFor();
   assert.match(await page.locator('#catalog-provenance').innerText(),/Экспертные метки дефектов отсутствуют/);
   await page.getByText('О подборке',{exact:true}).click();
   await page.getByText(/500 записей/).waitFor();
   const capsResponse=await page.request.get(origin+'/api/capabilities',{headers:{authorization}});
   assert.equal(capsResponse.ok(),true);
   const capabilities=(await capsResponse.json()).vision;
   assert.equal(capabilities.enabled,true);
   assert.equal(typeof capabilities.model,'string');
   assert.match(capabilities.revision,/^[a-f0-9]{40}$/);
   assert.equal(await page.locator('.coverage-card').count(),12);
   await page.getByLabel('Категория').selectOption('valve');
   await page.locator('.photo-option').first().click();
   const result=page.waitForResponse(async r=>{
    const pathname=new URL(r.url()).pathname;
    if(pathname==='/analyze') return r.status()!==202;
    if(!/^\/api\/analysis-jobs\/[0-9a-f]{32}$/.test(pathname)||r.status()!==200) return false;
    const data=await r.json();return ['succeeded','failed','cancelled','expired'].includes(data.job?.status);
   },{timeout:13*60*1000});
   await page.getByRole('button',{name:'Проверить фото',exact:true}).click();
   const response=await result;assert.equal(response.status(),200);
   const envelope=await response.json();
   if(envelope.job) assert.equal(envelope.job.status,'succeeded',envelope.job.error);
   const body=envelope.job?envelope.job.result:envelope;assert.equal(body.model,capabilities.model);
   if(body.revision!==undefined) assert.equal(body.revision,capabilities.revision);
   assert.ok(['defect','clear','unusable'].includes(body.status));
   if(['defect','clear'].includes(body.status)){
    await page.locator('#photo-result').getByText('О результате',{exact:true}).click();
    assert.ok(!(await page.locator('#photo-result').innerText()).includes(body.model));
    await page.locator('#photo-result').getByText('О результате',{exact:true}).click();
   }else await page.locator('#photo-status').getByText(/Не удалось оценить узел/).waitFor();
   const afterResponse=await page.request.get(origin+'/api/capabilities',{headers:{authorization}});
   assert.equal(afterResponse.ok(),true);
   const after=(await afterResponse.json()).vision;
   assert.equal(after.model,capabilities.model);assert.equal(after.revision,capabilities.revision);
   await page.getByText('Подтверждённые наблюдения',{exact:true}).click();
   await page.locator('#vision-guard_issue').selectOption('true');
   await page.getByRole('button',{name:'Проверить связки',exact:true}).click();
   await page.getByText('Сработало правил: 1.',{exact:true}).waitFor();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   assert.deepEqual(errors,[]);
   reports.push({engine,realHttps:true,realGpu:true,analysisStatus:body.status,model:body.model,revision:capabilities.revision,seconds:body.durationSeconds,manualGuardFinding:true});
   console.log(JSON.stringify(reports.at(-1)));
  }finally{await browser.close();}
 }
 fs.mkdirSync(root+'/var',{recursive:true});
 fs.writeFileSync(root+'/var/customer-browser-report.json',JSON.stringify(reports,null,2)+'\n');
})().catch(e=>{console.error(e);process.exitCode=1;});

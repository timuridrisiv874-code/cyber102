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
   await page.getByText(/Уникальных фото: 53/).waitFor();
   await page.getByText(/500 строк/).waitFor();
   assert.equal(await page.locator('.coverage-card').count(),12);
   await page.getByLabel('Категория').selectOption('valve');
   await page.locator('.photo-option').first().click();
   const result=page.waitForResponse(r=>new URL(r.url()).pathname==='/analyze',{timeout:120000});
   await page.getByRole('button',{name:'Проверить фото',exact:true}).click();
   const response=await result;assert.equal(response.status(),200);
   const body=await response.json();assert.equal(body.model,'OpenGVLab/InternVL3-2B-hf');
   assert.ok(['defect','clear','unusable'].includes(body.status));
   await page.getByText('Подтверждённые наблюдения',{exact:true}).click();
   await page.locator('#vision-guard_issue').selectOption('true');
   await page.getByRole('button',{name:'Проверить связки',exact:true}).click();
   await page.getByText('Сработало правил: 1.',{exact:true}).waitFor();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   assert.deepEqual(errors,[]);
   reports.push({engine,realHttps:true,realGpu:true,analysisStatus:body.status,model:body.model,seconds:body.durationSeconds,manualGuardFinding:true});
   console.log(JSON.stringify(reports.at(-1)));
  }finally{await browser.close();}
 }
 fs.mkdirSync(root+'/var',{recursive:true});
 fs.writeFileSync(root+'/var/customer-browser-report.json',JSON.stringify(reports,null,2)+'\n');
})().catch(e=>{console.error(e);process.exitCode=1;});

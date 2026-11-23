// Execute actual frontend in a minimal DOM harness. This is NOT a visual/browser test.
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const root=path.resolve(__dirname,'..');
const node={addEventListener(){},focus(){},querySelector(){return node},classList:{toggle(){}},style:{},hidden:false};
const sandbox={document:{querySelector:()=>node,querySelectorAll:()=>[],addEventListener(){}},
 localStorage:{getItem:()=>null,setItem(){}},fetch:async()=>({json:async()=>({configured:false})}),
 window:{scrollTo(){}},setTimeout:()=>0,console};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(root,'web/main.js'),'utf8'),sandbox);
const reference=JSON.parse(fs.readFileSync(path.join(root,'reports/finance_demo_v036.json'),'utf8'));
sandbox.fixture=reference;
const result=vm.runInContext(`(()=>{
 if(exactRub('10000000000000001')!=='100 000 000 000 000,01 ₽')throw Error('Exact large amount lost');
 state.store={id:'test',name:'Synthetic',platform:'WB'};
 const statuses={CHECKING:'Данные в процессе проверки',PARTIAL:'Частично загружено',MISMATCH:'Расхождение',STALE:'Устарело',VERIFIED:'Сверено'};
 for(const [status,label] of Object.entries(statuses)){
  const r={...fixture,status,label,contribution_kopeks:status==='VERIFIED'?'33000':null};
  state.finance={reports:[r],batches:[]};
  const html=financePage();
  if(!html.includes(label)||!html.includes('financeReport')||!html.includes('financeControl'))throw Error('Missing state '+status);
  if(status!=='VERIFIED'&&html.includes('330,00 ₽'))throw Error('Blocked contribution displayed');
  if(status==='VERIFIED'&&!html.includes('330,00 ₽'))throw Error('Expected contribution missing');
 }
 const dirty={...fixture,report_id:'<script>alert(1)</script>'};state.finance={reports:[dirty],batches:[]};
 if(financePage().includes('<script>alert(1)</script>'))throw Error('Unescaped report ID');
 state.page='finance';render();
 return {status:'PASS',checks:['five financial states','blocked contribution','exact large money','escaped identifiers','actual render'],visual:false};
})()`,sandbox);
console.log(JSON.stringify(result));

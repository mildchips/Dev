"""Frontend interaction QA using real backend fixtures; NOT real HTTP e2e.
Browser network is blocked in this workspace; tests use a mocked fetch boundary.
"""
from pathlib import Path
import tempfile,json,sys
from playwright.sync_api import sync_playwright
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.server import create_app
from app.local_auth import set_password

def run():
  with tempfile.TemporaryDirectory() as temp:
    path=Path(temp)/'test.db'
    app=create_app(path,test_mode=True)
    set_password(path,'visual-qa-password-1234567')
    with TestClient(app) as client:
      r=client.post('/api/auth/login',json={'passphrase':'visual-qa-password-1234567'})
      assert r.status_code==200,r.text
      client.headers.update({'X-AIPO-CSRF':r.json()['csrf']})
      sid=client.post('/api/demo').json()['id']
      fixtures={
        '/api/auth/status':client.get('/api/auth/status').json(),
        '/api/stores':client.get('/api/stores').json(),
        f'/api/stores/{sid}/dashboard':client.get(f'/api/stores/{sid}/dashboard').json(),
        f'/api/stores/{sid}/ledger?limit=250':client.get(f'/api/stores/{sid}/ledger?limit=250').json(),
        f'/api/stores/{sid}/costs':client.get(f'/api/stores/{sid}/costs').json(),
        f'/api/stores/{sid}/cost-revisions':client.get(f'/api/stores/{sid}/cost-revisions').json(),
        f'/api/stores/{sid}/actions':client.get(f'/api/stores/{sid}/actions').json(),
      }
      example={'baseline':{'label':'До изменений','paid_units':10,'realized_price_rub':'1000','unit_cogs_rub':'500','commission_percent':'15','logistics_unit_rub':'90','storage_unit_rub':'20','advertising_budget_rub':'1000','other_period_costs_rub':'0'},
               'alternative':{'label':'После изменений','paid_units':11,'realized_price_rub':'1050','unit_cogs_rub':'500','commission_percent':'15','logistics_unit_rub':'90','storage_unit_rub':'20','advertising_budget_rub':'1100','other_period_costs_rub':'0'}}
      from app.scenarios import compare,ScenarioRequest
      result=compare(ScenarioRequest(**example))
      fixtures[f'/api/stores/{sid}/scenarios/compare']=result
      html=(ROOT/'web/index.html').read_text().replace('<link rel="stylesheet" href="/assets/styles.css"/>','').replace('<script src="/assets/main.js"></script>','')
      css=(ROOT/'web/styles.css').read_text();js=(ROOT/'web/main.js').read_text()
      output=ROOT/'qa';output.mkdir(exist_ok=True)
      with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
        for width,height,mode in [(1440,900,'desktop'),(390,844,'mobile')]:
          page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
          errors=[]
          page.on('pageerror',lambda err:errors.append(str(err)))
          page.set_content(html)
          page.add_style_tag(content=css)
          page.evaluate('''(fixture)=>{
           Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:()=>null,setItem:()=>{}}});
           window.fetch=async (url,options)=>{
              const result=fixture[url];
              return new Response(JSON.stringify(result===undefined?{detail:'not stubbed'}:result),
                  {status:result===undefined?404:200,headers:{'Content-Type':'application/json'}})
           };
          }''',fixtures)
          page.add_script_tag(content=js)
          page.locator('.item').first.wait_for(timeout=20000)
          assert page.locator('#authGate').is_hidden()
          page.screenshot(path=str(output/f'{mode}_dashboard.png'),full_page=True)
          page.locator('button[data-page="scenarios"]').click()
          page.get_by_text('Что изменится с прибылью?').wait_for()
          page.screenshot(path=str(output/f'{mode}_scenarios.png'),full_page=True)
          page.locator('#scenarioForm button[type=submit]').click()
          page.get_by_text('Расчётный результат сценария').wait_for()
          page.screenshot(path=str(output/f'{mode}_result.png'),full_page=True)
          page.locator('button[data-page="actions"]').click()
          page.get_by_text('План действий').first.wait_for()
          page.screenshot(path=str(output/f'{mode}_actions.png'),full_page=True)
          page.locator('button[data-page="costs"]').click()
          page.get_by_text('Историческая себестоимость').wait_for()
          page.screenshot(path=str(output/f'{mode}_cost_history.png'),full_page=True)
          assert not errors,errors
          print(mode,'screenshots passed, no JS errors')
          page.close()
        browser.close()

if __name__=='__main__':run()

"""Render actual frontend source with actual API-generated synthetic fixtures.
Network navigation to localhost is prohibited in this container, so this runs in a
mock-fetch browser document. It is a frontend visual/interaction smoke test, NOT a
real HTTP end-to-end test.
"""
import json
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
from fastapi.testclient import TestClient
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.server import create_app
with tempfile.TemporaryDirectory() as d, TestClient(create_app(Path(d)/'test.sqlite3')) as client:
    sid=client.post('/api/demo').json()['id']
    fixture={
      '/api/stores':client.get('/api/stores').json(),
      f'/api/stores/{sid}/dashboard':client.get(f'/api/stores/{sid}/dashboard').json(),
      f'/api/stores/{sid}/ledger?limit=250':client.get(f'/api/stores/{sid}/ledger?limit=250').json(),
      f'/api/stores/{sid}/costs':client.get(f'/api/stores/{sid}/costs').json(),
    }
    html=(ROOT/'web/index.html').read_text()
    html=html.replace('<link rel="stylesheet" href="/assets/styles.css"/>','').replace('<script src="/assets/main.js"></script>','')
    js=(ROOT/'web/main.js').read_text()
    css=(ROOT/'web/styles.css').read_text()
    screenshots=ROOT/'screenshots';screenshots.mkdir(exist_ok=True)
    with sync_playwright() as p:
      browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
      for width,height,label in [(1440,900,'desktop'),(390,844,'mobile')]:
        page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda err:errors.append(str(err)))
        page.set_content(html)
        page.add_style_tag(content=css)
        page.evaluate('''(fixture)=>{Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:()=>null,setItem:()=>{}}});window.fetch=async (path,opts)=>{let result=fixture[path];if(!result){return new Response(JSON.stringify({detail:'no mock'}),{status:404,headers:{'Content-Type':'application/json'}});}return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}})};}''',fixture)
        page.add_script_tag(content=js)
        page.locator('h1').wait_for()
        print(label,'title',page.locator('h1').first.inner_text(), 'items',page.locator('.item').count())
        page.screenshot(path=str(screenshots/f'{label}_beginner.png'),full_page=True)
        page.locator('[data-mode=finance]').click()
        page.screenshot(path=str(screenshots/f'{label}_finance.png'),full_page=True)
        page.locator('[data-mode=growth]').click()
        page.screenshot(path=str(screenshots/f'{label}_growth.png'),full_page=True)
        page.get_by_role('button',name='Доказательства и действие ↗').first.click()
        print(label,'dialog',page.locator('#dialogTitle').inner_text())
        page.screenshot(path=str(screenshots/f'{label}_evidence.png'),full_page=True)
        assert not errors,errors
        page.close()
      browser.close()

"""Local synthetic browser smoke. Requires preinstalled Chromium and Playwright."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from saas_lab.core import Lab


with tempfile.TemporaryDirectory(prefix='aipo-browser-037-') as d:
    db=Path(d)/'synthetic.db'
    lab=Lab(db)
    _,tid=lab.provision('synthetic-browser-owner','Synthetic-browser-password-037',organization=True)
    lab.provision('synthetic-browser-manager','Synthetic-browser-password-037')
    token,_=lab.login('synthetic-browser-owner','Synthetic-browser-password-037')
    lab.add_store(lab.authenticate(token),tid,'SYNTHETIC BROWSER STORE')
    with (Path(d)/'server.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-m','saas_lab.server','--db',str(db)],cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('synthetic server failed')
                try:
                    with socket.create_connection(('127.0.0.1',8766),timeout=.2):break
                except OSError:time.sleep(.1)
            result=subprocess.run(['node',str(ROOT/'scripts/browser_lab_v037.cjs'),str(ROOT/'reports/browser_lab_v037.json')],cwd=ROOT,timeout=45,capture_output=True,text=True)
            print(result.stdout);print(result.stderr)
            if result.returncode:
                (ROOT/'reports/browser_lab_v037.json').write_text(__import__('json').dumps({'status':'NOT_VERIFIED','reason':result.stderr[:1500]},indent=2))
                raise SystemExit(result.returncode)
        finally:
            process.terminate();process.wait(timeout=10)

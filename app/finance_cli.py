"""Safe, offline financial demo. No marketplace or LLM imports/calls."""
import json
import tempfile
from pathlib import Path
from .finance import Finance,browser_json
from .storage import connect


def demo():
    examples=Path(__file__).resolve().parents[1]/'examples'/'finance_v036'
    load=lambda name:json.loads((examples/name).read_text())
    with tempfile.TemporaryDirectory(prefix='aipo-finance-demo-') as directory:
        f=Finance(Path(directory)/'synthetic.db')
        with connect(f.path) as db:
            db.execute("INSERT INTO stores(id,name,platform,is_demo) VALUES('synthetic','Synthetic only','WB',1)")
        f.ingest('synthetic',load('report_wb.json'))
        f.add_batch('synthetic',load('batch.json'))
        result=f.control('synthetic','SYN-REPORT-1',load('independent_control.json'))
        assert result['status']=='VERIFIED' and result['contribution_kopeks']==33000
        print(json.dumps(browser_json(result),ensure_ascii=False,indent=2))

if __name__=='__main__':
    demo()

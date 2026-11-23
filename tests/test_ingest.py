import io
import pytest
from openpyxl import Workbook
from app.ingest import parse_ledger,parse_money,IngestError

CSV=b'date;sku;category;amount;quantity;operation_id\n2026-11-01;ABC;sale;1000,50;2;ONE\n2026-11-01;ABC;commission;-120,55;;TWO\n'

def test_csv_decimal_and_row_provenance():
    r=parse_ledger(CSV,'example.csv')
    assert len(r.rows)==2
    assert r.rows[0]['amount_kopeks']==100050
    assert r.rows[0]['source_row']==2
    assert r.sum_kopeks==87995

def test_cp1251_headers():
    content='дата;артикул;категория;сумма;количество\n01.11.2026;АРТ1;sale;200,00;1\n'.encode('cp1251')
    r=parse_ledger(content,'test.csv')
    assert r.rows[0]['sku']=='АРТ1'

def test_negative_fee_required():
    content=b'date;sku;category;amount\n2026-11-01;A;commission;100\n'
    with pytest.raises(IngestError,match='negative|отрицательной'):
        parse_ledger(content,'x.csv')

def test_no_implicit_payout_category():
    with pytest.raises(IngestError,match='category'):
        parse_ledger(b'date;sku;category;amount\n2026-11-01;A;payout;200\n','x.csv')

def test_undefined_formula_does_not_turn_into_amount():
    wb=Workbook();ws=wb.active
    ws.append(['date','sku','category','amount','quantity'])
    ws.append(['2026-11-01','A','sale','=1+1',1])
    bio=io.BytesIO();wb.save(bio)
    with pytest.raises(IngestError):parse_ledger(bio.getvalue(),'test.xlsx')

def test_xlsx_values():
    wb=Workbook();ws=wb.active
    ws.append(['date','sku','category','amount','quantity'])
    ws.append(['2026-11-01','A','sale',100.75,1])
    bio=io.BytesIO();wb.save(bio)
    assert parse_ledger(bio.getvalue(),'test.xlsx').sum_kopeks==10075

def test_money_round_half_up():
    assert parse_money('1 234,565')==123457
    assert parse_money('-50.00')==-5000

def test_no_untrusted_format():
    with pytest.raises(IngestError):parse_ledger(b'hi','report.xls')

def test_file_too_large():
    with pytest.raises(IngestError,match='5 МБ'):
        parse_ledger(b'0'*(5*1024*1024+1),'x.csv')

def test_reject_bad_date():
    with pytest.raises(IngestError,match='Дата'):
        parse_ledger(b'date;sku;category;amount\nnot-date;A;sale;200\n','x.csv')

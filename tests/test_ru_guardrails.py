from app.ru_guardrails import DeploymentPlan, validate_plan, is_loopback_peer, runtime_posture
from app.server import create_app
from fastapi.testclient import TestClient


def test_loopback_only():
    assert is_loopback_peer('127.0.0.1')
    assert is_loopback_peer('127.4.5.6')
    assert is_loopback_peer('::1')
    assert not is_loopback_peer('192.168.1.8')
    assert not is_loopback_peer('172.16.0.1')
    assert not is_loopback_peer('localhost')
    assert not is_loopback_peer(None)


def test_local_plan_block_public_bind():
    assert validate_plan(DeploymentPlan(profile='local',public_host='0.0.0.0'))
    assert validate_plan(DeploymentPlan(profile='local',public_host='192.168.1.10'))
    assert not validate_plan(DeploymentPlan(profile='local',public_host='127.0.0.1'))


def test_ru_production_fails_even_if_regions_set():
    issues = validate_plan(DeploymentPlan(profile='production'))
    assert any('BLOCKED' in msg for msg in issues)
    assert any('tenant_isolation_verified' in msg for msg in issues)


def test_foreign_regions_and_crossborder_flagged():
    issues = validate_plan(DeploymentPlan(profile='production',ai_processing_region='EU',
                                          backup_region='KZ',crossborder_transfer_enabled=True))
    assert any('ai_processing_region' in i for i in issues)
    assert any('backup_region' in i for i in issues)
    assert any('Cross-border' in i for i in issues)


def test_disclosure_does_not_claim_compliance():
    assert runtime_posture()['ru_saas_ready'] is False
    assert runtime_posture()['data_residency_verified'] is False


def test_local_testclient_posture_and_remote_peer_rejection(tmp_path):
    app = create_app(tmp_path/'x.db',test_mode=False)
    # Explicit Host header alone is NOT sufficient. Remote test client fails closed.
    with TestClient(app,base_url='http://127.0.0.1') as client:
        r=client.get('/api/health')
        assert r.status_code == 403
        assert 'Local-only' in r.text


def test_authenticated_posture_local_only(tmp_path):
    from app.local_auth import set_password
    db=tmp_path/'x.db'
    app=create_app(db,test_mode=True)
    set_password(db,'super-long-sample-password')
    with TestClient(app) as client:
        assert client.get('/api/system/posture').status_code == 401
        response=client.post('/api/auth/login',json={'passphrase':'super-long-sample-password'})
        assert response.status_code == 200
        response=client.get('/api/system/posture')
        assert response.status_code == 200
        assert response.json()['ru_saas_ready'] is False

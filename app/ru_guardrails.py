"""Russia-first deployment policy + LOCAL-ONLY safety checks.

This module is a policy gate, NOT evidence of legal compliance or a cloud rollout.
It intentionally fails closed on public deployment with this single-user app.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from dataclasses import dataclass, asdict
from typing import Literal


class DeploymentBlocked(ValueError):
    pass


def is_loopback_peer(host: str | None) -> bool:
    """Only IPv4/IPv6 loopback literal addresses are accepted, not DNS names."""
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class DeploymentPlan:
    profile: Literal['local', 'production'] = 'local'
    hosting_region: str = 'RU'
    database_region: str = 'RU'
    object_region: str = 'RU'
    backup_region: str = 'RU'
    secret_region: str = 'RU'
    telemetry_region: str = 'RU'
    ai_processing_region: str = 'RU'
    public_host: str = '127.0.0.1'
    crossborder_transfer_enabled: bool = False
    human_reviewed_legal_basis: bool = False
    external_auth_rbac_ready: bool = False
    tls_gateway_ready: bool = False
    kms_ready: bool = False
    incident_response_ready: bool = False
    backup_restore_drill_passed: bool = False
    data_retention_policy_ready: bool = False
    tenant_isolation_verified: bool = False


def validate_plan(plan: DeploymentPlan) -> list[str]:
    issues: list[str] = []
    if plan.profile == 'local':
        if plan.public_host not in ('127.0.0.1', '::1', 'localhost'):
            issues.append('Local profile must be bound to a loopback interface')
        return issues
    if plan.profile != 'production':
        return ['Unknown deployment profile']
    # This local application does NOT support independent SaaS accounts.
    issues.append('BLOCKED: v0.3.7 local single-user application cannot be deployed as public SaaS')
    for key in ('hosting_region','database_region','object_region','backup_region',
                'secret_region','telemetry_region','ai_processing_region'):
        if getattr(plan,key) != 'RU':
            issues.append(f'{key}: Russian-resident processing policy is not met')
    if plan.crossborder_transfer_enabled:
        issues.append('Cross-border data processing requires documented legal review and procedure')
    mandatory = ('human_reviewed_legal_basis','external_auth_rbac_ready','tls_gateway_ready',
                 'kms_ready','incident_response_ready','backup_restore_drill_passed',
                 'data_retention_policy_ready','tenant_isolation_verified')
    for field in mandatory:
        if not getattr(plan, field):
            issues.append(f'Missing production gate: {field}')
    return issues


def assert_deployable(plan: DeploymentPlan) -> None:
    issues = validate_plan(plan)
    if issues:
        raise DeploymentBlocked('; '.join(issues))


def runtime_posture(*, local_only: bool = True) -> dict:
    return {
        'app_version': '0.3.7',
        'target_market': 'Russian WB/Ozon sellers',
        'multiuser_lab_available': True,
        'financial_app_multiuser': False,
        'security_profile': 'LOCAL_ONLY' if local_only else 'UNSUPPORTED',
        'ru_saas_ready': False,
        'real_marketplace_connectors': False,
        'production_rbac': False,
        'production_kms': False,
        'tls_public_endpoint': False,
        'data_residency_verified': False,
        'note': 'This response is a readiness disclosure, not a security/compliance certification.'
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Check readiness for RU deployment; never approves public SaaS of this version')
    parser.add_argument('--profile', choices=('local','production'), default='local')
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    plan = DeploymentPlan(profile=args.profile,public_host=args.host)
    issues = validate_plan(plan)
    print(json.dumps({'profile':args.profile,'approved':not issues,'issues':issues,
                      'note':'Legal readiness requires a qualified review and actual configuration tests.'},
                     ensure_ascii=False,indent=2))
    return 0 if not issues else 2


if __name__ == '__main__':
    raise SystemExit(main())

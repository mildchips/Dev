"""Repeatable, assumption-driven infrastructure sizing for 1,000 users."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
import json


@dataclass(frozen=True)
class Scenario:
    name: str
    users: int
    stores_per_user: float
    groups_per_store_day: int
    pages_per_group: float
    active_sync_hours: int
    peak_multiplier: float
    average_response_kb: int
    storage_compression_ratio: float
    retained_days: int


def calculate(s: Scenario):
    stores=ceil(s.users*s.stores_per_user)
    requests_day=ceil(stores*s.groups_per_store_day*s.pages_per_group)
    avg_rps=requests_day/(s.active_sync_hours*3600)
    raw_gb_day=requests_day*s.average_response_kb/1e6
    return {
        **asdict(s),
        'connections':stores,
        'provider_api_requests_per_day':requests_day,
        'provider_api_requests_per_month':requests_day*30,
        'average_requests_per_second_in_window':round(avg_rps,3),
        'planning_peak_requests_per_second':round(avg_rps*s.peak_multiplier,3),
        'estimated_raw_download_gb_day':round(raw_gb_day,3),
        'estimated_raw_storage_gb_for_retention':round(raw_gb_day*s.retained_days*s.storage_compression_ratio,1),
        'NOTE':'Planning assumptions, not API allowances or tested capacity.'
    }


SCENARIOS=[
 Scenario('Light',1000,1.0,8,2,16,5,30,.35,90),
 Scenario('Base',1000,1.4,12,3,16,6,80,.35,90),
 Scenario('Heavy',1000,2.0,24,6,16,8,150,.35,90),
]

if __name__=='__main__':
    print(json.dumps([calculate(s) for s in SCENARIOS],ensure_ascii=False,indent=2))

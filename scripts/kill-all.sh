#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m app.integration_cli stop --scope global --id '*' --reason emergency_manual_shutdown
python -m app.integration_cli status
printf '\nIntegration guard stopped future dispatches; revoke keys at WB/Ozon immediately if compromised.\n'

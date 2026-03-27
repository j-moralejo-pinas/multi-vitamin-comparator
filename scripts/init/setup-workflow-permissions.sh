#!/bin/bash
# Setup workflow token permissions
# Usage: setup-workflow-permissions.sh <repo_slug>

set -euo pipefail

REPO_SLUG="${1:?Repository slug is required}"

gh api -X PUT "/repos/$REPO_SLUG/actions/permissions/workflow" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  --input <(jq -n '{default_workflow_permissions:"write",can_approve_pull_requests:true}') > /dev/null

echo "✓ Workflow permissions updated"

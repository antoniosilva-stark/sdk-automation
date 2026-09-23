#!/usr/bin/env bash

set -euo pipefail

REPO="antoniosilva-stark/sdk-automation"
BRANCH="main"

apply=false
[ "${1:-}" = "--apply" ] && apply=true

echo "== current state of ${REPO} =="
gh api "repos/${REPO}" --jq '{allow_merge_commit,allow_squash_merge,allow_rebase_merge,allow_update_branch,delete_branch_on_merge}'
gh api "repos/${REPO}/branches/${BRANCH}/protection" 2>/dev/null || echo "no branch protection on ${BRANCH}"

if [ "$apply" = false ]; then
    echo
    echo "dry run. re-run with --apply to apply."
    exit 0
fi

echo "== merge methods =="
gh api -X PATCH "repos/${REPO}" \
    -F allow_merge_commit=true \
    -F allow_squash_merge=false \
    -F allow_rebase_merge=false \
    -F allow_update_branch=false \
    -F delete_branch_on_merge=true \
    --jq '{allow_merge_commit,allow_squash_merge,allow_rebase_merge,allow_update_branch,delete_branch_on_merge}'

echo "== branch protection on ${BRANCH} =="
gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" --input - <<JSON
{
  "required_status_checks": {"strict": true, "contexts": ["rebase-status"]},
  "required_pull_request_reviews": {"required_approving_review_count": 1, "dismiss_stale_reviews": true},
  "enforce_admins": true,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false,
  "required_conversation_resolution": true
}
JSON

echo
echo "== verification =="
gh api "repos/${REPO}/branches/${BRANCH}/protection" \
    --jq '{strict: .required_status_checks.strict, contexts: .required_status_checks.contexts, reviews: .required_pull_request_reviews.required_approving_review_count, admins: .enforce_admins.enabled}'

echo
echo "Emergency lift:"
echo "  gh api -X DELETE repos/${REPO}/branches/${BRANCH}/protection"

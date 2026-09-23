#!/usr/bin/env bash

set -euo pipefail

MARKER="<!-- enforce-rebase -->"
LABEL="not-rebased"

previousId=$(gh api "repos/$REPO/issues/$NUMBER/comments" --paginate --slurp \
    | jq -r "add | map(select(.body | contains(\"$MARKER\"))) | last.id // empty")

upsertComment() {
    if [ -z "$previousId" ]; then
        gh api -X POST "repos/$REPO/issues/$NUMBER/comments" -f body="$1" --silent
        return 0
    fi
    gh api -X PATCH "repos/$REPO/issues/comments/$previousId" -f body="$1" --silent
}

conclusion=failure
title="Branch is not rebased"
if [ "$OK" = "true" ]; then
    conclusion=success
    title="Rebase OK"
fi

gh api -X POST "repos/$REPO/check-runs" \
    -f name="rebase-status" \
    -f head_sha="$HEAD_SHA" \
    -f status="completed" \
    -f conclusion="$conclusion" \
    -f "output[title]=$title" \
    -f "output[summary]=$REASON" --silent

if [ "$OK" = "true" ]; then
    upsertComment "$MARKER
✅ **Rebase OK** — $REASON"
    gh api -X DELETE "repos/$REPO/issues/$NUMBER/labels/$LABEL" --silent 2>/dev/null || true
    exit 0
fi

upsertComment "$(cat <<EOF
$MARKER
❌ **Branch is not rebased** — $REASON

\`\`\`bash
git fetch origin
git rebase origin/$BASE
git push --force-with-lease
\`\`\`

Never \`git merge origin/$BASE\` into the branch: a merge crosses the history and the pull request diff stops being just your change.

Comment **\`@rebased\`** to re-evaluate without pushing again.
EOF
)"

gh api -X POST "repos/$REPO/issues/$NUMBER/labels" -f "labels[]=$LABEL" --silent \
    || echo "::warning::could not apply the $LABEL label"

exit 1

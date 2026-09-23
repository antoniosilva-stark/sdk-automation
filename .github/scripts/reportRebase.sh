#!/usr/bin/env bash

set -euo pipefail

MARKER="<!-- enforce-rebase -->"
LABEL="not-rebased"

previousId=$(gh api "repos/$REPO/issues/$NUMBER/comments" --paginate --slurp \
    | jq -r --arg marker "$MARKER" 'add | map(select((.body // "") | contains($marker))) | last.id // empty')

upsertComment() {
    if [ -z "$previousId" ]; then
        gh api -X POST "repos/$REPO/issues/$NUMBER/comments" -f body="$1" --silent
        return 0
    fi
    gh api -X PATCH "repos/$REPO/issues/comments/$previousId" -f body="$1" --silent
}

if [ "$OK" = "true" ]; then
    if [ -n "$previousId" ]; then
        upsertComment "$MARKER
✅ **Rebase OK** — $REASON"
    fi
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

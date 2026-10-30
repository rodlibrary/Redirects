#!/usr/bin/env bash
# Commit the given files on a new branch, push it, open a pull request into main, and
# start the Checks workflow on it (a pull request opened by a workflow doesn't start
# other workflows by itself).
#
#   open_pr.sh BRANCH "TITLE" BODY_FILE FILE...
#
# Needs GH_TOKEN (the workflow's github.token) with contents, pull-requests and actions
# write permission.
set -euo pipefail
branch="$1"; title="$2"; body_file="$3"; shift 3

git config user.name "github-actions[bot]"
git config user.email "41898283+github-actions[bot]@users.noreply.github.com"
git checkout -q -b "$branch"
git add -- "$@"
if git diff --cached --quiet; then
    echo "Nothing changed; no pull request needed." | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    exit 0
fi
git commit -q -m "$title"
git push -q origin "$branch"

if url=$(gh pr create --base main --head "$branch" --title "$title" --body-file "$body_file"); then
    echo "Opened $url" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
else
    msg="Pushed branch $branch, but couldn't open the pull request (turn on Settings > Actions > General > Allow GitHub Actions to create and approve pull requests). Open it by hand: ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/compare/main...${branch}"
    echo "::warning::$msg"; echo "$msg" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
fi
gh workflow run checks.yml --ref "$branch" || echo "::warning::Couldn't start the checks on $branch; run Actions > Checks > Run workflow on that branch."

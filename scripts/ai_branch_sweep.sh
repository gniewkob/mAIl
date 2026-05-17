#!/usr/bin/env bash
set -euo pipefail

# Sweep AI-generated branches and open PRs.
# Default mode is read-only report. Use --delete-duplicates to remove patch-duplicate remote branches without open PRs.

DELETE_DUPLICATES=0
BASE_BRANCH="${BASE_BRANCH:-main}"
REMOTE="${REMOTE:-origin}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --delete-duplicates)
      DELETE_DUPLICATES=1
      shift
      ;;
    --base)
      BASE_BRANCH="$2"
      shift 2
      ;;
    --remote)
      REMOTE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--delete-duplicates] [--base <branch>] [--remote <name>]" >&2
      exit 2
      ;;
  esac
done

git fetch --all --prune >/dev/null

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh CLI is required for PR discovery." >&2
  exit 1
fi

pr_json="$(gh pr list --state open --limit 200 --json number,title,headRefName,baseRefName,updatedAt,url 2>/dev/null || echo '[]')"
open_pr_heads="$(printf "%s" "$pr_json" | jq -r '.[].headRefName' 2>/dev/null || true)"

# Heuristic: AI/candidate integration branches.
mapfile -t candidates < <(
  git for-each-ref --format='%(refname:short)' "refs/remotes/${REMOTE}" \
    | rg -v "/HEAD$" \
    | rg -i "^${REMOTE}/(jules-|copilot-|ai[-_/]|autofix-|fix/|performance/)"
)

echo "== AI Branch Sweep =="
echo "base=${BASE_BRANCH} remote=${REMOTE}"
echo "open_prs=$(printf "%s" "$pr_json" | jq 'length' 2>/dev/null || echo 0)"
echo

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "No candidate branches found."
  exit 0
fi

to_delete=()

for ref in "${candidates[@]}"; do
  head="${ref#${REMOTE}/}"
  commit_count="$(git rev-list --count "${BASE_BRANCH}..${ref}")"
  files_changed="$(git diff --name-only "${BASE_BRANCH}...${ref}" | wc -l | tr -d ' ')"
  subject="$(git log -1 --pretty=%s "${ref}")"

  if printf "%s\n" "${open_pr_heads}" | rg -x -q "${head}"; then
    pr_state="OPEN_PR"
  else
    pr_state="NO_PR"
  fi

  cherry_line="$(git cherry -v "${BASE_BRANCH}" "${ref}" | head -n 1 || true)"
  # `-` means patch-equivalent commit already present on base branch.
  if [[ "${cherry_line}" =~ ^- ]]; then
    review="DROP"
    reason="patch-equivalent already in ${BASE_BRANCH}"
    duplicate=1
  elif [[ "${commit_count}" == "0" ]]; then
    review="DROP"
    reason="no commits ahead of ${BASE_BRANCH}"
    duplicate=1
  else
    review="NEEDS_REVIEW"
    reason="non-duplicate delta"
    duplicate=0
  fi

  echo "branch=${ref}"
  echo "  pr_state=${pr_state}"
  echo "  commits_ahead=${commit_count}"
  echo "  files_changed=${files_changed}"
  echo "  review=${review}"
  echo "  reason=${reason}"
  echo "  latest_subject=${subject}"

  echo "  changed_files:"
  git diff --name-only "${BASE_BRANCH}...${ref}" | sed 's/^/    - /'
  echo

  if [[ "${DELETE_DUPLICATES}" -eq 1 && "${duplicate}" -eq 1 && "${pr_state}" == "NO_PR" ]]; then
    to_delete+=("${head}")
  fi
done

if [[ "${DELETE_DUPLICATES}" -eq 1 ]]; then
  if [[ ${#to_delete[@]} -eq 0 ]]; then
    echo "No duplicate branches eligible for deletion."
  else
    echo "Deleting duplicate branches with no open PR:"
    printf '  - %s\n' "${to_delete[@]}"
    git push "${REMOTE}" --delete "${to_delete[@]}"
  fi
fi

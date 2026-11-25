#!/bin/bash
# External review by Google Antigravity (agy CLI) with Gemini 4 Hydrogen. Replaces the
# ad hoc Codex review used on 2026-11-18 (Mike, 2026-11-25).
# Usage: scripts/agents/antigravity_review.sh <git range> [model]
# PREVIOUS_REVIEW names the review to follow up (default: the last by name in docs/reviews).
#   e.g. scripts/agents/antigravity_review.sh 8cbaeb4..origin/main
# Runs in plan mode inside the sandbox, so the agent cannot edit the repo. The review text
# is written to docs/reviews/<date>-antigravity-review.md; the raw transcript goes to a
# temporary file outside the repo. AGY_BIN may point at a stub to test without a model call.
set -euo pipefail
RANGE="${1:?usage: antigravity_review.sh <git range> [model]}"
MODEL="${2:-${AGY_MODEL:-gemini-4-hydrogen-high}}"
R="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$R"
AGY="${AGY_BIN:-$(command -v agy || echo "$HOME/.local/bin/agy")}"
[ -x "$AGY" ] || { echo "antigravity_review: agy CLI not found" >&2; exit 2; }
git rev-parse --verify --quiet "${RANGE%%..*}^{commit}" >/dev/null \
  || { echo "antigravity_review: bad range start: $RANGE" >&2; exit 2; }
if [[ "$RANGE" == *..* ]]; then
  git rev-parse --verify --quiet "${RANGE##*..}^{commit}" >/dev/null \
    || { echo "antigravity_review: bad range end: $RANGE" >&2; exit 2; }
fi
TEMPLATE="$(cat scripts/agents/antigravity_review_prompt.md)"
PREVIOUS="${PREVIOUS_REVIEW:-$(ls docs/reviews/*.md | sort | tail -1)}"
PROMPT="${TEMPLATE//\{RANGE\}/$RANGE}"
PROMPT="${PROMPT//\{PREVIOUS\}/$PREVIOUS}"
[[ "$PROMPT" == *"$RANGE"* && "$PROMPT" != *"{RANGE}"* && "$PROMPT" != *"{PREVIOUS}"* ]] \
  || { echo "antigravity_review: could not build the prompt" >&2; exit 2; }
OUT="docs/reviews/$(date +%Y-%m-%d)-antigravity-review.md"
n=2
while [ -e "$OUT" ]; do
  OUT="docs/reviews/$(date +%Y-%m-%d)-antigravity-review-$n.md"; n=$((n + 1))
done
RAW="${TMPDIR:-/tmp}/antigravity-review-$(date +%Y%m%dT%H%M%S).log"
status=0
"$AGY" --print "$PROMPT" --model "$MODEL" --mode plan --sandbox \
  --print-timeout 0s > "$RAW" 2>&1 || status=$?
if [ "$status" -eq 0 ]; then
  {
    echo "# Antigravity review, $(date +%Y-%m-%d)"
    echo
    echo "Antigravity CLI (agy), model $MODEL, plan mode (read-only), on $RANGE"
    echo "($(git rev-parse --short "${RANGE##*..}"))."
    echo
    # agy links files by absolute local path; make them repo-relative.
    sed -e "s#file://$R/#../../#g" -e "s#file://${R// /%20}/#../../#g" "$RAW"
  } > "$OUT"
fi
echo "agy model=$MODEL exit=$status review=$OUT raw=$RAW"
exit "$status"

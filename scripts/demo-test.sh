#!/bin/bash
# Tests all three demo queries before the GIF recording
# Run after: bash scripts/demo-session-seed.sh
# Usage: bash scripts/demo-test.sh

set -e

ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8000}"
PASS=0
FAIL=0

check() {
  local description="$1"
  local query="$2"
  local expected_keyword="$3"

  echo "Testing: $description"

  response=$(curl -s -X GET \
    "$ORCHESTRATOR_URL/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$query'))")&limit=3")

  if echo "$response" | grep -qi "$expected_keyword"; then
    echo "  PASS — found '$expected_keyword' in results"
    PASS=$((PASS + 1))
  else
    echo "  FAIL — '$expected_keyword' not found in results"
    echo "  Response: $response" | head -c 300
    FAIL=$((FAIL + 1))
  fi
  echo ""
}

echo "======================================"
echo "Lumogis Demo Readiness Test"
echo "======================================"
echo ""

# Test 1: Database decision
check \
  "Database decision for Atlas" \
  "What database did we choose for Atlas" \
  "PostgreSQL"

# Test 2: Marcus deadline
check \
  "Marcus deadline" \
  "What is Marcus deadline for Atlas" \
  "Q3"

# Test 3: Pricing discussion (cross-session memory)
check \
  "Pricing from previous session" \
  "What did we decide about Atlas pricing" \
  "15"

echo "======================================"
echo "Results: $PASS passed, $FAIL failed"
echo "======================================"

if [ $FAIL -gt 0 ]; then
  echo ""
  echo "Fix failures before recording."
  echo "If session memory test fails, re-run: bash scripts/demo-session-seed.sh"
  exit 1
else
  echo ""
  echo "All tests pass. Ready to record."
fi

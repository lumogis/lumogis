#!/bin/bash
# Seeds a completed session into Lumogis memory for the demo GIF
# Run once before recording: bash scripts/demo-session-seed.sh

set -e

ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8000}"

echo "Seeding demo session into Lumogis memory..."

curl -s -X POST "$ORCHESTRATOR_URL/session/end" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo-seed-001",
    "messages": [
      {
        "role": "user",
        "content": "I want to think through the pricing model for Atlas. We are targeting 10 to 50 person teams."
      },
      {
        "role": "assistant",
        "content": "Based on your research notes, you were leaning toward a per-seat model in the €12-18 range, with a free tier for teams under 5 to drive bottom-up adoption. You also noted that Marcus suggested flat-fee pricing above 50 seats, which you wanted to revisit with him."
      },
      {
        "role": "user",
        "content": "Right. I think €15 per seat is the right number. Clean, under the psychological threshold, and leaves room to discount for annual billing."
      },
      {
        "role": "assistant",
        "content": "€15 per seat per month noted. That aligns with your research — below Linear'\''s upper range, above Basecamp'\''s effective per-user cost at typical team sizes. Annual billing discount of 15-20% would land you at €12-13 effective monthly, which is a strong anchor."
      }
    ]
  }' | python3 -m json.tool

echo ""
echo "Session seeded. Lumogis now has memory of the Atlas pricing discussion."
echo "Wait 10 seconds for embedding to complete before starting the demo."
sleep 10
echo "Ready."

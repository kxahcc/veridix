---
name: strix-race-condition
description: Detect TOCTOU and multi-request race windows in state-changing operations,
category: null
tags: null
cwe_ids: null
prerequisites: null
chains_with: null
severity_boost: {}
references: null
authors: null
license: null
version: 0.1.0
trigger: race_condition,concurrency
required_tools: []
required_runner: container
risk_level: L3
content_trust: project_trusted
source: strix
input_schema:
  type: object
  properties:
    target_ref:
      type: string
output_schema:
  type: object
  properties:
    findings:
      type: array
minimal_regression: false
regression_scenarios:
- name: basic
  steps:
  - identify race windows
  - fire concurrent requests
  - record evidence
  expected_oracle: finding or verified negative
---

# Race Condition Testing

## Objective
Detect TOCTOU and multi-request race windows in state-changing operations,
such as balance transfers, coupon redemption, signup, uploads and approvals.

## When to use
- An endpoint performs read-check-write with user-controlled data.
- Multiple concurrent requests can hit the same resource before a lock.

## Steps
1. Identify the operation and its critical state transition.
2. Send concurrent identical requests through the available tool, with
   enough parallelism to make a race observable.
3. Repeat with a bounded number of attempts to distinguish flaky behavior.
4. Verify the resulting state (double redemption, multiple successes) and
  stop once impact is proven.

## Verification
- A finding requires more successful outcomes than allowed, not just
  different response order.
- Show the final resource state and the requests that succeeded.

## Evidence
Save the request set, timestamps, response codes and the final state check
after the race.

## Risk notes
Use test accounts and limit attempts to avoid denial-of-service or resource
exhaustion.

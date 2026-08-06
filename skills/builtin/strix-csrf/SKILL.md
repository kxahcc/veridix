---
name: strix-csrf
description: Determine whether state-changing requests can be forged cross-site without
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
trigger: csrf,cross_site_request_forgery
required_tools: []
required_runner: container
risk_level: L2
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
  - test CSRF tokens
  - verify state-changing requests
  - record evidence
  expected_oracle: finding or verified negative
---

# CSRF and State-Changing Request Protection

## Objective
Determine whether state-changing requests can be forged cross-site without
the victim's knowledge, and whether anti-CSRF controls are effective.

## When to use
- POST/PUT/DELETE endpoints change state and rely on cookies or ambient
  authorization.

## Steps
1. Identify state-changing requests and their anti-CSRF mechanism (token,
  SameSite, custom header, double submit).
2. Build a cross-site proof from a browser origin the tester controls and
  observe whether the request succeeds with the victim's session.
3. Test token presence, binding to session, and SameSite behavior with the
  browser runner.
4. Confirm the impact (password change, settings mutation) in a non-
  destructive way on the test account.

## Verification
- A valid finding needs the forged request to execute with the victim
  session and change state.
- Token presence alone is not sufficient; check whether it is validated.

## Evidence
Save the original request, the cross-origin PoC, browser runner result and
the observed state change.

## Risk notes
Only use the tester's own accounts; never trigger destructive actions.

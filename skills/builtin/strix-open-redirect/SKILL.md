---
name: strix-open-redirect
description: Find redirect endpoints that forward to attacker-controlled destinations
  and
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
trigger: open_redirect
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
  - test redirect parameters
  - confirm open redirect
  - record evidence
  expected_oracle: finding or verified negative
---

# Open Redirect Detection

## Objective
Find redirect endpoints that forward to attacker-controlled destinations and
assess chaining impact (phishing, OAuth, CORS token theft).

## When to use
- URL parameters feed redirects, return URLs, next/logout paths or SSO
  callbacks.

## Steps
1. Enumerate redirect parameters and follow them with safe test values.
2. Test absolute URLs, protocol-relative URLs, backslashes, encoded slashes,
  CRLF and open-redirect regex bypasses.
3. Confirm the final `Location` header or browser navigation reaches the
  tester-controlled origin.
4. Chain with OAuth/SSO or CORS only when the redirect itself is proven.

## Verification
- The response must navigate to the attacker origin in the browser context,
  not merely reflect a URL.
- Record the redirect chain and whether credentials/state can be carried.

## Evidence
Save the crafted URL, redirect chain, final URL and browser navigation proof.

## Risk notes
Do not use payloads that steal real user sessions. Keep the proof on lab or
authorized test accounts.

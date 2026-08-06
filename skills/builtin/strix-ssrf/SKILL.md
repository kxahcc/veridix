---
name: strix-ssrf
description: Find places where the server fetches attacker-controlled URLs or inputs,
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
trigger: ssrf,server_side_request_forgery
required_tools:
- web.ssrf.test
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
  - enumerate URL inputs
  - test callback
  - record evidence
  expected_oracle: finding or verified negative
---

# Server-Side Request Forgery

## Objective
Find places where the server fetches attacker-controlled URLs or inputs,
then prove the request is made by the server to a target of the tester's
choosing.

## When to use
- URL, redirect, image proxy, import, webhook or SSO callback parameters.
- The application exposes an outbound request primitive.

## Steps
1. Map URL-like inputs and request modes (GET, POST, multipart, headers).
2. Use an OAST/interaction endpoint or a lab-only internal host to prove the
   server-side request: `oast.create` followed by the injected URL.
3. Test scheme restrictions (`file://`, `gopher://`, DNS rebinding) only when
   the baseline request works and the target authorizes it.
4. Check whether the response body reflects the fetched resource, which
   determines blind vs full SSRF.

## Verification
- The callback must come from the application host, not the tester browser.
- A full SSRF needs the fetched response body or status; a blind SSRF needs
  a matched interaction request.
- Assess access to cloud metadata only in an authorized lab.

## Evidence
Save the input, resulting request to the interaction endpoint, interaction ID
and response/error excerpt.

## Risk notes
Do not probe cloud metadata or internal services unless explicitly
authorized. Stop at proof of SSRF and impact chaining only with approval.

---
name: cyberstrikeai-web-attack-methods
description: 'Run a structured web assessment: recon, authentication, input handling,'
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
trigger: web_attack_methods,owasp_testing
required_tools:
- nuclei.scan
- web.sqlmap.scan
required_runner: container
risk_level: L3
content_trust: project_trusted
source: cyberstrikeai
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
  - apply skill guidance
  - collect evidence
  - report
  expected_oracle: finding or verified negative
---

# Web Attack Methodology

## Objective
Run a structured web assessment: recon, authentication, input handling,
session, logic and client-side controls.

## When to use
- A web application is in scope and a broad but structured sweep is needed.

## Steps
1. Map endpoints, parameters, auth flows and technologies.
2. Test access control and authentication: IDOR, privilege escalation,
  session fixation, weak credentials.
3. Test input handling: injection, XSS, SSRF, SSTI, file upload, XXE.
4. Test business logic and client-side controls with real workflows.

## Verification
- Apply the matching focused skill for every candidate before reporting.

## Evidence
Save per-category request/response proof and verification refs.

---
name: strix-sqlmap
description: Detect SQL injection in request parameters and confirm impact with a
  minimal,
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
trigger: sqlmap,sql_injection
required_tools:
- web.sqlmap.scan
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
  - enumerate parameters
  - run sqlmap
  - confirm injection and dump scope
  expected_oracle: finding or verified negative
---

# SQL Injection Detection and Exploitation

## Objective
Detect SQL injection in request parameters and confirm impact with a minimal,
non-destructive proof.

## When to use
- A parameter reflects user input into database-backed responses.
- An earlier probe (single quote, boolean diff, timing diff) suggests SQLi.
- The target is authorized for active testing and exploitation is allowed.

## Steps
1. Map the request: method, URL, parameters, cookies and headers.
2. Run `web.sqlmap.scan` with the exact request context and start with
   `--batch --level=1 --risk=1 --dbms=<detected>`.
3. Confirm the injection point and retrieve only the minimal proof (version,
   current database) needed for the finding.
4. If the automated scan is inconclusive, manually test one payload and
   compare response diff, timing or error behavior.

## Verification
- A valid finding needs a reproducible injected request and response.
- Boolean and error-based evidence are stronger than a single timing diff.
- Do not claim data exfiltration unless the actual data is shown and
  authorized.

## Evidence
Save the injected URL, payload, response excerpt, sqlmap command line and
the identified database banner.

## Risk notes
Avoid destructive operations (`--drop`, `--os-shell` unless explicitly
authorized). Limit `--risk` and `--level` on production-like targets.

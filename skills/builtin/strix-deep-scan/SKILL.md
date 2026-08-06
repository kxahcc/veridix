---
name: strix-deep-scan
description: Perform a comprehensive but bounded scan across network, web and code
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
trigger: deep_scan,thorough_scan
required_tools:
- nmap.scan
- nuclei.scan
- web.sqlmap.scan
- code.sast.semgrep
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
  - run deep scan
  - exploit candidates
  - verify findings
  expected_oracle: finding or verified negative
---

# Deep Vulnerability Scan

## Objective
Perform a comprehensive but bounded scan across network, web and code
surfaces, then verify candidate findings.

## When to use
- A full engagement sweep is requested and authorization is broad.

## Steps
1. Asset discovery first; never scan unknowns.
2. Run network service discovery, web fingerprinting and vulnerability
  templates in parallel where safe.
3. For each candidate, apply the matching focused skill for verification.
4. Aggregate findings by asset and severity, discarding duplicates.

## Verification
- Every reported finding must pass the focused verification step.

## Evidence
Save per-stage evidence with run ids and candidate-to-verified mapping.

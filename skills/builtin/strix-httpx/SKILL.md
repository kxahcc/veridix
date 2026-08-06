---
name: strix-httpx
description: Probe live hosts and extract HTTP status, title, technologies, TLS and
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
trigger: httpx,http_probe,fingerprint
required_tools: []
required_runner: container
risk_level: L1
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
  - probe hosts with httpx
  - capture status/title/tech
  - record assets
  expected_oracle: finding or verified negative
---

# HTTP Probing and Technology Fingerprint

## Objective
Probe live hosts and extract HTTP status, title, technologies, TLS and
redirects for asset enrichment.

## When to use
- After DNS/host discovery to filter live web services.

## Steps
1. Run `httpx` against resolved hosts with status/title/tech detection.
2. Record redirect chains and TLS information.
3. Use the result to select web skills per host.

## Verification
- Host liveness and tech are evidence for asset records, not findings.

## Evidence
Save the probe output and target list.

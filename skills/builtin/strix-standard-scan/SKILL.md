---
name: strix-standard-scan
description: 'Run a repeatable, conservative scan baseline for an engagement: discovery,'
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
trigger: standard_scan
required_tools:
- nmap.scan
- nuclei.scan
- web.nikto.scan
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
  - run standard scan
  - verify web services
  - triage results
  expected_oracle: finding or verified negative
---

# Standard Engagement Scan

## Objective
Run a repeatable, conservative scan baseline for an engagement: discovery,
network, web and code checks, with all findings verified.

## When to use
- A standard authorized assessment starts and no special constraints exist.

## Steps
1. Asset map from authorized scope.
2. Network scan with service detection.
3. Web fingerprinting, content discovery and focused vulnerability checks.
4. Code review when source is in scope.
5. Verify candidates and produce the final finding set.

## Verification
- Standard scan output must be reproducible and evidence-backed.

## Evidence
Save stage outputs, verification requests and finding materialization.

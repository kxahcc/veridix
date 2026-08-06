---
name: strix-quick-scan
description: 'Produce a fast initial picture of a target: live hosts, open ports,
  web'
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
trigger: quick_scan,fast_scan
required_tools:
- nmap.scan
- nuclei.scan
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
  - run bounded nmap scan
  - run nuclei templates
  - triage results
  expected_oracle: finding or verified negative
---

# Quick Reconnaissance Scan

## Objective
Produce a fast initial picture of a target: live hosts, open ports, web
technologies and obvious exposures.

## When to use
- The mission asks for a quick first pass or the agent needs orientation.

## Steps
1. Resolve and probe live hosts.
2. Scan top ports with service detection.
3. Fingerprint web technologies and run high-severity templates.
4. Summarize candidates for focused follow-up.

## Verification
- Quick scan output is orientation evidence, not verified findings.

## Evidence
Save probe output and the follow-up plan.

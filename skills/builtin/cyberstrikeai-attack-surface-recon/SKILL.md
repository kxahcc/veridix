---
name: cyberstrikeai-attack-surface-recon
description: 'Build an ordered attack surface from authorized assets: domains, subdomains,'
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
trigger: attack_surface_recon,reconnaissance
required_tools:
- browser.open
- proxy.list
required_runner: container
risk_level: L1
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

# Attack Surface Reconnaissance

## Objective
Build an ordered attack surface from authorized assets: domains, subdomains,
hosts, web apps, APIs, cloud services and exposed ports.

## When to use
- A new mission needs scope mapping before any active exploitation.

## Steps
1. Passive: certificate transparency, DNS history, search engines, archives.
2. Active: resolution, HTTP probing, port scanning, tech fingerprinting.
3. Application: endpoints, parameters, authentication surface, API docs.
4. Enrich each asset with source, confidence and test priority.

## Verification
- Every asset must be tied to an observation; deduplicate and note scope.

## Evidence
Save discovery commands, source tags, resolved assets and priority notes.

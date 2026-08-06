---
name: strix-katana
description: Crawl the application to discover endpoints, parameters and client-side
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
trigger: katana,crawler,endpoint_discovery
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
  - crawl target scope
  - collect endpoints and parameters
  - record observations
  expected_oracle: finding or verified negative
---

# Crawler-Based Endpoint Discovery

## Objective
Crawl the application to discover endpoints, parameters and client-side
routes that static enumeration would miss.

## When to use
- The app is JS-heavy or endpoint discovery needs browser-like behavior.

## Steps
1. Run `katana` with scope filters and depth bounded to the target.
2. Collect paths, query parameters and forms; add them to the asset map.
3. For each new endpoint, classify auth requirement and response behavior.

## Verification
- Discovered endpoints are assets, not findings; test them with the right
  skill.

## Evidence
Save crawl output, discovered paths and the relevant response excerpts.

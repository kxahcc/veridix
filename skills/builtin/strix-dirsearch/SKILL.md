---
name: strix-dirsearch
description: Perform recursive web content discovery with a persistent wordlist while
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
trigger: dirsearch,directory_brute,web_scan
required_tools:
- web.dirsearch.scan
required_runner: container
risk_level: L2
content_trust: project_trusted
source: strix
input_schema:
  type: object
  properties:
    url:
      type: string
output_schema:
  type: object
  properties:
    endpoints:
      type: array
minimal_regression: false
regression_scenarios:
- name: basic
  steps:
  - run dirsearch against the target
  - collect discovered paths with status codes
  - record evidence
  expected_oracle: finding or verified negative
---

# Directory Search with Dirsearch

## Objective
Perform recursive web content discovery with a persistent wordlist while
controlling false positives and scope.

## When to use
- The target has many extensions and recursive paths worth mapping.

## Steps
1. Run `web.dirsearch.scan` with extensions matching the tech stack.
2. Filter by status codes and content length to remove soft-404s.
3. Manually fetch high-value paths and inspect response headers/body.

## Verification
- Report paths only when the direct request proves exposure or a behavior.

## Evidence
Save command, filtered output and direct request evidence.

---
name: strix-subfinder
description: Discover subdomains using certificate transparency, DNS and OSINT sources
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
trigger: subfinder,subdomain_enumeration
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
  - enumerate subdomains
  - deduplicate
  - record assets
  expected_oracle: finding or verified negative
---

# Passive Subdomain Enumeration

## Objective
Discover subdomains using certificate transparency, DNS and OSINT sources
without active scanning.

## When to use
- Domain scope is authorized and the agent needs the full asset surface.

## Steps
1. Run `subfinder` for the root domain with passive sources.
2. Resolve and filter live hosts with `base.dns.resolve`/`httpx`.
3. Add each unique asset to the asset store with its source.

## Verification
- A subdomain is an asset; findings require service-level evidence.

## Evidence
Save the subdomain list, source tags and resolution results.

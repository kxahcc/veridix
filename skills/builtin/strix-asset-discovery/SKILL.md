---
name: strix-asset-discovery
description: Build an ordered asset map (domains, hosts, web apps, APIs) and prioritize
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
trigger: asset_discovery,reconnaissance
required_tools:
- browser.open
- proxy.list
required_runner: browser
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
  - open target root
  - list proxy observations
  - normalize endpoints and auth states
  expected_oracle: finding or verified negative
---

# Asset Discovery and Attack Surface Mapping

## Objective
Build an ordered asset map (domains, hosts, web apps, APIs) and prioritize
testing by exposure.

## When to use
- A new engagement starts, or the asset store is incomplete.

## Steps
1. Passive discovery: subdomains, certificates, DNS and historical data.
2. Active discovery: resolved hosts, live HTTP probes, ports.
3. Web/app discovery: endpoints, technologies, API paths.
4. Record every asset with source and confidence.

## Verification
- Assets must be deduplicated and tied to observation evidence.

## Evidence
Save discovery commands, source tags and resolved asset records.

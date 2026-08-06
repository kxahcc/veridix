---
name: strix-naabu
description: Find open ports quickly with a lightweight scanner before service-specific
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
trigger: naabu,port_discovery
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
  - run naabu port scan
  - feed results to service scan
  - record evidence
  expected_oracle: finding or verified negative
---

# Port Discovery with Naabu

## Objective
Find open ports quickly with a lightweight scanner before service-specific
checks.

## When to use
- Host discovery needs broad port coverage without nmap's full scan.

## Steps
1. Run `naabu` with top ports or full TCP on the authorized host.
2. Validate open ports with `network.tcp.connect`.
3. Pass validated ports to service fingerprinting or exploitation skills.

## Verification
- Report only validated open ports; service findings need banners.

## Evidence
Save scan output, validation probes and timestamps.

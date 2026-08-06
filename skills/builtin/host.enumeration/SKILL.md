---
name: host.enumeration
description: Enumerate a single host's services, shares, users and network presence
  to
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
trigger: host
required_tools:
- shell.probe
- nmap.scan
- nuclei.scan
required_runner: container
risk_level: L3
content_trust: project_trusted
source: builtin
input_schema:
  type: object
  properties:
    target:
      type: string
output_schema:
  type: object
  properties:
    services:
      type: array
minimal_regression: true
regression_scenarios:
- name: enumerate_services
  steps:
  - scan host ports
  - validate service versions
  - emit structured observations
  expected_oracle: service model populated
---

# Host Enumeration

## Objective
Enumerate a single host's services, shares, users and network presence to
find high-value targets.

## When to use
- A host is in scope and the agent needs service/user context.

## Steps
1. Port and service discovery.
2. SMB/SSH/SNMP checks depending on exposed services.
3. Enumerate shares, banners and version information.
4. Use results to choose the next skill.

## Verification
- Enumeration findings need service-level proof.

## Evidence
Save service banners, share lists and response excerpts.

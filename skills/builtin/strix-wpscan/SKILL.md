---
name: strix-wpscan
description: Enumerate a WordPress installation (core, plugins, themes, users) and
  verify
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
trigger: wpscan,wordpress,cms_scan,web_scan
required_tools:
- web.wpscan.scan
required_runner: container
risk_level: L3
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
    findings:
      type: array
minimal_regression: false
regression_scenarios:
- name: basic
  steps:
  - run wpscan against a WordPress target
  - identify plugins, themes and user enumeration signals
  - verify candidate findings
  expected_oracle: finding or verified negative
---

# WordPress Vulnerability Scan

## Objective
Enumerate a WordPress installation (core, plugins, themes, users) and verify
known vulnerabilities with version evidence.

## When to use
- WhatWeb or response headers identify WordPress.

## Steps
1. Run `web.wpscan.scan` with non-aggressive enumeration.
2. Enumerate plugins/themes and versions from readme and response clues.
3. Match findings to version ranges only when version evidence is clear.
4. Verify exploitation impact in a lab target before reporting.

## Verification
- A WordPress finding needs the vulnerable component and version proof.

## Evidence
Save scan output, version source, matched advisory and exploit proof.

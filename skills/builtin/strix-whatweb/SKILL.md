---
name: strix-whatweb
description: Identify CMS, frameworks, server software and plugins from HTTP responses.
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
trigger: whatweb,fingerprint,tech_detect,web_scan
required_tools:
- web.whatweb.scan
required_runner: container
risk_level: L1
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
    technologies:
      type: array
minimal_regression: false
regression_scenarios:
- name: basic
  steps:
  - run whatweb against the target
  - extract server, framework and technology signals
  - record evidence
  expected_oracle: verified negative
---

# Web Technology Fingerprinting

## Objective
Identify CMS, frameworks, server software and plugins from HTTP responses.

## When to use
- Before selecting CMS/framework-specific skills.

## Steps
1. Run `web.whatweb.scan` against the target.
2. Match fingerprints with plugin/version evidence in response headers and
  body.
3. Use the result to select wpscan, semgrep or CVE templates.

## Verification
- Fingerprints are candidate evidence; version-based findings need direct
  response proof.

## Evidence
Save the whatweb output and source response excerpts.

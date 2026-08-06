---
name: cyberstrikeai-source-code-hunting
description: Search source for injection, auth, crypto, file and secret-handling flaws,
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
trigger: source_code_hunting,code_review
required_tools:
- code.sast.semgrep
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

# Source Code Vulnerability Hunting

## Objective
Search source for injection, auth, crypto, file and secret-handling flaws,
then prove each candidate with a complete data-flow path.

## When to use
- Source code is available and code-audit findings are requested.

## Steps
1. Inventory entry points: routes, CLI handlers, parsers, imports.
2. Search for dangerous sinks and tainted sources: SQL/OS command,
  deserialization, path join, template render, crypto misuse.
3. Trace each candidate from source to sink with validation checks.
4. Classify true/false positive and estimate reachability.

## Verification
- A finding needs a reachable path and a concrete trigger, not a regex hit.

## Evidence
Save file/line, code excerpt, data flow and minimal trigger.

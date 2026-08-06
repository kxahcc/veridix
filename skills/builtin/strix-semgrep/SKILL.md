---
name: strix-semgrep
description: Find security-relevant patterns in source code and trace data flow to
  a sink
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
trigger: semgrep,sast,source_scan
required_tools:
- code.sast.semgrep
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
  - run semgrep rules
  - triage findings
  - record evidence
  expected_oracle: finding or verified negative
---

# Source Code Security Review with Semgrep

## Objective
Find security-relevant patterns in source code and trace data flow to a sink
before reporting.

## When to use
- The agent has access to source code and needs code-level findings.

## Steps
1. Run `code.sast.semgrep` with rules relevant to the language/framework.
2. For each match, read the surrounding code and trace input to sink.
3. Classify true/false positive using data flow, validation and callers.
4. Produce a finding only with a reproducible code path.

## Verification
- Pattern matches are candidates; a finding needs a complete source-to-sink
  path and reachable trigger.

## Evidence
Save rule id, file/line, code excerpt, data flow and a minimal trigger.

---
name: strix-sql-injection
description: 'Confirm SQL injection without relying on a single automated scanner
  verdict:'
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
trigger: sql_injection
required_tools:
- web.sqlmap.scan
required_runner: container
risk_level: L3
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
  - test SQL injection
  - confirm with sqlmap
  - record evidence
  expected_oracle: finding or verified negative
---

# Manual SQL Injection Validation

## Objective
Confirm SQL injection without relying on a single automated scanner verdict:
probe the parameter, identify the backend, and verify the payload effect.

## When to use
- A parameter is reflected in database-backed responses and sqlmap output is
  ambiguous.

## Steps
1. Baseline: send the normal request and record response status, length,
  timing and error behavior.
2. Injection probes: use a single quote, a benign arithmetic condition
  (`id=1 AND 1=1` vs `id=1 AND 1=2`) and a comment terminator.
3. Classify the technique: error-based, boolean, union, time-based or
  blind.
4. Prove impact with the minimal authorized query (version, database name),
  never a destructive statement.

## Verification
- A positive needs a reproducible response difference between the true and
  false conditions, or a reflected computed value.
- Timing-only findings need a controlled baseline and repeated samples.

## Evidence
Save the parameter, both requests, response excerpts, timings and the
detected backend.

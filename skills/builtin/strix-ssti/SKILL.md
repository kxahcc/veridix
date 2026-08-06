---
name: strix-ssti
description: Detect template engines evaluating user input, identify the engine, and
  prove
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
trigger: ssti,template_injection
required_tools: []
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
  - test template injection
  - confirm RCE path
  - record evidence
  expected_oracle: finding or verified negative
---

# Server-Side Template Injection

## Objective
Detect template engines evaluating user input, identify the engine, and prove
code evaluation or data exposure with a non-destructive expression.

## When to use
- Templates, email bodies, PDF/HTML generation or error pages render user
  input.
- A payload like `{{7*7}}` or `${7*7}` produces computed output.

## Steps
1. Probe arithmetic expressions for common engines: `{{7*7}}`, `${7*7}`,
   `<%= 7*7 %>`, `#{7*7}`.
2. Identify the engine from the reflected output and the tech stack.
3. Prove impact with an engine-specific read/eval expression that touches
   only the lab target or a clearly authorized file.
4. If full RCE is not authorized, stop at code-evaluation proof and record
   the exact expression.

## Verification
- Arithmetic reflection is candidate evidence; the engine must be identified.
- Code evaluation proof should show a computed expression, not only error
  syntax.
- Record whether the injection is in the template body or a template path.

## Evidence
Save the payload, response containing the evaluated expression, engine
detection notes and the tool/output used.

## Risk notes
SSTI can become RCE. Do not run `system()` or file writes unless the target
is a lab and exploitation is explicitly allowed.

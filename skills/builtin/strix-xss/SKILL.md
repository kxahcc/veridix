---
name: strix-xss
description: Detect reflected, stored and DOM XSS with a payload that proves script
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
trigger: xss,cross_site_scripting
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
  - test reflected XSS
  - test stored XSS
  - record evidence
  expected_oracle: finding or verified negative
---

# Cross-Site Scripting Verification

## Objective
Detect reflected, stored and DOM XSS with a payload that proves script
execution, then verify the impact in a real browser context.

## When to use
- User-controlled input is reflected in HTML, attribute, JavaScript or URL
  context.
- The target has a browser/headless runner available for execution proof.

## Steps
1. Identify every reflection point and context (tag, attribute, script,
   URL).
2. Use context-specific payloads: `"><svg/onload=...>` for tag context,
   `" autofocus onfocus=...` for attribute context, and URL/JS encodings
   where needed.
3. Use `web.dom-xss.test` for DOM sinks when the source is read and passed
   to `innerHTML`, `document.write`, `eval` or similar.
4. In the browser runner, confirm script execution with a non-destructive
   marker (cookie set, DOM mutation, request to a local OAST) instead of
   `alert(1)` only.

## Verification
- Reflection alone is not XSS; the payload must execute.
- For stored XSS, show the stored payload and the victim-view execution.
- Note CSP, WAF and encoding as impact modifiers, not proof of absence.

## Evidence
Save request with payload, response reflection, browser console/DOM proof or
OAST callback, and the execution context.

## Risk notes
Do not use payloads that exfiltrate real credentials. Keep the proof
non-destructive and inside the approved scope.

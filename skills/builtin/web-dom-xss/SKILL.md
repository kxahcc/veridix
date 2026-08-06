---
name: web-dom-xss
description: Find client-side XSS where a DOM source flows into a dangerous sink.
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
trigger: dom xss, dom-based xss, xss
required_tools:
- web.dom-xss.test
required_runner: native
risk_level: L3
content_trust: project_trusted
source: builtin
input_schema:
  type: object
  properties:
    target:
      type: string
    username:
      type: string
    password:
      type: string
    marker:
      type: string
output_schema:
  type: object
  properties:
    triggered:
      type: boolean
minimal_regression: false
regression_scenarios:
- name: dvwa_dom_xss
  steps:
  - login and set security low
  - load xss_d page with hash payload in real browser
  - verify marker executes in DOM
  expected_oracle: verified XSS finding
---

# DOM-Based XSS Verification

## Objective
Find client-side XSS where a DOM source flows into a dangerous sink.

## When to use
- The app is JavaScript-heavy and reflected XSS checks are negative.

## Steps
1. Identify sources: `location`, `document.referrer`, `postMessage`, storage.
2. Identify sinks: `innerHTML`, `outerHTML`, `document.write`, `eval`,
  `setTimeout`, `jQuery.html`.
3. Use `web.dom-xss.test` with a payload that changes a DOM marker.
4. Verify execution in the browser runner.

## Verification
- DOM reflection alone is not proof; the payload must reach the sink and
  execute.

## Evidence
Save source/sink trace, payload, browser console/DOM proof.

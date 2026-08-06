---
name: strix-nuclei
description: Run a focused, evidence-backed vulnerability scan with Nuclei templates,
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
trigger: nuclei,vuln_scan,template_scan
required_tools:
- nuclei.scan
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
  - run nuclei template scan
  - triage matched templates
  - record evidence
  expected_oracle: finding or verified negative
---

# Nuclei Template Scan

## Objective
Run a focused, evidence-backed vulnerability scan with Nuclei templates,
then verify every candidate before reporting.

## When to use
- Web or service targets where the surface is already known.
- Quick coverage across common CVEs, misconfigurations and exposures.
- Not as the sole source of a verified finding: template matches are
  candidate evidence only.

## Steps
1. Run `nuclei.scan` against the target with severity tags appropriate to
   scope, starting with `critical,high`.
2. Use template groups that match discovered tech (`tech`, `exposures`,
   `misconfiguration`, `cves`).
3. Parse the matched template, request and response evidence.
4. Manually replay the exact request and confirm the condition is real.

## Verification
- Confirm the finding is reproducible with the same request.
- Distinguish template false positives (waf, default page, generic regex)
  from actual behavior.
- Check whether the impact requires authentication or chaining.

## Evidence
Record template id, severity, matched URL, request, response excerpt,
`curl` replay command and scan timestamp.

## Risk notes
Limit to authorized targets. Avoid `-headless` heavy scans unless the target
is a lab. Do not report a CVE without version evidence.

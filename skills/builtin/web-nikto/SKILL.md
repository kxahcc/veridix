---
name: web-nikto
description: Run a focused Nikto web-server scan and turn structured observations into verified findings
category: web
tags:
- nikto
- web_scan
- server_configuration
cwe_ids:
- CWE-16
prerequisites:
- authorized_web_target
chains_with:
- verifier
severity_boost: {}
references: []
authors: []
license: ''
version: '1.0'
trigger: nikto,web_scan,server_configuration,http_headers
required_tools:
- web.nikto.scan
required_runner: container
risk_level: L2
content_trust: project_trusted
source: builtin
input_schema:
  type: object
  properties:
    target:
      type: string
    port:
      type: integer
output_schema:
  type: object
minimal_regression: false
regression_scenarios:
- name: dvwa_nikto
  steps:
  - scan target with web.nikto.scan
  - parse structured findings
  - verify at least one Exposure finding
  expected_oracle: verified Exposure finding
---

# Nikto Web Scan

## Objective
Run a scoped Nikto scan against an authorized web target, normalize its
structured observations and stop when evidence supports a verified finding
or coverage is complete.

## When to use
- The target exposes HTTP/HTTPS services and the task asks for web-server
  hardening, outdated component, or exposure checks.

## Steps
1. Confirm the target URL and protocol are inside the authorized scope.
2. Call `web.nikto.scan` with the target and any task-provided port.
3. Normalize every structured observation into a finding candidate with
  endpoint, category, severity and matched evidence.
4. If no new candidates remain, finish with a negative coverage record.

## Verification
- A finding is verified only when the structured observation carries
  evidence (artifact refs, matched_at or replay proof) and the category is
  accepted by the mission oracle.

## Evidence
Save the scan command, target, structured observations, artifact refs and
the final coverage record.

## Risk notes
Do not exceed the authorized host/port list; stop before any action that
could modify the target.

---
name: strix-xxe
description: Detect XML parsers resolving external entities and prove file read or
  SSRF
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
trigger: xxe,xml_external_entity
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
  - test XML parsers
  - confirm external entity
  - record evidence
  expected_oracle: finding or verified negative
---

# XML External Entity Testing

## Objective
Detect XML parsers resolving external entities and prove file read or SSRF
with a controlled, minimal payload.

## When to use
- XML request bodies, SOAP, SVG upload, DOCX/XLSX parsing or XML-based APIs.

## Steps
1. Send a well-formed XML body with an internal entity first to confirm the
   parser resolves entities: `<!DOCTYPE foo [<!ENTITY x "ok">]>`.
2. If internal entities resolve, test external entities against a local OAST
   or a harmless lab file: `<!ENTITY xxe SYSTEM "http://...">`.
3. Test parameter entities for blind XXE and out-of-band exfiltration only
   with an authorized interaction endpoint.
4. Check XXE via file read on a non-sensitive lab file, not real system
   secrets.

## Verification
- Entity resolution alone is not enough; show the resolved value or callback.
- Distinguish file-read XXE from SSRF XXE and document the parser/library.

## Evidence
Save the full request body, response excerpt or OAST interaction, and the
parser identified in the tech stack.

## Risk notes
Do not read credentials or system files outside an authorized lab. Do not
make the parser fetch arbitrary internal services without approval.

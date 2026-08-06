---
name: veridix-redteam-orchestration
description: 'Run a complete authorized red-team flow: recon, scanner dispatch, verifier,'
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
trigger: hw,redteam,red_team,orchestration,remote_node
required_tools:
- nmap.scan
- nuclei.scan
- fscan.scan
- web.nikto.scan
- web.sqlmap.scan
required_runner: container
risk_level: L3
content_trust: project_trusted
source: builtin
input_schema:
  type: object
  properties:
    target_ref:
      type: string
    node_id:
      type: string
output_schema:
  type: object
  properties:
    findings:
      type: array
minimal_regression: true
regression_scenarios:
- name: remote-toolchain
  steps:
  - dispatch scanner to the configured remote node
  - execute nmap/nuclei/fscan/nikto/sqlmap within the Docker toolchain
  - verify candidate findings through replay evidence and the oracle
  - materialize verified findings and pass the evidence gate
  expected_oracle: verified finding or verified negative
---

# Veridix Red Team Orchestration

## Objective
Run a complete authorized red-team flow: recon, scanner dispatch, verifier,
reporter, with evidence gates between each stage.

## When to use
- The mission requests a red-team, penetration test or orchestrated
  multi-stage assessment.

## Steps
1. Recon: asset discovery, live host validation, service fingerprinting.
2. Scanner: dispatch nmap/nuclei/fscan/nikto/sqlmap through the container
  toolchain to the configured runner.
3. Verifier: replay each candidate with direct evidence and classify
  true/false positive.
4. Reporter: materialize verified findings with severity, evidence refs and
  remediation notes.

## Verification
- Every finding must have a verified oracle result and replay evidence
  before it becomes a final finding.

## Evidence
Save per-stage run ids, tool output, replay proof and final finding refs.

---
name: strix-nmap
description: 'Map the network exposure of a host: open TCP/UDP ports, running services,'
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
trigger: nmap,port_scan,service_scan
required_tools:
- nmap.scan
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
  - run nmap service scan
  - identify open ports and versions
  - record evidence
  expected_oracle: finding or verified negative
---

# Nmap Service and Port Discovery

## Objective
Map the network exposure of a host: open TCP/UDP ports, running services,
versions and host fingerprints, then turn that into a scoped testing plan.

## When to use
- Asset reconnaissance after target authorization is confirmed.
- Before web or exploitation skills so the agent tests only live services.
- Follow-up service-specific checks after ports are identified.

## Steps
1. Run `nmap.scan` against the target host with service detection enabled
   (`-sV`) and safe scripts (`-sC`) on the top ports first.
2. If the first pass is inconclusive, scan all TCP ports with a bounded rate.
3. Record the service banner, version, open ports and any script output.
4. Select the next skill based on the actual service set, not the task name.

## Verification
- A finding is not valid from the port list alone; it must show a service
  behavior or misconfiguration.
- Re-run the version detection on the exact endpoint before claiming a
  vulnerable version.

## Evidence
Save the raw scan output, the target, command arguments, timestamps and the
resolved service tuple for each open port.

## Risk notes
Prefer `-Pn` only when host discovery is blocked. Do not scan ranges outside
the approved target scope.

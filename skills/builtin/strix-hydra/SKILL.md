---
name: strix-hydra
description: Test login services against a controlled credential policy using Hydra,
  with
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
trigger: hydra,brute_force,login_attack,host_auth
required_tools:
- host.auth.hydra
required_runner: container
risk_level: L3
content_trust: project_trusted
source: strix
input_schema:
  type: object
  properties:
    service:
      type: string
    target:
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
  - run hydra credential validation against an authorized service
  - record attempts and successful responses
  - verify candidate finding
  expected_oracle: finding or verified negative
---

# Online Credential Testing

## Objective
Test login services against a controlled credential policy using Hydra, with
bounded attempts and evidence.

## When to use
- Password testing is explicitly authorized and the service supports it.

## Steps
1. Identify the protocol and exact login form/request.
2. Use a small targeted wordlist, not a full rockyou dump.
3. Run `host.auth.hydra` with a bounded retry rate.
4. Verify a successful login and stop immediately.

## Verification
- The finding is a valid credential plus a successful authenticated request.

## Evidence
Save the service, method, attempt count, successful request and redacted
credential evidence.

## Risk notes
Online brute force can lock accounts or trigger SOC alerts. Keep attempts
minimal and use test accounts where possible.

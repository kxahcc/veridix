---
name: strix-active-directory
description: Enumerate AD structure, users, shares, trust relationships and Kerberos
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
trigger: active_directory,ad_security
required_tools:
- ad.nmap.smb
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
  - enumerate AD services
  - test SMB and LDAP
  - record evidence
  expected_oracle: finding or verified negative
---

# Active Directory Security Testing

## Objective
Enumerate AD structure, users, shares, trust relationships and Kerberos
behavior, then verify misconfigurations in an authorized AD lab.

## When to use
- The target includes AD services (LDAP, SMB, Kerberos, DNS) and the lab
  environment supports domain testing.

## Steps
1. Enumerate the domain: LDAP search, SMB shares, DNS records, Kerberos
  policy.
2. Identify users, groups, ACLs and interesting targets from enumeration.
3. Test AS-REP roasting, Kerberoasting and unconstrained delegation only in
  the authorized AD lab.
4. For lateral movement, verify each step with evidence and stop at scope
  boundaries.

## Verification
- A finding needs the domain object, command/output and the resulting
  security impact.

## Evidence
Save LDAP/SMB/Kerberos output, target object IDs and redacted credentials.

## Risk notes
AD testing can disrupt production. Run in the provided AD lab and never
persist credentials outside the test environment.

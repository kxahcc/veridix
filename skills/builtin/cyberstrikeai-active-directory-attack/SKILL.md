---
name: cyberstrikeai-active-directory-attack
description: 'Execute an authorized AD assessment: enumeration, credential attacks,'
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
trigger: active_directory_attack,ad_security
required_tools:
- ad.nmap.smb
required_runner: container
risk_level: L3
content_trust: project_trusted
source: cyberstrikeai
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
  - apply skill guidance
  - collect evidence
  - report
  expected_oracle: finding or verified negative
---

# Active Directory Attack Methodology

## Objective
Execute an authorized AD assessment: enumeration, credential attacks,
privilege escalation and lateral movement, with evidence gates.

## When to use
- An AD lab or authorized domain is in scope and the agent has network
  access to domain services.

## Steps
1. Enumerate domain users, groups, shares, LDAP ACLs and Kerberos policy.
2. Test AS-REP roasting, Kerberoasting, password spraying and delegation
  abuse only in the authorized lab.
3. Escalate privileges through ACL abuse, GPO, service accounts or ADCS
  misconfigurations when present.
4. Lateral movement via PsExec/WinRM/SMB requires explicit authorization.

## Verification
- Every step needs domain object evidence and the resulting access proof.

## Evidence
Save enumeration output, attack commands, target object ids and redacted
credentials.

## Risk notes
Stop at scope boundaries; do not persist or weaken production AD.

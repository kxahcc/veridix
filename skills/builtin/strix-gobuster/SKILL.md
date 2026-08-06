---
name: strix-gobuster
description: Brute-force web directories or DNS subdomains with bounded wordlists,
  then
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
trigger: gobuster,directory_brute,dns_recon,web_scan
required_tools:
- web.directory.brute
required_runner: container
risk_level: L2
content_trust: project_trusted
source: strix
input_schema:
  type: object
  properties:
    url:
      type: string
output_schema:
  type: object
  properties:
    endpoints:
      type: array
minimal_regression: false
regression_scenarios:
- name: basic
  steps:
  - run gobuster directory brute against the target
  - collect discovered paths with status codes
  - record evidence
  expected_oracle: finding or verified negative
---

# Directory and DNS Brute Force with Gobuster

## Objective
Brute-force web directories or DNS subdomains with bounded wordlists, then
filter false positives.

## When to use
- Content discovery is authorized and the technology or subdomain scope is
  known.

## Steps
1. Use `web.directory.brute` for directories with a status-code filter.
2. Use DNS mode only for the authorized domain and a conservative wordlist.
3. Verify each hit with a direct request and compare content length.
4. Triage soft-404s, redirects and default pages before adding assets.

## Verification
- Hits must be validated with a follow-up request, not just a 200 in the
  brute-force output.

## Evidence
Save the wordlist, command, hit list and follow-up response.

## Risk notes
Keep DNS brute force small; long runs can look like abuse.

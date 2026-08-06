---
name: strix-idor
description: Find resources accessed by predictable identifiers without authorization
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
trigger: idor,broken_object_level_authorization
required_tools:
- web.authz.test
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
  - enumerate object ids
  - cross-account test
  - record evidence
  expected_oracle: finding or verified negative
---

# Insecure Direct Object Reference

## Objective
Find resources accessed by predictable identifiers without authorization
checks, and prove cross-user or privilege impact.

## When to use
- API endpoints and web handlers use IDs, UUIDs, filenames or sequential
  keys for object access.

## Steps
1. Enumerate object references from responses, links, client state and API
   docs.
2. Test access with a second account or lower-privilege session when
   available; otherwise verify ownership checks with your own object.
3. For numeric IDs, test adjacent values only in the approved range and log
   each request.
4. Confirm the response differs meaningfully (200 with another user's data)
   and is not cached or public.

## Verification
- A finding requires two identities or privilege levels showing the missing
  check.
- Response 200 alone is weak; show the sensitive field and compare with the
  authorized account.
- Note if the issue is object enumeration, mass assignment or broken access
  control.

## Evidence
Save both requests, authenticated sessions (redacted), response excerpts and
the identifier ranges tested.

## Risk notes
Do not enumerate large identifier spaces. Test only a minimal proof set and
stop when impact is established.

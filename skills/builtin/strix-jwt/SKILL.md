---
name: strix-jwt
description: Verify that a JWT is validated cryptographically and cannot be forged
  or
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
trigger: jwt,token_manipulation
required_tools: []
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
  - decode JWT
  - test algorithm confusion
  - record evidence
  expected_oracle: finding or verified negative
---

# JWT Security Testing

## Objective
Verify that a JWT is validated cryptographically and cannot be forged or
confused across algorithms, keys or issuers.

## When to use
- The application authenticates requests with JWT and a claim affects
  authorization.

## Steps
1. Decode the JWT header, payload and signature; note algorithm, issuer,
   audience, expiry and claim semantics.
2. Test algorithm confusion only when the header is accepted unchanged
   (`alg=none`, HS256 with the public key, RS256 downgrade).
3. Test weak keys with a small local wordlist when the token is short-lived
   and the target is authorized.
4. Verify expiry, issuer and audience are enforced by reusing an expired or
   wrong-audience token.

## Verification
- A forged token must be accepted and produce an observable authorization
  difference.
- Distinguish signature-stripping acceptance from algorithm confusion.
- Record the exact token, the modified claim and the API response.

## Evidence
Save original and forged JWT, signing header used, request/response pair and
the validation library identified if possible.

## Risk notes
Do not brute-force online. Test weak-key cracking offline on lab material.

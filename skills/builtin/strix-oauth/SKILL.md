---
name: strix-oauth
description: Verify that OAuth/OIDC authorization flows validate redirect URIs, state,
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
trigger: oauth,sso,token_testing
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
  - enumerate oauth flows
  - test token validation
  - record evidence
  expected_oracle: finding or verified negative
---

# OAuth and SSO Flow Testing

## Objective
Verify that OAuth/OIDC authorization flows validate redirect URIs, state,
nonce and token audience, and cannot be abused for account takeover.

## When to use
- The app uses OAuth/OIDC login, API delegation, third-party authorization
  or single sign-on.

## Steps
1. Map the authorization flow: redirect URI, scope, state, nonce, token
   endpoint and userinfo/token validation.
2. Test redirect URI handling with exact, subdomain and path-matching
   variations; only report a finding when the crafted URI is accepted and
   carries an authorization code.
3. Test missing or weak state/nonce by replaying an authorization code or
   starting a cross-site flow.
4. Verify audience/issuer checks by using a token from the wrong client,
   issuer or resource.

## Verification
- A finding needs an accepted crafted flow or token that produces a session
  or data access difference.
- A missing `state` alone is weak unless you show login CSRF impact.

## Evidence
Save the flow requests, crafted redirect URI, callback proof and the
resulting session/API response.

## Risk notes
Only use the tester's own accounts and consent screens; do not intercept
real users.

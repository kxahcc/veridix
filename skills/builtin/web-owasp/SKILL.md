---
name: web-owasp
description: Run focused checks across command injection, XSS, CSRF, rate limiting,
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
trigger: command injection, xss, csrf, rate limit, brute force, session, weak session,
  security headers, cookie flags, information disclosure, backup file, directory listing
required_tools:
- web.owasp.test
required_runner: native
risk_level: L3
content_trust: project_trusted
source: builtin
input_schema:
  type: object
  properties:
    target:
      type: string
    check:
      type: string
    username:
      type: string
    password:
      type: string
output_schema:
  type: object
  properties:
    observation_count:
      type: integer
minimal_regression: false
regression_scenarios:
- name: dvwa_owasp_sweep
  steps:
  - run command_injection
  - run backup_file
  - run xss_reflected and xss_stored
  - run rate_limit, csrf, weak_session, info_disclosure, security_headers
  expected_oracle: verified findings per present category
---

# OWASP Web Check Suite

## Objective
Run focused checks across command injection, XSS, CSRF, rate limiting,
session strength, information disclosure and security headers, then verify
each candidate.

## When to use
- The mission asks for an OWASP-style web sweep or a category is in scope.

## Steps
1. For each enabled check, use `web.owasp.test` with the target and
  credentials when required.
2. Apply the category-specific methodology:
   - command injection: inject `;`, `|`, backticks and observe execution.
   - xss: execute a non-destructive marker in the browser runner.
   - csrf: verify a cross-origin state change.
   - rate limit: bounded repeated requests and lockout behavior.
   - session: cookie flags, fixation, expiry and entropy.
   - info disclosure: headers, errors, source maps and debug endpoints.
   - security headers: CSP, HSTS, X-Frame-Options, nosniff, Referrer-Policy.
3. Verify every candidate with a dedicated proof before materializing.

## Verification
- Category sweep output is a candidate list; each reported item must have
  its own replayable proof.

## Evidence
Save check name, request/response pairs and the verification result.

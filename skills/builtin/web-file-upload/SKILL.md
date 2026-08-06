---
name: web-file-upload
description: Test upload endpoints for content-type trust, extension handling, path
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
trigger: file upload, upload, unrestricted upload, rce
required_tools:
- web.file-upload.test
required_runner: native
risk_level: L3
content_trust: project_trusted
source: builtin
input_schema:
  type: object
  properties:
    target:
      type: string
    username:
      type: string
    password:
      type: string
    upload_path:
      type: string
output_schema:
  type: object
  properties:
    marker_present:
      type: boolean
minimal_regression: false
regression_scenarios:
- name: dvwa_upload_rce
  steps:
  - login to target
  - set security low
  - upload PHP marker
  - request uploaded file and verify marker
  expected_oracle: verified RCE finding
---

# File Upload Security Testing

## Objective
Test upload endpoints for content-type trust, extension handling, path
traversal and executable upload impact in a controlled way.

## When to use
- The application accepts files and stores or serves them.

## Steps
1. Map upload endpoint and accepted content types.
2. Upload benign files first; then test extension and MIME variations in a
  lab-safe manner.
3. Test filename traversal and double extensions only where authorized.
4. Confirm whether the file is served, stored with a predictable path or
  parsed by the application.

## Verification
- A finding requires the upload to produce an observable security impact
  (file disclosure, stored XSS, code execution in lab).

## Evidence
Save request/response, file content, storage path and served proof.

## Risk notes
Do not upload malware or destructive files; use inert test content.

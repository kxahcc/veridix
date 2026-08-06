---
name: web-lfi
description: Detect file read primitives in file parameters and prove readable content
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
trigger: lfi, local file inclusion, file inclusion, path traversal
required_tools:
- web.lfi.test
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
    lfi_path:
      type: string
output_schema:
  type: object
  properties:
    has_file_content:
      type: boolean
minimal_regression: false
regression_scenarios:
- name: dvwa_lfi
  steps:
  - login to target
  - request lfi page with /etc/passwd
  - verify file content in response
  expected_oracle: verified LFI finding
---

# Local File Inclusion Testing

## Objective
Detect file read primitives in file parameters and prove readable content
within the authorized scope.

## When to use
- Parameters accept file names, paths, language/locale files or download
  names.

## Steps
1. Identify file-influencing parameters.
2. Test path traversal with safe delimiters (`../`, encoded forms, null
  bytes only in legacy stacks).
3. Confirm the response includes file content or a distinguishable error.
4. Verify impact with a lab-authorized file, not sensitive host files.

## Verification
- A finding needs the included file's content in the response or an
  observable inclusion side effect.

## Evidence
Save the URL/payload, response content and traversal depth tested.

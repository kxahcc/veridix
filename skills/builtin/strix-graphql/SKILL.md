---
name: strix-graphql
description: Discover GraphQL endpoints and test introspection, authorization, batching
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
trigger: graphql,api_testing
required_tools:
- web.graphql.test
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
  - introspect schema
  - test queries and mutations
  - record evidence
  expected_oracle: finding or verified negative
---

# GraphQL API Testing

## Objective
Discover GraphQL endpoints and test introspection, authorization, batching
and input handling without disrupting the API.

## When to use
- The target exposes `/graphql`, `/api/graphql`, `/v1/graphql` or Apollo
  tooling, or queries with JSON bodies.

## Steps
1. Probe common GraphQL paths and run a minimal introspection query.
2. Enumerate queries/mutations, arguments and field types from introspection.
3. Test authorization per operation and argument; batch or aliases only in
  lab scope.
4. Test injection and error disclosure through specific fields and arguments.

## Verification
- Introspection alone is informational; findings need a failed authorization
  or exploitable behavior.
- Record the operation name, variables and response for every proof.

## Evidence
Save the introspection output, executed queries/mutations, variables and
response excerpts.

## Risk notes
Do not run expensive queries or deep nested batching against production.

# Security

Veridix is a security testing agent. Use it only against targets you own or are
explicitly authorized to test.

## Reporting

Do not open a public issue for a vulnerability in Veridix itself. Send details
to the maintainers privately before disclosure.

## Safe defaults

- Targets are checked against the project authorization scope before execution.
- High-risk tools and human gates are recorded as structured approval events.
- Findings become visible only after an evidence gate and, where required,
  human review.

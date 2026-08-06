# Veridix Changelog

## v0.1.0

Veridix v0.1.0 is the first public release. It provides an authorization-aware
security testing agent with Web, TUI, CLI, Docker tool images, built-in
knowledge and skills, and an auditable evidence pipeline.

### Highlights

- Harness / Loop / Graph agent engineering with multi-role orchestration.
- Web, TUI, and CLI entrypoints backed by a shared TypeScript SDK.
- Hybrid retrieval: BM25, vector, graph, rerank, and RRF fusion.
- Built-in ATT&CK, OWASP, CWE, vulnerability methodology, and skill packages.
- Docker tool environment with nmap, nuclei, fscan, sqlmap, Metasploit, ZAP,
  Burp, Caido, and related tooling.
- Evidence gate, finding oracle, human approval, RBAC, audit logging, and
  remote node support.
- Public Docker images:
  - `ghcr.io/kxahcc/veridix/veridix-tools:full`
  - `ghcr.io/kxahcc/veridix/veridix-tools:code-lite`

### Distribution

- GitHub source repository with npm workspace build.
- Docker Compose managed storage and tool environment.
- Windows desktop zip and setup installer.

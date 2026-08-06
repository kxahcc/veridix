# OWASP Check Matrix

| Category | Candidate evidence | Verification |
| --- | --- | --- |
| Command injection | Response/time/error differs with metacharacters | Replay with a harmless marker command |
| Reflected XSS | Payload reflected | Browser execution proof |
| Stored XSS | Payload persisted | Victim-view execution |
| CSRF | State change from cross-origin request | Replay PoC with tester account |
| Rate limit | Repeated requests bypass | Bounded attempt count |
| Weak session | Missing flags, fixation, weak expiry | Reuse/fixation proof |
| Info disclosure | Error, headers, source maps | Direct response excerpt |
| Security headers | Missing HSTS/CSP/nosniff | Header response |

Every reported item must have its own request/response proof.

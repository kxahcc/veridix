---
name: waf-challenge-bypass
description: 'Identify and handle common anti-bot/WAF JavaScript challenges (River
  Security 瑞数, Cloudflare, Akamai, AWS WAF, Incapsula) at observation level: fingerprint
  the challenge, solve it with browser_challenge_solve, replay the cookies into curl/httpx,
  and record blocked coverage when the challenge cannot be cleared. Never brute-force
  or bypass scope boundaries.'
category: security-testing
tags: []
cwe_ids: []
prerequisites: []
chains_with: []
severity_boost: {}
references: []
authors: []
license: ''
version: 1.0.0
trigger: web_discovery,host
required_tools:
- shell.probe
- web.replay
required_runner: native
risk_level: L3
content_trust: project_trusted
source: builtin
input_schema:
  type: object
  properties:
    target:
      type: string
output_schema:
  type: object
minimal_regression: false
regression_scenarios: []
---

# WAF / Anti-Bot Challenge Handling

## When to Use

- The target returns a challenge page instead of real application content: HTTP 202/403 with a JS anti-bot body, a "verify you are human" page, or a WAF block page.
- Observed signals include `$_ts` / `r='m'` (River Security 瑞数), `cf-chl` / `__cf_chl` (Cloudflare), `_abck` / `ak_bmsc` (Akamai), `incap_ses` / `visid_incap` (Incapsula), `awswaf` (AWS WAF), or a generic captcha/challenge page.
- httpx/whatweb/curl are all returning the same challenge body and fingerprinting is masked.

## When NOT to Use

- Do not use this skill to attack the WAF itself or to evade an access-control boundary that the operator did not authorize.
- Do not brute-force challenge cookies, rotate IPs to defeat rate limits, or solve CAPTCHAs at scale.
- If the challenge is an authentication/login wall (not an anti-bot challenge), use authentication and session testing skills instead.

## Workflow

### 1. Confirm the challenge and record evidence

Run `curl_http_probe` with `include_headers: true` and `httpx_probe` with `tech_detect: false`. Record:

- HTTP status (typically 202 or 403);
- challenge cookie names (e.g. `iP188UAHhEoDO`, `cf_clearance`, `_abck`, `bm_sz`);
- body markers (`$_ts`, `r='m'`, `cf-chl`, `ak_bmsc`, etc.);
- whether `/robots.txt` or other static paths return origin content (proves the challenge is path- or JS-dependent, not a full block).

### 2. Solve the challenge with a real browser (observation level)

Call `browser_challenge_solve`:

```json
{
  "url": "https://target/",
  "max_wait_seconds": 15,
  "cookie_jar_path": "artifacts/runs/<run_id>/challenge-cookies.json",
  "screenshot": "artifacts/runs/<run_id>/challenge.png"
}
```

The tool executes the page, waits for network idle, detects challenge markers, and returns the cookie set. If `solved` is true and cookies were returned:

- replay the cookies into `curl_http_probe` with `cookie` (Cookie header) or `cookie_jar` (curl `-b` jar file);
- re-run `httpx_probe` with `cookie` to refresh liveness/headers;
- re-run `whatweb_fingerprint` with the cookie if fingerprints were masked.

### 3. Re-test the previously blocked surface

Re-request the root, `/robots.txt`, login/API paths with the replayed cookies. Compare:

- status code changed from the challenge response to a real origin response;
- content-length/title/body changed to application content;
- challenge cookie is accepted and a session cookie is issued.

### 4. If the challenge cannot be cleared

- Do not loop the same solve attempt more than twice.
- Record the coverage as `waf_blocked` with the challenge type, cookie names, and marker evidence.
- Add a repair/next-step note: browser-observed challenge solve plus cookie replay, or an operator-provided session cookie if the target requires interactive solving.

## Safety Rules

- The browser tool only loads the approved URL and executes its own scripts; never pass attacker-controlled JavaScript that mutates the target.
- Cookie replay stays inside the approved scope and the same engagement run.
- Keep screenshots/cookie jars under `artifacts/runs/<run_id>/`.
- A challenge page is a coverage result, not a finding by itself. Do not report "WAF exists" as a vulnerability unless it causes a measurable security defect (e.g., missing security headers behind the challenge, origin information disclosure).

# veridix-tools offline image

This image is the executable runtime for the layered Tool Packs. Git tracks
the build recipe and the pinned digest, not the large binaries themselves.

## What is inside

- Base: curl, dig, jq, openssl, file, git, python venv.
- Network/host: nmap, masscan, tcpdump, dig, nc, traceroute, hydra,
  smbclient, smbmap, enum4linux, ssh, whois, arp-scan, dnsrecon,
  onesixtyone/SNMP, nikto.
- Web: sqlmap, gobuster, ffuf, dirb, wfuzz, dirsearch, whatweb, wpscan plus
  the browser/proxy plane.
- AD: ldapsearch, smbclient, Kerberos client tools, nmap SMB scripts,
  ldapdomaindump and impacket examples (secretsdump/GetNPUsers/psexec).
- Code: semgrep, detect-secrets, trivy, syft, grype, codeql, spotbugs,
  dependency-check.
- Cloud: awscli.
- Binary: binutils, gdb, binwalk, file, xxd.
- Optional downloads: nuclei, fscan, Metasploit Framework, nikto, enum4linux.

Current pinned digest: `sha256:58aaf27c4e8fe358e0e86c5cb2be6b6889c6471441fbb818f6bd3f4cabc40a93`.

## Local build

Optional binaries are intentionally not committed to git. Download them once:

```powershell
.venv\Scripts\python.exe scripts\fetch_tool_binaries.py
```

The fetcher retries GitHub releases and falls back to domestic mirrors, and
downloads Metasploit from the official apt repository. Then build:

```powershell
docker build -t veridix-tools:full -f deploy\container\veridix-tools\Dockerfile .
```

## Distribution model

The image follows the product as a local/private-registry artifact, like the
Strix sandbox image, rather than being committed to GitHub:

```powershell
docker save veridix-tools:full | gzip > dist-product/veridix-tools-full.tar.gz
```

For a private registry:

```powershell
docker tag veridix-tools:full registry.example.local/veridix/veridix-tools:<digest>
docker push registry.example.local/veridix/veridix-tools:<digest>
```

`deploy/manifests/images.json` and every Tool Pack manifest pin the digest, so
`veridix up`/`doctor` can verify the exact image before enabling capabilities.

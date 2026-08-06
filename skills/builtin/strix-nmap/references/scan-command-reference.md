# Nmap Command Reference

- Service detection: `nmap -sV -sC <host>`
- Top ports first: `nmap -sV --top-ports 1000 <host>`
- Full TCP: `nmap -p- -sV --min-rate 1000 <host>`
- Host discovery disabled: `nmap -Pn <host>`
- Output: XML for structured evidence, normal output for readability.

Always record the exact command and target when saving evidence.

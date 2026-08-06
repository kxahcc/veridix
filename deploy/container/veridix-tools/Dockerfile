ARG BASE_IMAGE=debian:bookworm-slim

FROM ${BASE_IMAGE} AS downloaded

WORKDIR /downloads
COPY deploy/container/veridix-tools/downloads/nuclei.zip ./nuclei.zip
COPY deploy/container/veridix-tools/downloads/fscan ./fscan
COPY deploy/container/veridix-tools/downloads/nikto.tar.gz ./nikto.tar.gz
COPY deploy/container/veridix-tools/downloads/subfinder ./subfinder
COPY deploy/container/veridix-tools/downloads/httpx ./httpx
COPY deploy/container/veridix-tools/downloads/naabu ./naabu
COPY deploy/container/veridix-tools/downloads/enum4linux ./enum4linux
COPY deploy/container/veridix-tools/downloads/nuclei-templates/ ./nuclei-templates/

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends unzip python3 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/veridix-tools/bin /root/nuclei-templates \
    && if [ -f /downloads/nuclei.zip ]; then \
        unzip -o /downloads/nuclei.zip -d /opt/veridix-tools/bin \
        && chmod +x /opt/veridix-tools/bin/nuclei; \
    fi \
    && if [ -f /downloads/fscan ]; then \
        install -m 0755 /downloads/fscan /opt/veridix-tools/bin/fscan; \
    fi \
    && if [ -f /downloads/nikto.tar.gz ]; then \
        tar -xzf /downloads/nikto.tar.gz -C /opt/veridix-tools \
        && find /opt/veridix-tools -maxdepth 1 -type d -name 'nikto-*' \
            -exec sh -c 'ln -sf "$1/program/nikto.pl" /opt/veridix-tools/bin/nikto' _ {} \;; \
    fi \
    && if [ -f /downloads/subfinder ]; then \
        install -m 0755 /downloads/subfinder /opt/veridix-tools/bin/subfinder; \
    fi \
    && if [ -f /downloads/httpx ]; then \
        install -m 0755 /downloads/httpx /opt/veridix-tools/bin/httpx; \
    fi \
    && if [ -f /downloads/naabu ]; then \
        install -m 0755 /downloads/naabu /opt/veridix-tools/bin/naabu; \
    fi \
    && if [ -f /downloads/enum4linux ]; then \
        install -m 0755 /downloads/enum4linux /opt/veridix-tools/bin/enum4linux; \
    fi \
    && if [ -d /downloads/nuclei-templates ]; then \
        cp -a /downloads/nuclei-templates/. /root/nuclei-templates/; \
    fi

RUN find /opt/veridix-tools -maxdepth 1 -type d -name 'nikto-*' \
    -exec python3 -c 'import sys; p=sys.argv[1]; s=open(p, encoding="utf-8", errors="replace").read(); s=s.replace("JSON->new", "JSON::PP->new").replace("\\\"msg\\\":\\\"$msg\\\"", "\\\"msg\\\":$msg"); open(p,"w",encoding="utf-8").write(s)' {}/program/plugins/nikto_report_json.plugin \;

FROM ${BASE_IMAGE}

ENV PATH="/opt/veridix-tools/bin:/usr/local/bin:${PATH}"

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dnsutils \
        ffuf \
        file \
        git \
        gobuster \
        hydra \
        jq \
        ldap-utils \
        libnet-ssleay-perl \
        masscan \
        netcat-openbsd \
        nmap \
        openssh-client \
        openssl \
        perl \
        python3 \
        ruby \
        smbclient \
        smbmap \
        sqlmap \
        tcpdump \
        traceroute \
        unzip \
        wfuzz \
        whatweb \
        whois \
    && rm -rf /var/lib/apt/lists/*

COPY --from=downloaded /opt/veridix-tools /opt/veridix-tools
COPY --from=downloaded /root/nuclei-templates /root/nuclei-templates

CMD ["sh", "-c", "echo veridix-tools image ready; command -v nmap; command -v sqlmap; command -v nuclei || true"]

"""Import and filter seed URLs from a Tranco or top-domains CSV file."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from search_engine.crawler.sources import parse_seeds

# Substrings and patterns typical of CDN, DNS, ad-tech, telemetry, and non-content infrastructure
_INFRA_PATTERN = re.compile(
    r"(?:"
    r"cdn|dns|arpa|adsrvr|adsystem|adnxs|adobedtm|adobedc|adsafeprotected|"
    r"doubleclick|googlesyndication|googletagmanager|googleadservices|google-analytics|"
    r"app-measurement|app-analytics|analytics|telemetry|metrics|tracking|scorecardresearch|"
    r"crashlytics|sentry|datadoghq|conviva|demdex|omtrdc|criteo|pubmatic|openx|"
    r"rubiconproject|casalemedia|taboola|3lift|360yield|smartadserver|dotomi|lijit|"
    r"appsflyer|branch\.io|onelink\.me|braze\.com|klaviyo\.com|statsigapi|"
    r"edgekey|edgesuite|msedge|akamaiedge|akamai|akadns|akam\.|cloudfront|fastly|"
    r"gstatic|googleapis|googleusercontent|gvt1|gvt2|googlevideo|ggpht|ytimg|"
    r"fbcdn|twimg|cdninstagram|licdn|scdn|mzstatic|aaplimg|rbxcdn|ttvnw|nflx|"
    r"gtld-servers|root-servers|domaincontrol|registrar-servers|name-services|worldnic|"
    r"lencr|pki\.goog|digicert|hcaptcha|awswaf|forter|imcmdb|ntp\.org|"
    r"ipify|icanhazip|ip-api|msftconnecttest|msftncsi|firefox-portal-detection|"
    r"windowsupdate|captiveportal|trafficmanager|elasticbeanstalk|azurefd|azureedge|"
    r"aliyuncs|myqcloud|myhuaweicloud|tencent-cloud|yandexcloud|oraclecloud|"
    r"amazonaws|awsglobalaccelerator|shifen|hicloudcam|ezviz7|triplinkintl|"
    r"gwfb\.net|lsrelayaccess|quickconnect|plex\.direct|steamserver|xboxlive|"
    r"comcast\.net|charter\.net|telekom\.net|virginm\.net|userapi|vkuser|okcdn|"
    r"byteoversea|volcfcdndvs|tiktokcdn|tiktokv|tiktokpangle|kwai|capcutapi|"
    r"wbx2|svc\.ms|sfx\.ms|run\.app|workers\.dev|on\.aws|g\.page|goo\.gl|t\.co|"
    r"amzn\.to|bit\.ly|tinyurl\.com|forms\.gle|wa\.me|t\.me|telegram\.me|"
    r"browser-intake|nr-data|exp-tas|nmrodam|wsdvs|e2ro|sc-gw|nease|"
    r"allawnos|heytap|samsungcloud|samsungqbe|immedia-semi|goskope|nexthink|"
    r"paloaltonetworks|checkpoint|tplinkcloud|mangosip|spaceweb|dropcatch|afternic|"
    r"expireddomains|mybluehost|timeweb|selectel|netangels|space\.pro|skybridge|"
    r"at\.alicdn|tbcache|yccdn|ksyuncdn|qlivecdn|byteglb|ibyteimg|vedcdnlb|cdngslb|"
    r"avcdn|trbcdn|ks-cdn|crcdn|a2z\.com|featureassets|prodregistryv2|shopifysvc|"
    r"cloud\.microsoft|static\.microsoft|usgovcloudapi|beian\.gov\.cn"
    r")"
)


def is_infrastructure_domain(domain: str) -> bool:
    """Return True if domain looks like a CDN, ad network, DNS host, or API endpoint."""
    d = domain.strip().lower()
    if not d or "." not in d:
        return True
    if _INFRA_PATTERN.search(d):
        return True
    # Filter obvious generated hash-like domains (e.g., w2rt6byl5nf1opmc-a.com)
    label = d.split(".", 1)[0]
    return len(label) >= 14 and any(ch.isdigit() for ch in label) and "-" in label


def extract_seeds_from_csv(
    csv_path: Path,
    *,
    limit: int = 500,
    filter_infra: bool = True,
) -> list[str]:
    """Read domains from a Tranco-style CSV and return validated HTTPS seed URLs."""
    candidates: list[str] = []
    seen: set[str] = set()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue
            domain = row[1].strip() if len(row) >= 2 else row[0].strip()
            domain = domain.lower()
            if not domain or domain in ("domain", "url"):
                continue
            if filter_infra and is_infrastructure_domain(domain):
                continue
            if domain in seen:
                continue
            seen.add(domain)
            candidates.append(f"https://{domain}/")
            if limit > 0 and len(candidates) >= limit * 2:
                # Oversample slightly before parse_seeds normalization
                break

    parsed = parse_seeds(candidates, allow_loopback=False)
    valid_urls = [entry.key for entry in parsed.entries]
    if limit > 0:
        return valid_urls[:limit]
    return valid_urls


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import seed URLs from a Tranco/top-1m CSV into data/seeds.txt"
    )
    parser.add_argument("csv_path", type=Path, help="Path to CSV file (e.g. top-1m.csv)")
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum clean domains to import (0 for unlimited, default: 500)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/seeds.txt"),
        help="Output seeds file (default: data/seeds.txt)",
    )
    parser.add_argument(
        "--no-filter",
        action="store_true",
        help="Do not filter out CDN/DNS/telemetry infrastructure domains",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing seeds file instead of overwriting",
    )
    args = parser.parse_args()

    urls = extract_seeds_from_csv(
        args.csv_path,
        limit=args.limit,
        filter_infra=not args.no_filter,
    )

    existing: list[str] = []
    if args.append and args.output.exists():
        existing_parsed = parse_seeds(
            args.output.read_text(encoding="utf-8-sig").splitlines(),
            allow_loopback=False,
        )
        existing = [e.key for e in existing_parsed.entries]

    merged: list[str] = []
    seen = set()
    for u in [*existing, *urls]:
        if u not in seen:
            seen.add(u)
            merged.append(u)

    header = [
        "# Approved Seed URLs for Independent Search Engine",
        f"# Imported from {args.csv_path.name} ({len(merged)} unique seed URLs)",
        "# Format: one normalized HTTP/HTTPS URL per line",
        "",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join([*header, *merged]) + "\n", encoding="utf-8")
    print(f"Wrote {len(merged)} seed URLs to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

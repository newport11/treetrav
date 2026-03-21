import hashlib
import re
import urllib.parse


# Tracking parameters to strip from URLs
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid",
    "mc_cid", "mc_eid", "yclid", "twclid",
    "_ga", "_gl", "ref", "source", "ref_src", "ref_url",
}


def canonicalize_url(raw_url):
    """Normalize a URL to its canonical form and return (canonical_url, url_hash, domain)."""
    url = raw_url.strip()

    # Decode if URL-encoded
    try:
        url = urllib.parse.unquote(url)
    except Exception:
        pass

    # Ensure scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)

    # Normalize scheme to https
    scheme = "https"

    # Normalize hostname
    hostname = (parsed.hostname or "").lower().strip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]

    # Normalize port (drop default ports)
    port = parsed.port
    if port in (80, 443, None):
        netloc = hostname
    else:
        netloc = f"{hostname}:{port}"

    # Normalize path
    path = parsed.path or "/"
    # Remove trailing slash (except for root)
    if len(path) > 1:
        path = path.rstrip("/")
    # Collapse double slashes
    path = re.sub(r"/+", "/", path)

    # Filter query params — remove tracking params, sort remaining
    query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        k: v for k, v in query_params.items()
        if k.lower() not in TRACKING_PARAMS
    }
    sorted_query = urllib.parse.urlencode(
        {k: filtered[k][0] if len(filtered[k]) == 1 else filtered[k] for k in sorted(filtered)},
        doseq=True,
    )

    # Reconstruct
    canonical = urllib.parse.urlunparse((scheme, netloc, path, "", sorted_query, ""))

    # Hash
    url_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return canonical, url_hash, hostname


def extract_domain(url):
    """Extract the domain from a URL."""
    try:
        parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
        hostname = (parsed.hostname or "").lower().strip(".")
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname
    except Exception:
        return ""

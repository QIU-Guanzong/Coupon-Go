"""Production ASGI entry point with an explicit, fail-closed proxy allowlist.

Proxy wiring (item 5, verified): uvicorn's proxy_headers +
forwarded_allow_ips come from FORWARDED_ALLOW_IPS; with no allowlist
proxy handling is off entirely (fail-closed), so request.client.host
is the direct peer. The sign-contract route keeps reading
request.client.host -- it is the real client IP once a trusted proxy
is configured; no second X-Forwarded-For parser lives in the routes.

Access-log wiring (item 6): uvicorn's default access log prints the
full request line, query string included -- a signed download URL
(?exp=...&token=...) would leak its token into the logs. The
StripQueryStringFilter below strips the query string from every
uvicorn.access record (method / path / status are kept) and is
installed at import time, so both `python -m app.serve` and any
other entry point that imports this module get it. The reverse proxy
in front must be configured the same way (see docs/P1D_API.md §4).
"""
from ipaddress import ip_network
import logging
import os

import uvicorn


def trusted_proxies(raw):
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    for entry in entries:
        network = ip_network(entry, strict=False)
        if network.prefixlen == 0:
            raise ValueError("A catch-all forwarded-proxy network is unsafe")
    return ",".join(entries)


class StripQueryStringFilter(logging.Filter):
    """Drop the query string from uvicorn access-log records.

    Uvicorn logs access as '%s - "%s %s %s" %s' with args
    (client, method, full_path, http_version, status): the full_path
    element (index 2) carries '?exp=...&token=...'. Strip everything
    from the first '?' in any string arg so no token ever reaches a
    log line; method, path and status survive unchanged.
    """

    def filter(self, record):
        if record.args:
            args = list(record.args)
            changed = False
            for i, arg in enumerate(args):
                if isinstance(arg, str) and "?" in arg:
                    args[i] = arg.split("?", 1)[0]
                    changed = True
            if changed:
                record.args = tuple(args)
        return True


def install_access_log_filter():
    """Attach the query-stripping filter to the uvicorn.access logger
    (idempotent -- adding the same filter class twice is harmless
    because logging dedupes identical filter instances only by
    identity, so guard on class)."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, StripQueryStringFilter)
               for f in logger.filters):
        logger.addFilter(StripQueryStringFilter())


install_access_log_filter()


if __name__ == "__main__":
    proxies = trusted_proxies(os.getenv("FORWARDED_ALLOW_IPS", ""))
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000,
                proxy_headers=bool(proxies), forwarded_allow_ips=proxies)

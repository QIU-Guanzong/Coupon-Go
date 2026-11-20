"""Production ASGI entry point with an explicit, fail-closed proxy allowlist."""
from ipaddress import ip_network
import os

import uvicorn


def trusted_proxies(raw):
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    for entry in entries:
        network = ip_network(entry, strict=False)
        if network.prefixlen == 0:
            raise ValueError("A catch-all forwarded-proxy network is unsafe")
    return ",".join(entries)


if __name__ == "__main__":
    proxies = trusted_proxies(os.getenv("FORWARDED_ALLOW_IPS", ""))
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000,
                proxy_headers=bool(proxies), forwarded_allow_ips=proxies)

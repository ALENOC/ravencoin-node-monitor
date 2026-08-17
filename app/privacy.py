"""Server-side address masking for PRIVACY_MODE. Applied before a snapshot
ever reaches `_send_json` so raw addresses never cross the wire to the
browser when privacy mode is on - masking client-side would be pointless
window dressing.
"""

import re

_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def mask_ip(host):
    """Bare host (no port) -> masked. IPv4 keeps the first two octets,
    IPv6 keeps the first two groups. Hostnames and .onion addresses (not
    real, geolocatable IPs to begin with) pass through unchanged.
    """
    if not host:
        return host
    if host.endswith(".onion"):
        return host
    m = _IPV4_RE.match(host)
    if m:
        return f"{m.group(1)}.{m.group(2)}.x.x"
    if ":" in host:
        groups = [g for g in host.split(":") if g][:2]
        if groups:
            return ":".join(groups) + ":x:x:x:x"
        return "x:x:x:x:x:x:x:x"
    return host


def mask_addr(value):
    """Mask a "host:port" / "[ipv6]:port" address string as returned by
    Core's getpeerinfo or ElectrumX session info, preserving the port.
    """
    if not value:
        return value
    value = value.strip()
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return f"[{mask_ip(value[1:end])}]{value[end + 1:]}"
        return mask_ip(value)
    if value.count(":") == 1:
        host, _, port = value.partition(":")
        return f"{mask_ip(host)}:{port}"
    return mask_ip(value)


def apply_privacy(snapshot, enabled):
    """Mutates and returns `snapshot` in place - only ever called once,
    right before the poll loop publishes the new snapshot, so mutating in
    place is safe (nothing else holds a reference yet).
    """
    if not enabled:
        return snapshot

    for p in snapshot.get("peers") or []:
        if p.get("addr"):
            p["addr"] = mask_addr(p["addr"])

    ex = snapshot.get("electrumx")
    if ex:
        for s in ex.get("sessions") or []:
            if s.get("remote_address"):
                s["remote_address"] = mask_addr(s["remote_address"])

    for b in snapshot.get("banned_peers") or []:
        if b.get("address"):
            b["address"] = mask_addr(b["address"])

    return snapshot

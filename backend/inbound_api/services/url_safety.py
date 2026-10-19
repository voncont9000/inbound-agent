import ipaddress
import socket
from urllib.parse import urlsplit


def normalize_website_url(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return normalized
    if normalized.startswith("//"):
        return f"https:{normalized}"
    if "://" not in normalized:
        return f"https://{normalized}"
    return normalized


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def is_public_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or hostname == "localhost"
            or hostname.endswith(".localhost")
        ):
            return False
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        return False
    if not addresses:
        return False
    for address_info in addresses:
        try:
            address = ipaddress.ip_address(address_info[4][0])
        except ValueError:
            return False
        if not is_public_address(address):
            return False
    return True

import ipaddress
import socket
from urllib.parse import urlsplit


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
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if (
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            return False
    return True

"""Validation helpers for operator-configured outbound HTTP destinations.

The settings page can ask the backend to probe an arbitrary provider.  Keep the
network policy in one small module so every probe applies the same scheme,
credential, DNS and private-address checks.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit


class OutboundTargetError(ValueError):
    """Raised when an operator-supplied outbound target is unsafe or malformed."""


@dataclass(frozen=True)
class OutboundTarget:
    """A validated URL and its parsed components."""

    raw: str
    parsed: SplitResult


_HTTP_SCHEMES = {"http", "https"}
_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}


def _host_ips(hostname: str, port: int, *, resolver=None) -> tuple[ipaddress._BaseAddress, ...]:
    """Resolve all address records, retaining every address for rebinding checks."""
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return (literal,)

    resolve = resolver or socket.getaddrinfo
    try:
        records = resolve(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        raise OutboundTargetError("host could not be resolved") from exc
    addresses: list[ipaddress._BaseAddress] = []
    for record in records:
        sockaddr = record[4]
        if not sockaddr:
            continue
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError):
            continue
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise OutboundTargetError("host could not be resolved")
    return tuple(addresses)


def _validate_common(
    value: str,
    *,
    field: str,
    schemes: set[str],
    allow_credentials: bool,
    allow_private: bool,
    require_https_for_public: bool,
    resolver=None,
    resolve_host: bool = True,
) -> OutboundTarget:
    if not isinstance(value, str) or not value.strip():
        raise OutboundTargetError(f"{field} must be a non-empty URL")
    raw = value.strip()
    if any(ord(char) < 0x20 for char in raw):
        raise OutboundTargetError(f"{field} contains control characters")
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise OutboundTargetError(f"{field} is malformed") from exc

    if parsed.scheme.casefold() not in schemes or not hostname:
        allowed = ", ".join(sorted(schemes))
        raise OutboundTargetError(f"{field} must use one of: {allowed}")
    if parsed.fragment:
        raise OutboundTargetError(f"{field} must not contain a fragment")
    if field == "api_base" and parsed.query:
        raise OutboundTargetError("api_base must not contain query parameters")
    if parsed.username is not None or parsed.password is not None:
        if not allow_credentials:
            raise OutboundTargetError(f"{field} must not contain embedded credentials")
    if port is None:
        port = 443 if parsed.scheme.casefold() == "https" else 80
    if not (1 <= port <= 65535):
        raise OutboundTargetError(f"{field} has an invalid port")

    addresses = _host_ips(hostname, port, resolver=resolver) if resolve_host else ()
    # `is_global` excludes loopback, RFC1918, link-local, metadata, multicast,
    # unspecified, documentation and other non-routable ranges.  Check every
    # DNS answer so a single private record cannot bypass the policy.
    private = [address for address in addresses if not address.is_global]
    if private and not allow_private:
        raise OutboundTargetError(f"{field} resolves to a private or non-public address")
    if require_https_for_public and parsed.scheme.casefold() != "https":
        raise OutboundTargetError(f"{field} must use HTTPS")

    return OutboundTarget(raw=raw, parsed=parsed)


def validate_api_base(
    value: str,
    *,
    resolver=None,
    allow_private: bool = False,
    allow_http_loopback: bool = False,
    resolve_host: bool = True,
) -> OutboundTarget:
    """Validate a provider API base URL before a probe is attempted."""
    target = _validate_common(
        value,
        field="api_base",
        schemes=_HTTP_SCHEMES,
        allow_credentials=False,
        allow_private=allow_private,
        require_https_for_public=False,
        resolver=resolver,
        resolve_host=resolve_host,
    )
    if target.parsed.scheme.casefold() == "http":
        if resolve_host:
            host_ips = _host_ips(target.parsed.hostname or "", target.parsed.port or 80, resolver=resolver)
            loopback_only = all(address.is_loopback for address in host_ips)
            private_allowed = allow_private and any(not address.is_global for address in host_ips)
        else:
            hostname = (target.parsed.hostname or "").rstrip(".").casefold()
            try:
                literal = ipaddress.ip_address(hostname)
            except ValueError:
                literal = None
            loopback_only = hostname == "localhost" or bool(literal and literal.is_loopback)
            private_allowed = bool(allow_private and literal and not literal.is_global)
        if not (private_allowed or (allow_http_loopback and loopback_only)):
            raise OutboundTargetError("api_base must use HTTPS")
    return target


def validate_proxy(
    value: str,
    *,
    resolver=None,
    allow_private: bool = False,
    resolve_host: bool = True,
) -> OutboundTarget:
    """Validate an HTTP/SOCKS proxy URL, including its resolved host."""
    return _validate_common(
        value,
        field="proxy",
        schemes=_PROXY_SCHEMES,
        allow_credentials=True,
        allow_private=allow_private,
        require_https_for_public=False,
        resolver=resolver,
        resolve_host=resolve_host,
    )


def validate_probe_targets(
    api_base: str,
    proxy: str = "",
    *,
    resolver=None,
) -> tuple[OutboundTarget | None, OutboundTarget | None]:
    """Strict policy used by settings connection tests.

    Probes run with credentials supplied by an authenticated owner, so they are
    deliberately restricted to public HTTPS endpoints and public proxies.  A
    local provider can still be used by the runtime channel manager, but it is
    not reachable through this arbitrary URL testing endpoint.
    """
    base = None
    if api_base and api_base.strip():
        base = validate_api_base(api_base, resolver=resolver, allow_private=False)
    proxy_target = None
    if proxy and proxy.strip():
        proxy_target = validate_proxy(proxy, resolver=resolver, allow_private=False)
    return base, proxy_target


def redact_url_credentials(value: str) -> str:
    """Return a URL suitable for an error message without URL-embedded secrets.

    Provider and proxy URLs occasionally carry credentials in userinfo or
    query parameters in legacy configurations.  Neither form belongs in an
    API response or log line, so redact both while retaining the scheme/host
    useful for diagnosis.
    """
    try:
        parsed = urlsplit(value)
        has_userinfo = parsed.username is not None or parsed.password is not None
        if not has_userinfo and not parsed.query and not parsed.fragment:
            return value
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        netloc = f"<redacted>@{host}" if has_userinfo else host
        return parsed._replace(
            netloc=netloc,
            query="<redacted>" if parsed.query else "",
            fragment="<redacted>" if parsed.fragment else "",
        ).geturl()
    except (TypeError, ValueError):
        return "<redacted-url>"

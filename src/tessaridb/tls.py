"""TLS to a node (protocol §1.1).

A node given a certificate speaks TLS 1.3 on both ports and nothing else, and a
cluster node serves clients in the clear only when its operator chose to. So a
client is **configured** for one or the other: :func:`tls_context` says whom it
trusts, and every connection made with it — the first, every one a redirect
opens, and every HTTP request — checks the node's certificate chain against that
trust and its name against the host it dialled.

There is no way to skip either check. A context that does not verify is refused
rather than used: a client that accepts any certificate is talking to whoever
answered.
"""

from __future__ import annotations

import socket
import ssl

from .errors import TlsError


def tls_context(cafile: str | None = None) -> ssl.SSLContext:
    """Trust the certificates in the PEM file ``cafile``, or the platform's own
    store when it is ``None``. TLS 1.3 only, hostname checked."""
    context = ssl.create_default_context(cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    return context


def verified(context: ssl.SSLContext) -> ssl.SSLContext:
    """``context``, or a refusal when it would accept a node it did not check."""
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise ValueError(
            "this TLS context does not verify the node's certificate and name, "
            "and this client does not connect without both"
        )
    return context


def host_of(address: str) -> str:
    """The name the node's certificate must carry: the host part of ``address``,
    without its port or an IPv6 address's brackets."""
    host, _, _ = address.rpartition(":")
    return host.removeprefix("[").removesuffix("]")


def wrap(sock: socket.socket, context: ssl.SSLContext, address: str) -> ssl.SSLSocket:
    """Complete the handshake on ``sock`` for the node at ``address``.

    Every failure here is :class:`~tessaridb.errors.TlsError` and not
    :class:`~tessaridb.errors.IoError`: nothing about a second attempt at the
    same node would differ, so it is never retried.
    """
    try:
        return context.wrap_socket(sock, server_hostname=host_of(address))
    except (ssl.SSLError, ssl.CertificateError, OSError) as why:
        sock.close()
        raise TlsError(f"TLS with {address} failed: {why}") from why

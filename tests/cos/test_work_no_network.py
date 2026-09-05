"""The suite's own network guard, proved to bite.

A test that asserts "nothing reached the network" is worth only as much as
the mechanism that would have stopped it. This module makes that mechanism
fail visibly, so the rest of the suite's offline claim rests on something
that was checked rather than assumed.
"""

from __future__ import annotations

import socket
import ssl

import pytest


def test_guard_blocks_connections():
    """any attempt to open a socket inside this suite raises"""
    with pytest.raises(OSError):
        socket.socket()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 9))


def test_guard_blocks_tls():
    """wrapping a socket in TLS is refused too"""
    with pytest.raises(OSError):
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).wrap_socket(object())


def test_service_works_with_no_network(flow):
    """the whole flow runs with every socket refused"""
    work_id = flow.started()
    response = flow.write(work_id, "A draft written with no network at all.\n")
    assert response["ok"] is True

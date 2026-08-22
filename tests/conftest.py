import socket

import pytest


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    """
    prevent tests from opening external network connections
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """

    original_connect = socket.socket.connect

    def deny_connection(socket_instance, address):
        """
        reject an unexpected socket connection
        :param socket_instance: socket attempting the connection
        :param address: requested network address
        :returns: nothing
        """
        host = address[0] if isinstance(address, tuple) else address
        if host in {"127.0.0.1", "::1", "localhost"}:
            return original_connect(socket_instance, address)
        raise AssertionError(f"Tests must mock external network access to {address}")

    monkeypatch.setattr(socket.socket, "connect", deny_connection)

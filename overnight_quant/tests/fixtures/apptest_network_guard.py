"""Test-only AppTest IPC boundary; no blanket loopback network exception."""
from contextlib import contextmanager
from contextvars import ContextVar
import http.client
import socket
import subprocess
import sys
import urllib.request

import requests
from streamlit.testing.v1.local_script_runner import LocalScriptRunner


class AppTestNetworkGuard:
    def __init__(self, monkeypatch):
        self.business_attempts = {key: 0 for key in ("connection", "dns", "http", "legacy_action", "subprocess")}
        self.framework_pairs = 0
        self.framework_connections = 0
        self._initializing = ContextVar("apptest_framework_initializing", default=False)
        original_connect = socket.socket.connect
        original_pair = socket.socketpair
        pair_code = getattr(original_pair, "__code__", None)
        original_init = LocalScriptRunner.__init__

        def guarded_connect(client, address):
            caller = sys._getframe(1)
            listener = caller.f_locals.get("lsock")
            # Only the stdlib-created pair's exact client and private listener,
            # while AppTest initializes its runner, may make this connection.
            if (self._initializing.get() and pair_code is not None
                    and caller.f_code is pair_code
                    and caller.f_locals.get("csock") is client
                    and isinstance(listener, socket.socket)
                    and isinstance(address, tuple) and len(address) >= 2
                    and address[0] in ("127.0.0.1", "::1")
                    and address[:2] == listener.getsockname()[:2]):
                self.framework_connections += 1
                return original_connect(client, address)
            self.deny("connection")

        def guarded_pair(*args, **kwargs):
            if not self._initializing.get():
                self.deny("connection")
            pair = original_pair(*args, **kwargs)
            self.framework_pairs += 1
            return pair

        def guarded_init(runner, *args, **kwargs):
            with self.framework_initialization():
                original_init(runner, *args, **kwargs)

        def deny_dns(*args, **kwargs):
            self.deny("dns")

        def deny_connection(*args, **kwargs):
            self.deny("connection")

        def deny_http(*args, **kwargs):
            self.deny("http")

        def deny_subprocess(*args, **kwargs):
            self.deny("subprocess")

        monkeypatch.setattr(socket.socket, "connect", guarded_connect)
        monkeypatch.setattr(socket.socket, "connect_ex", deny_connection)
        monkeypatch.setattr(socket, "socketpair", guarded_pair)
        monkeypatch.setattr(socket, "getaddrinfo", deny_dns)
        monkeypatch.setattr(socket, "gethostbyname", deny_dns)
        monkeypatch.setattr(socket, "gethostbyname_ex", deny_dns)
        monkeypatch.setattr(socket, "gethostbyaddr", deny_dns)
        monkeypatch.setattr(http.client.HTTPConnection, "request", deny_http)
        monkeypatch.setattr(urllib.request, "urlopen", deny_http)
        monkeypatch.setattr(requests.sessions.Session, "request", deny_http)
        monkeypatch.setattr(subprocess, "run", deny_subprocess)
        monkeypatch.setattr(LocalScriptRunner, "__init__", guarded_init)

    @contextmanager
    def framework_initialization(self):
        token = self._initializing.set(True)
        try:
            yield
        finally:
            self._initializing.reset(token)

    def deny(self, category):
        self.business_attempts[category] += 1
        raise AssertionError(f"business_{category}_forbidden")

    def deny_legacy_action(self, *args, **kwargs):
        self.deny("legacy_action")

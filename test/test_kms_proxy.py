# Copyright 2026-present MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Prototype test: assert KMS requests are routed through an HTTP proxy via kms_connect_callback.

Requires:
  - A running test proxy (test-proxy.py) on 127.0.0.1:8080.
  - AWS temp credentials in CSFLE_AWS_TEMP_ACCESS_KEY_ID, etc.

Run via: kevinAlbs/test_pymongo.sh
"""
from __future__ import annotations

import json
import socket
import unittest
import urllib.request

from bson.codec_options import CodecOptions
from test import IntegrationTest, client_context
from test.helpers_shared import AWS_TEMP_CREDS

from pymongo.encryption_options import _HAVE_PYMONGOCRYPT
from pymongo.synchronous.encryption import ClientEncryption

_AWS_CMK_ARN = "arn:aws:kms:us-east-1:579766882180:key/89fcc2c4-08b0-4bd9-9f25-e30687b580d0"
_PROXY_HOST = "127.0.0.1"
_PROXY_PORT = 8080
OPTS = CodecOptions()


def _proxy_count():
    with urllib.request.urlopen(
        f"http://{_PROXY_HOST}:{_PROXY_PORT}/count", timeout=2
    ) as r:
        return json.loads(r.read())["count"]


def _connect_via_proxy(host: str, port: int) -> socket.socket:
    """Connect to host:port through the test HTTP CONNECT proxy."""
    sock = socket.create_connection((_PROXY_HOST, _PROXY_PORT))
    connect_request = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
    sock.sendall(connect_request.encode())
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("Proxy closed connection during CONNECT")
        response += chunk
    status_line = response.split(b"\r\n")[0].decode()
    if "200" not in status_line:
        raise RuntimeError(f"Proxy CONNECT failed: {status_line}")
    return sock


@unittest.skipUnless(_HAVE_PYMONGOCRYPT, "pymongocrypt is not installed")
class TestKmsProxy(IntegrationTest):
    """Assert KMS requests during create_data_key are routed through kms_connect_callback."""

    @client_context.require_version_min(4, 2, -1)
    @unittest.skipUnless(
        all(AWS_TEMP_CREDS.values()),
        "AWS temp credentials not set (CSFLE_AWS_TEMP_ACCESS_KEY_ID, "
        "CSFLE_AWS_TEMP_SECRET_ACCESS_KEY, CSFLE_AWS_TEMP_SESSION_TOKEN)",
    )
    def test_kms_requests_use_proxy(self):
        try:
            baseline = _proxy_count()
        except Exception as e:
            self.skipTest(f"Proxy not running on {_PROXY_HOST}:{_PROXY_PORT}: {e}")

        client_encryption = ClientEncryption(
            kms_providers={"aws": AWS_TEMP_CREDS},
            key_vault_namespace="keyvault.datakeys",
            key_vault_client=self.client,
            codec_options=OPTS,
            kms_connect_callback=_connect_via_proxy,
        )
        self.addCleanup(client_encryption.close)

        client_encryption.create_data_key(
            "aws",
            master_key={"region": "us-east-1", "key": _AWS_CMK_ARN},
        )

        count = _proxy_count()
        self.assertGreater(
            count,
            baseline,
            f"Proxy was not used: count={count}, baseline={baseline}. "
            "kms_connect_callback did not route requests through the proxy.",
        )

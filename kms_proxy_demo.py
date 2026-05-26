# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "pymongo[encryption] @ git+https://github.com/kevinAlbs/mongo-python-driver@callback.D2920",
# ]
# ///
"""
Demo: route CSFLE KMS requests through an HTTP proxy using kms_connect_callback.

This uses a prototype branch of the Python driver that adds kms_connect_callback
to ClientEncryption and AutoEncryptionOpts.

Prerequisites:
  - uv (https://docs.astral.sh/uv/getting-started/installation/)
  - A running MongoDB instance
  - Azure credentials with permission to use the specified Key Vault key

Usage:
  uv run kms_proxy_demo.py

Required environment variables:
  MONGODB_URI                         MongoDB connection URI
  PROXY_HOST                          Proxy hostname
  PROXY_PORT                          Proxy port
  CSFLE_AZURE_TENANTID                Azure tenant ID
  CSFLE_AZURE_CLIENTID                Azure client ID
  CSFLE_AZURE_CLIENTSECRET            Azure client secret
  CSFLE_AZURE_KEY_VAULT_ENDPOINT      Key Vault endpoint, e.g. my-vault.vault.azure.net
  CSFLE_AZURE_KEY_NAME                Name of the key in Key Vault
  

"""

import os
import socket
import base64

from bson.codec_options import CodecOptions
from pymongo import MongoClient
from pymongo.synchronous.encryption import ClientEncryption

# --- Configuration -----------------------------------------------------------

MONGODB_URI = os.environ["MONGODB_URI"]
PROXY_HOST = os.environ["PROXY_HOST"]
PROXY_PORT = int(os.environ["PROXY_PORT"])

azure_creds = {
    "tenantId": os.environ["CSFLE_AZURE_TENANTID"],
    "clientId": os.environ["CSFLE_AZURE_CLIENTID"],
    "clientSecret": os.environ["CSFLE_AZURE_CLIENTSECRET"],
}

master_key = {
    "keyVaultEndpoint": os.environ["CSFLE_AZURE_KEY_VAULT_ENDPOINT"],
    "keyName": os.environ["CSFLE_AZURE_KEY_NAME"],
}

# --- Proxy callback -----------------------------------------------------------

def connect_via_proxy(host: str, port: int) -> socket.socket:
    """Open a tunnel to host:port through an HTTP CONNECT proxy."""
    sock = socket.create_connection((PROXY_HOST, PROXY_PORT))
    connect_request = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
    sock.sendall(connect_request.encode())
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("Proxy closed connection during CONNECT handshake")
        response += chunk
    status_line = response.split(b"\r\n")[0].decode()
    if "200" not in status_line:
        raise RuntimeError(f"Proxy CONNECT failed: {status_line}")
    return sock  # plain socket; driver wraps it with TLS

# --- Main --------------------------------------------------------------------

client = MongoClient(MONGODB_URI)
client_encryption = ClientEncryption(
    kms_providers={"azure": azure_creds},
    key_vault_namespace="keyvault.datakeys",
    key_vault_client=client,
    codec_options=CodecOptions(),
    kms_connect_callback=connect_via_proxy,
)

print(f"Creating data key via proxy {PROXY_HOST}:{PROXY_PORT} ...")
key_id = client_encryption.create_data_key("azure", master_key=master_key)
print("OK — created data key with _id: {}".format(base64.b64encode(key_id).decode()))

client_encryption.close()
client.close()

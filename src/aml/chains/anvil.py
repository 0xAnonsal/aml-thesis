"""Anvil subprocess manager.

Anvil is Foundry's local Ethereum test chain. We launch a fresh instance per
experiment so state is reproducible — no leakage between runs.

Usage:
    from aml.chains import AnvilNode

    with AnvilNode() as node:
        # node.rpc_url is now live, default funded accounts ready
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        ...
    # Anvil terminates on context exit
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
from contextlib import closing
from dataclasses import dataclass

import requests

ANVIL_BIN = "anvil"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_CHAIN_ID = 31337
STARTUP_TIMEOUT_SECONDS = 15.0
RPC_RETRY_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True)
class AnvilAccount:
    address: str
    private_key: str


# Anvil's deterministic default accounts (mnemonic:
# "test test test test test test test test test test test junk"). Each is
# pre-funded with 10000 ETH. Documented in the Foundry book.
_DEFAULT_ACCOUNTS: tuple[AnvilAccount, ...] = (
    AnvilAccount(
        "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    ),
    AnvilAccount(
        "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    ),
    AnvilAccount(
        "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    ),
    AnvilAccount(
        "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    ),
    AnvilAccount(
        "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
        "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
    ),
    AnvilAccount(
        "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc",
        "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba",
    ),
    AnvilAccount(
        "0x976EA74026E726554dB657fA54763abd0C3a0aa9",
        "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
    ),
    AnvilAccount(
        "0x14dC79964da2C08b23698B3D3cc7Ca32193d9955",
        "0x4bbbf85ce3377467afe5d46f804f221813b2bb87f24d81f60f1fcdbf7cbf4356",
    ),
    AnvilAccount(
        "0x23618e81E3f5cdF7f54C3d65f7FBc0aBf5B21E8f",
        "0xdbda1821b80551c9d65939329250298aa3472ba22feea921c0cf5d620ea67b97",
    ),
    AnvilAccount(
        "0xa0Ee7A142d267C1f36714E4a8F75612F20a79720",
        "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6",
    ),
)


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind((DEFAULT_HOST, 0))
        return sock.getsockname()[1]


class AnvilNode:
    """Subprocess wrapper for a local Anvil instance.

    Args:
        host: bind address. Defaults to 127.0.0.1.
        port: RPC port. If None, picks a free port automatically.
        chain_id: chain id reported via eth_chainId. Defaults to 31337.
        block_time: seconds between auto-mined blocks. 0 = instamine on tx.

    Use as a context manager — the node terminates on exit.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        chain_id: int = DEFAULT_CHAIN_ID,
        block_time: int = 0,
    ):
        if shutil.which(ANVIL_BIN) is None:
            raise FileNotFoundError(
                f"{ANVIL_BIN!r} not found on PATH. "
                f"Install Foundry: curl -L https://foundry.paradigm.xyz | bash && foundryup"
            )
        self.host = host
        self.port = port or _find_free_port()
        self.chain_id = chain_id
        self.block_time = block_time
        self._proc: subprocess.Popen | None = None

    @property
    def rpc_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def accounts(self) -> list[str]:
        return [a.address for a in _DEFAULT_ACCOUNTS]

    @property
    def private_keys(self) -> list[str]:
        return [a.private_key for a in _DEFAULT_ACCOUNTS]

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("AnvilNode already started")
        cmd = [
            ANVIL_BIN,
            "--host", self.host,
            "--port", str(self.port),
            "--chain-id", str(self.chain_id),
        ]
        if self.block_time > 0:
            cmd.extend(["--block-time", str(self.block_time)])
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            self._wait_for_rpc()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._proc = None

    def _wait_for_rpc(self) -> None:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            assert self._proc is not None
            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read().decode() if self._proc.stderr else ""
                raise RuntimeError(f"anvil exited during startup:\n{stderr}")
            try:
                resp = requests.post(
                    self.rpc_url,
                    json={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1},
                    timeout=1.0,
                )
                if resp.ok and "result" in resp.json():
                    return
            except requests.exceptions.RequestException:
                pass
            time.sleep(RPC_RETRY_INTERVAL_SECONDS)
        raise TimeoutError(
            f"anvil did not respond on {self.rpc_url} within {STARTUP_TIMEOUT_SECONDS}s"
        )

    def __enter__(self) -> "AnvilNode":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

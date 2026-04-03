from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx

PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "all_proxy",
    "no_proxy",
    "NO_PROXY",
)
LOOPBACK_NO_PROXY_ENTRIES = ("localhost", "127.0.0.1", "::1")
LOOPBACK_PROXY_BYPASS_MOUNTS = {
    "all://localhost": None,
    "all://127.0.0.1": None,
    "all://[::1]": None,
}
ALLOWED_NETWORK_MODES = {"auto", "direct", "custom"}
ALLOWED_PROXY_SCHEMES = {"http", "https", "socks5"}
NETWORK_MODE_ERROR = "网络模式无效。"
PROXY_URL_REQUIRED_ERROR = "高级网络模式下必须填写代理地址。"
PROXY_URL_ERROR = (
    "代理地址格式无效，请使用 http://127.0.0.1:7890 或 socks5://127.0.0.1:7890。"
)
SOCKS_PROXY_SUPPORT_ERROR = (
    "当前构建未包含 SOCKS5 代理支持，请安装 socksio，或改用 http:// / https:// 代理。"
)
NETWORK_GUIDANCE = "如果你开了代理工具，可到 设置 > 网络 尝试切换为“直连”。"


@dataclass(frozen=True)
class NetworkConfig:
    mode: str = "auto"
    proxy_url: str = ""


def normalize_network_mode(network_mode: str | None) -> str:
    candidate = str(network_mode or "auto").strip().lower()
    if candidate not in ALLOWED_NETWORK_MODES:
        raise ValueError(NETWORK_MODE_ERROR)
    return candidate


def validate_proxy_url(proxy_url: str | None) -> str:
    candidate = str(proxy_url or "").strip()
    if not candidate:
        raise ValueError(PROXY_URL_REQUIRED_ERROR)

    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    if (
        scheme not in ALLOWED_PROXY_SCHEMES
        or not parsed.hostname
        or parsed.port is None
    ):
        raise ValueError(PROXY_URL_ERROR)
    if scheme == "socks5" and not supports_socks_proxy():
        raise ValueError(SOCKS_PROXY_SUPPORT_ERROR)
    return candidate


def normalize_network_config(
    network_mode: str | None,
    proxy_url: str | None,
) -> NetworkConfig:
    mode = normalize_network_mode(network_mode)
    normalized_proxy_url = ""
    if mode == "custom":
        normalized_proxy_url = validate_proxy_url(proxy_url)
    return NetworkConfig(mode=mode, proxy_url=normalized_proxy_url)


def supports_socks_proxy() -> bool:
    return importlib.util.find_spec("socksio") is not None


def httpx_client_kwargs(network_config: NetworkConfig | None) -> dict[str, Any]:
    config = network_config or NetworkConfig()
    if config.mode == "direct":
        return {"trust_env": False}
    if config.mode == "custom":
        validate_proxy_url(config.proxy_url)
        return {
            "trust_env": False,
            "proxy": config.proxy_url,
            "mounts": LOOPBACK_PROXY_BYPASS_MOUNTS.copy(),
        }
    return {"trust_env": True}


def should_bypass_proxy(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def build_subprocess_env(
    network_config: NetworkConfig | None,
    base_env: dict[str, str] | None = None,
) -> dict[str, str] | None:
    config = network_config or NetworkConfig()
    if config.mode == "auto":
        return None

    env = dict(base_env or os.environ)
    original_no_proxy = env.get("NO_PROXY") or env.get("no_proxy") or ""
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)

    if config.mode == "custom":
        env["http_proxy"] = config.proxy_url
        env["https_proxy"] = config.proxy_url
        env["HTTP_PROXY"] = config.proxy_url
        env["HTTPS_PROXY"] = config.proxy_url
        env["ALL_PROXY"] = config.proxy_url
        env["all_proxy"] = config.proxy_url
        no_proxy_entries = [
            entry.strip()
            for entry in original_no_proxy.split(",")
            if entry.strip()
        ]
        for entry in LOOPBACK_NO_PROXY_ENTRIES:
            if entry not in no_proxy_entries:
                no_proxy_entries.append(entry)
        env["NO_PROXY"] = ",".join(no_proxy_entries)
        env["no_proxy"] = env["NO_PROXY"]

    return env


def is_network_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.ProxyError,
            httpx.NetworkError,
        ),
    ):
        return True

    message = str(exc).lower()
    signals = (
        "proxy",
        "all connection attempts failed",
        "timed out",
        "timeout",
        "connection reset",
        "connection refused",
        "connecterror",
        "network is unreachable",
        "temporary failure in name resolution",
        "name or service not known",
        "nodename nor servname provided",
    )
    return any(signal in message for signal in signals)


def add_network_guidance(message: str, category: str | None = None) -> str:
    if category != "network" and not is_network_error(RuntimeError(message)):
        return message
    if NETWORK_GUIDANCE in message:
        return message
    return f"{message} {NETWORK_GUIDANCE}"

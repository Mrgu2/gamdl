from __future__ import annotations

import logging
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger("gamdl.app.wrapper")

WRAPPER_NAME_RE = re.compile(r"^wrapper-latest-(\d+)$")
DEFAULT_WRAPPER_HOST = "127.0.0.1"
DEFAULT_WRAPPER_PORT = 10022


@dataclass(frozen=True)
class WrapperCandidate:
    name: str
    port: int


@dataclass(frozen=True)
class WrapperStatus:
    available: bool
    mode: Literal["none", "docker", "external-wrapper"]
    resolved_ip: str | None = None
    message: str | None = None


def default_wrapper_decrypt_ip() -> str:
    return f"{DEFAULT_WRAPPER_HOST}:{DEFAULT_WRAPPER_PORT}"


def parse_wrapper_decrypt_ip(wrapper_decrypt_ip: str) -> tuple[str, int]:
    candidate = wrapper_decrypt_ip.strip()
    host, _sep, port = candidate.rpartition(":")
    if not host:
        host = DEFAULT_WRAPPER_HOST
    else:
        host = host.strip() or DEFAULT_WRAPPER_HOST

    if any(character.isspace() for character in host):
        raise ValueError("Wrapper host contains whitespace.")

    try:
        parsed_port = int(port or str(DEFAULT_WRAPPER_PORT))
    except ValueError as exc:
        raise ValueError("Wrapper decrypt port must be an integer.") from exc
    if not 1 <= parsed_port <= 65535:
        raise ValueError("Wrapper decrypt port must be between 1 and 65535.")
    return host, parsed_port


def normalize_wrapper_decrypt_ip(wrapper_decrypt_ip: str) -> str:
    host, port = parse_wrapper_decrypt_ip(wrapper_decrypt_ip)
    return f"{host}:{port}"


def prioritize_wrapper_candidates(
    container_names: list[str],
    preferred_port: int,
) -> list[WrapperCandidate]:
    parsed: list[WrapperCandidate] = []
    for name in container_names:
        match = WRAPPER_NAME_RE.match(name.strip())
        if match:
            parsed.append(WrapperCandidate(name=name.strip(), port=int(match.group(1))))

    exact = [candidate for candidate in parsed if candidate.port == preferred_port]
    if exact:
        rest = [candidate for candidate in parsed if candidate.port != preferred_port]
        return exact + rest

    if len(parsed) == 1:
        return parsed

    return []


class WrapperManager:
    def __init__(self, docker_bin: str = "docker", wait_timeout: float = 8.0) -> None:
        self.docker_bin = docker_bin
        self.wait_timeout = wait_timeout

    @staticmethod
    def _parse_host_port(wrapper_decrypt_ip: str) -> tuple[str, int]:
        return parse_wrapper_decrypt_ip(wrapper_decrypt_ip)

    @staticmethod
    def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(timeout)
                return probe.connect_ex((host, port)) == 0
        except OSError:
            return False

    def _ports_ready(self, host: str, decrypt_port: int) -> bool:
        return self._is_port_open(host, decrypt_port) and self._is_port_open(
            host,
            decrypt_port + 10000,
        )

    def probe_status(self, wrapper_decrypt_ip: str) -> WrapperStatus:
        host, preferred_port = self._parse_host_port(wrapper_decrypt_ip)
        if self._ports_ready(host, preferred_port):
            return WrapperStatus(
                available=True,
                mode="external-wrapper",
                resolved_ip=f"{host}:{preferred_port}",
                message="已检测到可用的外部 wrapper。",
            )

        if not self._docker_available():
            return WrapperStatus(
                available=False,
                mode="none",
                message="未检测到外部 wrapper；正式分发版默认使用 AAC，ALAC 仅在外部 wrapper 可用时开放。",
            )

        candidates = prioritize_wrapper_candidates(
            self._list_wrapper_containers(),
            preferred_port,
        )
        for candidate in candidates:
            resolved_ip = f"{host}:{candidate.port}"
            if self._ports_ready(host, candidate.port):
                return WrapperStatus(
                    available=True,
                    mode="docker",
                    resolved_ip=resolved_ip,
                    message="已检测到通过 Docker 运行的外部 wrapper。",
                )

        if candidates:
            return WrapperStatus(
                available=False,
                mode="docker",
                resolved_ip=f"{host}:{candidates[0].port}",
                message="检测到可用的 Docker wrapper 容器，但当前未运行。",
            )

        return WrapperStatus(
            available=False,
            mode="none",
            message="未检测到外部 wrapper；正式分发版默认使用 AAC，ALAC 仅在外部 wrapper 可用时开放。",
        )

    def _docker_available(self) -> bool:
        try:
            result = subprocess.run(
                [self.docker_bin, "version", "--format", "{{.Server.Version}}"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return False
        return result.returncode == 0

    def _list_wrapper_containers(self) -> list[str]:
        result = subprocess.run(
            [self.docker_bin, "ps", "-a", "--format", "{{.Names}}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _start_container(self, name: str) -> bool:
        logger.info("Trying to start wrapper container %s", name)
        result = subprocess.run(
            [self.docker_bin, "start", name],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def ensure_running(self, wrapper_decrypt_ip: str) -> str:
        host, preferred_port = self._parse_host_port(wrapper_decrypt_ip)
        if self._ports_ready(host, preferred_port):
            return f"{host}:{preferred_port}"

        if not self._docker_available():
            raise RuntimeError(
                "未检测到可用的 Docker，无法自动启动 wrapper。"
            )

        candidates = prioritize_wrapper_candidates(
            self._list_wrapper_containers(),
            preferred_port,
        )
        if not candidates:
            raise RuntimeError(
                "没有找到可自动启动的 wrapper 容器。"
                " 请先创建并运行形如 wrapper-latest-10022 的容器。"
            )

        last_error: str | None = None
        for candidate in candidates:
            resolved_ip = f"{host}:{candidate.port}"
            if self._ports_ready(host, candidate.port):
                logger.info("Detected running wrapper at %s", resolved_ip)
                return resolved_ip

            if not self._start_container(candidate.name):
                last_error = f"docker start {candidate.name} failed"
                continue

            deadline = time.time() + self.wait_timeout
            while time.time() < deadline:
                if self._ports_ready(host, candidate.port):
                    logger.info("Wrapper became ready at %s", resolved_ip)
                    return resolved_ip
                time.sleep(0.25)

            last_error = f"{candidate.name} did not expose ports in time"

        raise RuntimeError(
            "自动启动 wrapper 失败。"
            + (f" 最后错误：{last_error}" if last_error else "")
        )

"""FastFlowLM process and socket management helpers (Linux)."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import config
from subprocess_util import run_captured

log = logging.getLogger("flowkey.flmserver")

# FastFlowLM upstream release feed.
FLM_RELEASES_API = "https://api.github.com/repos/FastFlowLM/FastFlowLM/releases/latest"
FLM_RELEASES_PAGE = "https://github.com/FastFlowLM/FastFlowLM/releases/"


@dataclass(frozen=True)
class FlmServerSettings:
    base_url: str
    model: str
    timeout_seconds: int
    power_mode: str
    startup_timeout_seconds: int
    extra_args: list[str]
    log_to_file: bool
    log_file: str
    pid_path: Path
    logs_dir: Path


def flm_host_port(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 52625
    return host, port


def is_flm_server_reachable(base_url: str) -> bool:
    host, port = flm_host_port(base_url)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.6)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def read_pid(pid_path: Path) -> int:
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def write_pid(pid_path: Path, pid: int) -> None:
    try:
        pid_path.write_text(str(pid), encoding="utf-8")
    except OSError as exc:
        log.warning("could not write FLM pid file %s: %s", pid_path, exc)


def remove_pid(pid_path: Path) -> None:
    try:
        pid_path.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("could not remove FLM pid file %s: %s", pid_path, exc)


def is_pid_alive(pid: int) -> bool:
    """Check if a PID exists by sending signal 0.

    No signal is actually sent — this only checks whether the process
    exists and we have permission to signal it.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_pid(pid: int) -> bool:
    """Send SIGTERM to a process. Returns True if the signal was sent."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        # Process may already be gone — that's fine.
        return False


def _pids_via_proc_net(port: int) -> list[int]:
    """Find PIDs listening on *port* by reading /proc/net/tcp + /proc/[pid]/fd.

    No root required — reads world-readable /proc entries.
    """
    hex_port = f":{port:04x}"
    # Collect socket inode numbers whose local address matches the port.
    sock_inodes: set[int] = set()
    for net_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            text = Path(net_file).read_text(encoding="ascii", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            # Format: sl  local_address  rem_address  st  ...
            # local_address is HEX_IP:HEX_PORT
            parts = line.strip().split()
            if len(parts) < 10:
                continue
            local = parts[1]  # e.g. "00000000:1F90"
            if local.endswith(hex_port):
                try:
                    sock_inodes.add(int(parts[9]))
                except (IndexError, ValueError):
                    continue

    if not sock_inodes:
        return []

    # Scan /proc/[pid]/fd for matching socket inodes.
    pids: set[int] = set()
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(proc.name)
        except ValueError:
            continue
        fd_dir = proc / "fd"
        try:
            for fd_entry in fd_dir.iterdir():
                try:
                    link = os.readlink(str(fd_entry))
                except OSError:
                    continue
                # Socket links look like "socket:[12345]"
                if link.startswith("socket:["):
                    try:
                        ino = int(link[8:-1])
                    except ValueError:
                        continue
                    if ino in sock_inodes:
                        pids.add(pid)
        except PermissionError:
            continue
    return sorted(pids)


def _pids_via_ss(port: int) -> list[int]:
    """Find PIDs via `ss -tlnp`. Requires CAP_NET_ADMIN for PID display."""
    try:
        result = run_captured(["ss", "-tlnp"], timeout=5)
    except FileNotFoundError:
        return []
    except Exception as exc:
        log.debug("ss failed: %s", exc)
        return []

    pids: set[int] = set()
    needle = f":{port}"
    for line in (result.stdout or "").splitlines():
        if needle not in line:
            continue
        for m in re.finditer(r"pid=(\d+)", line):
            try:
                pids.add(int(m.group(1)))
            except ValueError:
                continue
    return sorted(pids)


def find_pids_on_port(port: int) -> list[int]:
    """Find PIDs listening on a TCP port.

    Tries /proc/net/tcp first (no root, stable ABI), then falls back to
    ``ss -tlnp``. Returns sorted unique PIDs, or [] on error.
    """
    pids = _pids_via_proc_net(port)
    if not pids:
        pids = _pids_via_ss(port)
    return pids


def warmup_request(
    model: str,
    timeout_seconds: int,
    call_api: Callable[[str, str, str, int, int], tuple[str, str]],
) -> None:
    call_api(model, "ping", "warmup ping", 8, max(2, timeout_seconds))


def start_flm_server(
    settings: FlmServerSettings,
    call_api: Callable[[str, str, str, int, int], tuple[str, str]],
    *,
    force_restart: bool = False,
    stop_callback: Callable[[bool], bool] | None = None,
) -> str:
    if force_restart and stop_callback is not None:
        stop_callback(True)
    elif is_flm_server_reachable(settings.base_url):
        return "already_running"

    host, port = flm_host_port(settings.base_url)
    try:
        pmode = config.PowerMode(settings.power_mode.strip().lower()).value
    except ValueError:
        pmode = config.PowerMode.BALANCED.value
    args = [
        "flm",
        "serve",
        settings.model,
        "--pmode",
        pmode,
        "--host",
        host or "127.0.0.1",
        "--port",
        str(port or 52625),
    ]
    args.extend(settings.extra_args)

    stdout_target = None
    stderr_target = None
    log_handle = None
    if settings.log_to_file:
        log_path = settings.logs_dir / settings.log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        log_handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] starting: {' '.join(args)}\n")
        log_handle.flush()
        stdout_target = log_handle
        stderr_target = log_handle

    try:
        proc = subprocess.Popen(args, stdout=stdout_target, stderr=stderr_target)
    finally:
        if log_handle is not None:
            log_handle.close()
    write_pid(settings.pid_path, proc.pid)

    deadline = time.time() + max(5, settings.startup_timeout_seconds)
    while time.time() < deadline:
        if is_flm_server_reachable(settings.base_url):
            return "started"
        if proc.poll() is not None:
            remove_pid(settings.pid_path)
            raise RuntimeError(f"FastFlowLM server exited early (exit {proc.returncode}).")
        time.sleep(0.25)
    kill_pid(proc.pid)
    remove_pid(settings.pid_path)
    raise RuntimeError("FastFlowLM server did not start in time.")


def stop_flm_server(settings: FlmServerSettings, *, force: bool = False) -> bool:
    pid = read_pid(settings.pid_path)
    killed = False
    if pid > 0 and is_pid_alive(pid):
        killed = kill_pid(pid)
    host, port = flm_host_port(settings.base_url)
    for port_pid in find_pids_on_port(port):
        if kill_pid(port_pid):
            killed = True
    if force and not killed and host in {"127.0.0.1", "localhost"}:
        log.info("force stop requested, but no Flowkey-owned FLM pid was found")
    if killed:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not is_flm_server_reachable(settings.base_url):
                break
            time.sleep(0.25)
    remove_pid(settings.pid_path)
    return killed


def _parse_flm_json(text: str) -> dict | None:
    """Parse `flm list --json`, tolerating any non-JSON preamble/trailer."""
    text = text or ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
    return None


_NON_CHAT_LABELS = frozenset({"embeddings"})
_NON_CHAT_ONLY_LABEL_SETS = (frozenset({"audio", "realtime-transcription", "transcription"}),)
_NON_CHAT_FAMILIES = frozenset({"whisper-v3"})
_NON_CHAT_NAME_PREFIXES = ("embed-", "whisper-")


def _is_selectable_chat_model(name: str, entry: dict | None = None) -> bool:
    """Decide whether a model is chat-selectable (LLM, not embedding/ASR/etc.).

    Primary signal: per-model metadata from `flm list --json` (the `label`
    array and `details.family`). Fallback: name-prefix match for catalogs
    that don't surface the metadata.
    """
    if not name:
        return False
    if isinstance(entry, dict):
        family = str(((entry.get("details") or {})).get("family") or "").strip().lower()
        labels = {str(lbl).strip().lower() for lbl in (entry.get("label") or [])}
        if family and family in _NON_CHAT_FAMILIES:
            return False
        if labels and (labels & _NON_CHAT_LABELS):
            return False
        if labels and labels.issubset(_NON_CHAT_ONLY_LABEL_SETS[0]):
            return False
        if labels or family:
            return True
    prefix = name.split(":", 1)[0].strip().lower()
    return bool(prefix) and not prefix.startswith(_NON_CHAT_NAME_PREFIXES)


def flm_list(filter_kind: str, model: str) -> dict:
    """Return model names via `flm list --json`, filtered by install state.

    Embedding and ASR-only models (e.g. `embed-gemma:300m`, `whisper-v3:*`)
    are excluded by `_is_selectable_chat_model` — they are reserved for
    side-loading and break `flm serve` when used as the main model.
    """
    if filter_kind not in {"installed", "not-installed", "all"}:
        return {"error": f"bad filter: {filter_kind}", "models": [], "active": model}
    try:
        result = run_captured(
            ["flm", "list", "--json"],
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {"error": "flm CLI not found in PATH", "models": [], "active": model}
    if result.returncode != 0:
        return {
            "error": (result.stderr or result.stdout or "").strip() or "flm list failed",
            "models": [],
            "active": model,
        }
    payload = _parse_flm_json(result.stdout)
    if payload is None:
        return {"error": "could not parse 'flm list --json' output", "models": [], "active": model}

    names: list[str] = []
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("model") or entry.get("name") or "").strip()
        if not name:
            continue
        if not _is_selectable_chat_model(name, entry):
            continue
        is_installed = bool(entry.get("installed"))
        if filter_kind == "installed" and not is_installed:
            continue
        if filter_kind == "not-installed" and is_installed:
            continue
        names.append(name)
    return {"models": names, "active": model}


def flm_version() -> str:
    """Return the installed flm version string (e.g. '0.9.43'), or '' if unknown."""
    try:
        result = run_captured(
            ["flm", "version", "--json"],
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return ""
    payload = _parse_flm_json(result.stdout)
    if isinstance(payload, dict):
        version = str(payload.get("version") or "").strip()
        if version:
            return version
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", (result.stdout or "") + (result.stderr or ""))
    return match.group(1) if match else ""


def check_flm_update(
    cache_path: Path | None = None,
    *,
    ttl_seconds: int = 86400,
    force: bool = False,
    cache_only: bool = False,
) -> dict:
    """Compare the installed flm version against the latest GitHub release."""
    from updater import version_tuple

    local = flm_version()
    out: dict = {"current": local, "name": "FastFlowLM", "release_url": FLM_RELEASES_PAGE}

    cached: dict | None = None
    if cache_path is not None and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cached = loaded
        except (OSError, ValueError) as exc:
            log.debug("FLM update cache unreadable (%s): %s", cache_path, exc)
            cached = None

    now = time.time()
    fresh = bool(cached) and (now - float(cached.get("checked_at") or 0)) < ttl_seconds
    if cached and not force and (fresh or cache_only):
        latest = str(cached.get("latest") or "")
        out["latest"] = latest
        out["asset_url"] = str(cached.get("asset_url") or "")
        out["release_url"] = str(cached.get("release_url") or FLM_RELEASES_PAGE)
        out["checked_at"] = float(cached.get("checked_at") or 0)
        out["cached"] = True
        out["stale"] = not fresh
        out["has_update"] = bool(local and latest) and version_tuple(latest) > version_tuple(local)
        return out

    if cache_only:
        out["latest"] = ""
        out["has_update"] = False
        out["cached"] = False
        out["stale"] = True
        return out

    try:
        req = urllib.request.Request(
            FLM_RELEASES_API,
            headers={"User-Agent": "Flowkey", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        log.debug("FLM update check failed, serving local/cache: %s", exc)
        out["error"] = str(exc)
        out["has_update"] = False
        out["cached"] = False
        if cached:
            out["latest"] = str(cached.get("latest") or "")
            out["asset_url"] = str(cached.get("asset_url") or "")
            out["release_url"] = str(cached.get("release_url") or FLM_RELEASES_PAGE)
        return out

    tag = str(payload.get("tag_name") or "").strip()
    latest = tag.lstrip("vV")
    release_url = str(payload.get("html_url") or FLM_RELEASES_PAGE)
    # Look for any downloadable asset (not Windows .exe specific)
    asset_url = ""
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        url = str(asset.get("browser_download_url") or "")
        if url:
            asset_url = url
            break

    out["latest"] = latest
    out["release_url"] = release_url
    out["asset_url"] = asset_url
    out["checked_at"] = now
    out["cached"] = False
    out["has_update"] = bool(local and latest) and version_tuple(latest) > version_tuple(local)

    if cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "checked_at": now,
                        "latest": latest,
                        "release_url": release_url,
                        "asset_url": asset_url,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log.debug("could not persist FLM update cache (%s): %s", cache_path, exc)
    return out

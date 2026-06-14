from __future__ import annotations

import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest


def _write_fake_release_archive(path: Path) -> None:
    script = """#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


def _xdg_root() -> Path:
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "Flowkey"
    return Path.home() / ".local" / "share" / "Flowkey"


def _ensure_runtime_dirs() -> Path:
    root = _xdg_root()
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print("usage: flowkey <command> [args...]")
        return 0

    root = _ensure_runtime_dirs()
    cmd = args[0]
    if cmd == "install":
        (root / "config.json").write_text('{"installed": true}', encoding="utf-8")
        (root / "data" / "install.seen").write_text("1", encoding="utf-8")
        print("installed")
        return 0
    if cmd in {"daemon", "tui", "listen"}:
        (root / "data" / f"{cmd}.seen").write_text("1", encoding="utf-8")
        print(cmd)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
"""

    with tarfile.open(path, "w:gz") as tf:
        data = script.encode("utf-8")
        info = tarfile.TarInfo("flowkey/flowkey")
        info.size = len(data)
        info.mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
        tf.addfile(info, io.BytesIO(data))


def test_binary_install_smoke(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    xdg_data = home / ".local" / "share"
    bin_dir = home / ".local" / "bin"
    install_root = home / ".local" / "opt" / "flowkey" / "current"
    archive = tmp_path / "flowkey-linux-x86_64.tar.gz"

    home.mkdir()
    _write_fake_release_archive(archive)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(xdg_data),
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "FLOWKEY_RELEASE_TARBALL": str(archive),
            "FLOWKEY_SKIP_SYSTEM_SETUP": "1",
            "FLOWKEY_INSTALL_ROOT": str(install_root),
            "FLOWKEY_BIN_DIR": str(bin_dir),
            "FLOWKEY_BIN_PATH": str(bin_dir / "flowkey"),
        }
    )

    subprocess.run(["bash", "install.sh"], cwd=repo, env=env, check=True)

    flowkey_bin = bin_dir / "flowkey"
    assert flowkey_bin.exists()
    assert flowkey_bin.is_symlink()
    assert flowkey_bin.resolve() == install_root / "flowkey"

    subprocess.run(["flowkey", "--help"], cwd=repo, env=env, check=True)
    subprocess.run(["flowkey", "daemon"], cwd=repo, env=env, check=True)
    subprocess.run(["flowkey", "tui"], cwd=repo, env=env, check=True)
    subprocess.run(["flowkey", "listen"], cwd=repo, env=env, check=True)

    xdg_root = xdg_data / "Flowkey"
    assert (xdg_root / "config.json").exists()
    assert (xdg_root / "data" / "install.seen").exists()
    assert (xdg_root / "data" / "daemon.seen").exists()
    assert (xdg_root / "data" / "tui.seen").exists()
    assert (xdg_root / "data" / "listen.seen").exists()


def test_ensure_config_copies_seed_without_fallback(fresh_modules, tmp_path: Path):
    install = fresh_modules("install")
    seed = tmp_path / "config.seed.json"
    live = tmp_path / "config.json"
    payload = '{"theme":"textual-dark"}\n'
    seed.write_text(payload, encoding="utf-8")
    install.CONFIG_SEED = seed
    install.CONFIG_LIVE = live

    install._ensure_config()

    assert live.read_text(encoding="utf-8") == payload


def test_ensure_config_fails_when_seed_missing(fresh_modules, tmp_path: Path):
    install = fresh_modules("install")
    live = tmp_path / "config.json"
    install.CONFIG_SEED = tmp_path / "missing.seed.json"
    install.CONFIG_LIVE = live

    with pytest.raises(FileNotFoundError):
        install._ensure_config()

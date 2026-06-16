from __future__ import annotations

import logging

import paths
from config import PowerMode
from textual.app import ComposeResult
from textual.containers import VerticalScroll

import engine
import flm_server
from tui.dashboard._pane import Pane
from tui.dashboard.config_pane.chat_panel import ChatPanel
from tui.dashboard.config_pane.chat_settings import FlmServerPanel
from tui.dashboard.config_pane.flm import FlmModelPanel

log = logging.getLogger("ffchat.tui.dashboard")

_FLM_UPDATE_CACHE = paths.DATA_DIR / "flm_update_cache.json"


class ConfigPane(Pane):
    """Config pane: FLM server, model, chat settings."""

    DEFAULT_CSS = """
    #config-tab-root {
        height: 100%;
    }
    .subsection-header {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="config-tab-root"):
            yield FlmModelPanel(id="flm-model-panel")
            yield FlmServerPanel(id="flm-server-panel")
            yield ChatPanel(id="chat-panel")

    def _fetch(self) -> None:
        try:
            cfg = engine.build_config_snapshot()
        except Exception as exc:
            log.warning("config_snapshot failed: %s", exc)
            cfg = {}

        # Fetch model lists and feed FlmModelPanel.
        installed_info = engine.list_flm_models("installed")
        not_installed_info = engine.list_flm_models("not-installed")

        installed_names: list[str] = list(installed_info.get("models") or [])
        not_installed_names: list[str] = list(not_installed_info.get("models") or [])
        daemon_reachable = "error" not in installed_info or "error" not in not_installed_info

        active = str(cfg.get("flm_server", {}).get("model") or "")
        model_loaded = bool(cfg.get("flm_server", {}).get("flm_model_loaded", False))
        server_cfg = cfg.get("flm_server") or {}
        chat_cfg = cfg.get("chat") or {}

        # Fetch FLM runtime version info (uses server-side cache, ~24h TTL).
        try:
            flm_runtime_data = flm_server.check_flm_update(
                cache_path=_FLM_UPDATE_CACHE,
                cache_only=True,
            )
        except Exception as exc:
            log.warning("flm_update_check failed: %s", exc)
            flm_runtime_data = {}

        # Update sub-panels (data-ingestion, not a render).
        self.call_later(self._update_flm_panel, installed_names, not_installed_names,
                        active, daemon_reachable, model_loaded, flm_runtime_data)
        self.call_later(self._update_server_panel, server_cfg)
        self.call_later(self._update_chat_panel, chat_cfg)

    def _update_flm_panel(self, installed: list[str], not_installed: list[str],
                          active: str, daemon_reachable: bool,
                          model_loaded: bool = False,
                          flm_runtime_data: dict | None = None) -> None:
        try:
            panel = self.query_one(FlmModelPanel)
        except Exception as exc:
            log.warning("FLM model panel not ready: %s", exc)
            return
        if not daemon_reachable:
            panel.mark_daemon_down()
            return
        panel.update_models(installed, not_installed, active, model_loaded,
                            daemon_reachable=True)
        if flm_runtime_data:
            panel.update_version_info(flm_runtime_data)

    def _update_server_panel(self, server_cfg: dict) -> None:
        try:
            panel = self.query_one(FlmServerPanel)
        except Exception as exc:
            log.warning("server panel not ready: %s", exc)
            return
        pm_raw = server_cfg.get("power_mode", PowerMode.BALANCED.value)
        panel.update_server_settings(
            auto_start=bool(server_cfg.get("auto_start", True)),
            power_mode=PowerMode(str(pm_raw).strip().lower()).value if isinstance(pm_raw, str) else PowerMode.BALANCED.value,
            log_to_file=bool(server_cfg.get("log_to_file", True)),
        )

    def _update_chat_panel(self, chat_cfg: dict) -> None:
        try:
            panel = self.query_one(ChatPanel)
        except Exception as exc:
            log.warning("chat panel not ready: %s", exc)
            return
        panel.update_config(chat_cfg)

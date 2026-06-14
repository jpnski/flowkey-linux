from __future__ import annotations

import logging

from config import PowerMode
from textual.app import ComposeResult
from textual.containers import Vertical

from tui.dashboard._daemon import _daemon_post
from tui.dashboard._pane import Pane
from tui.dashboard.config_pane.chat_panel import ChatPanel
from tui.dashboard.config_pane.chat_settings import FlmServerPanel
from tui.dashboard.config_pane.flm import FlmModelPanel
from tui.dashboard.config_pane.hotkeys import HotkeysPanel
from tui.dashboard.config_pane.input_processing import InputProcessingPanel

log = logging.getLogger("flowkey.tui.dashboard")


class ConfigPane(Pane):
    """Config pane: FLM server, model, chat, input processing, hotkeys."""

    DEFAULT_CSS = """
    .subsection-header {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="config-tab-root"):
            yield FlmModelPanel(id="flm-model-panel")
            yield FlmServerPanel(id="flm-server-panel")
            yield ChatPanel(id="chat-panel")
            yield InputProcessingPanel(id="input-processing-panel")
            yield HotkeysPanel(id="hotkeys-panel")

    def _fetch(self) -> None:
        config_resp = _daemon_post("config_snapshot")

        # Fetch model lists and feed FlmModelPanel.
        installed_resp = _daemon_post("models_installed")
        not_installed_resp = _daemon_post("models_not_installed")
        active = ""
        installed_names: list[str] = []
        not_installed_names: list[str] = []
        daemon_reachable = True

        if installed_resp.get("ok"):
            installed_names = list((installed_resp.get("result") or {}).get("models") or [])
        else:
            daemon_reachable = False
        if not_installed_resp.get("ok"):
            not_installed_names = list(
                (not_installed_resp.get("result") or {}).get("models") or []
            )
        else:
            daemon_reachable = False
        model_loaded = False
        if config_resp.get("ok"):
            cfg = config_resp.get("result") or {}
            active = str(cfg.get("flm_server", {}).get("model") or "")
            model_loaded = bool(cfg.get("flm_server", {}).get("flm_model_loaded", False))

        # Read server config for FlmServerPanel.
        server_cfg: dict = {}
        if config_resp.get("ok"):
            cfg = config_resp.get("result") or {}
            server_cfg = cfg.get("flm_server") or {}

        # Read chat config for ChatPanel.
        chat_cfg: dict = {}
        if config_resp.get("ok"):
            cfg = config_resp.get("result") or {}
            chat_cfg = cfg.get("chat") or {}

        # Fetch FLM runtime version info (uses server-side cache, ~24h TTL).
        flm_version_resp = _daemon_post("flm_update_check", {"cache_only": True})
        flm_runtime_data = flm_version_resp.get("result") if flm_version_resp.get("ok") else {}

        # Pass hotkeys to HotkeysPanel.
        transform_hotkeys: dict[str, str] = {}
        interaction_hotkeys: dict[str, str] = {}
        if config_resp.get("ok"):
            cfg = config_resp.get("result") or {}
            transform_hotkeys = dict(cfg.get("transform_hotkeys") or {})
            interaction_hotkeys = dict(cfg.get("interaction_hotkeys") or {})

        # Pass input_processing config to InputProcessingPanel.
        input_processing_cfg: dict = {}
        if config_resp.get("ok"):
            input_processing_cfg = dict((config_resp.get("result") or {}).get("input_processing") or {})

        # Update sub-panels (data-ingestion, not a render).
        self.call_later(self._update_hotkeys_panel, transform_hotkeys, interaction_hotkeys)
        self.call_later(self._update_input_processing_panel, input_processing_cfg)
        self.call_later(self._update_flm_panel, installed_names, not_installed_names,
                        active, daemon_reachable, model_loaded, flm_runtime_data)
        self.call_later(self._update_server_panel, server_cfg)
        self.call_later(self._update_chat_panel, chat_cfg)

    def _update_hotkeys_panel(self, transform_hotkeys: dict, interaction_hotkeys: dict) -> None:
        try:
            panel = self.query_one(HotkeysPanel)
        except Exception as exc:
            log.warning("hotkeys panel not ready: %s", exc)
            return
        panel.update_hotkeys(transform_hotkeys, interaction_hotkeys)

    def _update_input_processing_panel(self, cfg: dict) -> None:
        try:
            panel = self.query_one(InputProcessingPanel)
        except Exception as exc:
            log.warning("input processing panel not ready: %s", exc)
            return
        panel.update_config(cfg)

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

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical

from tui.dashboard._daemon import _daemon_post
from tui.dashboard._pane import Pane
from tui.dashboard.config_pane.chat_settings import ChatSettingsPanel
from tui.dashboard.config_pane.flm_panel import FlmModelPanel
from tui.dashboard.config_pane.hotkeys import HotkeysPanel
from tui.dashboard.config_pane.input_processing import InputProcessingPanel


class ConfigPane(Pane):
    """Config pane: hotkeys, model, performance mode + FlmModelPanel sub-widget."""

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
            yield ChatSettingsPanel(id="chat-settings-panel")
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
            active = str(cfg.get("flm_config", {}).get("active_model") or "")
            model_loaded = bool(cfg.get("flm_config", {}).get("flm_model_loaded", False))

        # Feed tone preset + server settings to ChatSettingsPanel.
        tone_preset = "formal"
        server_cfg: dict = {}
        if config_resp.get("ok"):
            cfg = config_resp.get("result") or {}
            tone_preset = str(cfg.get("tone", {}).get("preset", "formal"))
            server_cfg = cfg.get("flm_serving_config") or {}

        # Fetch FLM runtime version info (uses server-side cache, ~24h TTL).
        flm_version_resp = _daemon_post("flm_update_check", {"cache_only": True})
        flm_runtime_data = flm_version_resp.get("result") if flm_version_resp.get("ok") else {}

        # Pass hotkeys to HotkeysPanel.
        hotkeys: dict[str, str] = {}
        if config_resp.get("ok"):
            hotkeys = dict((config_resp.get("result") or {}).get("hotkeys") or {})

        # Pass input_processing config to InputProcessingPanel.
        input_processing_cfg: dict = {}
        if config_resp.get("ok"):
            input_processing_cfg = dict((config_resp.get("result") or {}).get("input_processing") or {})

        # Update sub-panels (data-ingestion, not a render).
        self.call_later(self._update_hotkeys_panel, hotkeys)
        self.call_later(self._update_input_processing_panel, input_processing_cfg)
        self.call_later(self._update_flm_panel, installed_names, not_installed_names,
                        active, daemon_reachable, model_loaded, flm_runtime_data)
        power_mode = cfg.get("flm_config", {}).get("power_mode", "balanced")
        self.call_later(self._update_chat_settings_panel, tone_preset, server_cfg, power_mode)

    def _update_hotkeys_panel(self, hotkeys: dict[str, str]) -> None:
        try:
            panel = self.query_one(HotkeysPanel)
        except Exception:
            return
        panel.update_hotkeys(hotkeys)

    def _update_input_processing_panel(self, cfg: dict) -> None:
        try:
            panel = self.query_one(InputProcessingPanel)
        except Exception:
            return
        panel.update_config(cfg)

    def _update_flm_panel(self, installed: list[str], not_installed: list[str],
                          active: str, daemon_reachable: bool,
                          model_loaded: bool = False,
                          flm_runtime_data: dict | None = None) -> None:
        try:
            panel = self.query_one(FlmModelPanel)
        except Exception:
            return
        if not daemon_reachable:
            panel.mark_daemon_down()
            return
        panel.update_models(installed, not_installed, active, model_loaded,
                            daemon_reachable=True)
        if flm_runtime_data:
            panel.update_version_info(flm_runtime_data)

    def _update_chat_settings_panel(self, tone_preset: str, server_cfg: dict,
                                    power_mode: str = "balanced") -> None:
        try:
            panel = self.query_one(ChatSettingsPanel)
        except Exception:
            return
        panel.update_tone(tone_preset)
        panel.update_server_settings(
            auto_start=bool(server_cfg.get("auto_start", True)),
            power_mode=str(power_mode),
        )

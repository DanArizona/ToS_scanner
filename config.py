# config.py
"""
ToS_scanner configuration adapter.

Rules:
    - mb_tools.config owns environment loading and precedence.
    - MB_* variables are canonical.
    - This file maps MB_* values into scanner-friendly Python names.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mb_tools.config import load_mb_config


@dataclass(frozen=True)
class ScannerConfig:
    # ThinkOrSwim window title / title-match settings
    window_tos: str
    window_tos_main: str
    window_tos_update: str
    window_tos_logon: str
    window_tos_export: str
    window_tos_wl_main: str
    window_tos_wl_export_match: str
    window_tos_wl_symbols: str

    # Window validation
    win_all_max_dims_err: int
    win_main_ref_width: int
    win_main_ref_height: int

    # Files / directories
    pwidget_yaml: str
    scans_dir: str
    lan_scans_dir: str
    vault_dir: str
    log_folder: str

    # Notifications
    notify_enable: bool
    notify_provider: str
    pushover_ecfg: str

    @property
    def pwidget_yaml_path(self) -> Path:
        return Path(self.pwidget_yaml).expanduser()

    @property
    def scans_path(self) -> Path:
        return Path(self.scans_dir).expanduser()

    @property
    def lan_scans_path(self) -> Path:
        return Path(self.lan_scans_dir).expanduser()

    @property
    def vault_path(self) -> Path:
        return Path(self.vault_dir).expanduser()

    @property
    def log_folder_path(self) -> Path:
        return Path(self.log_folder).expanduser()

    @property
    def pushover_ecfg_path(self) -> Path:
        return Path(self.pushover_ecfg).expanduser()

    @property
    def title_map(self) -> dict[str, str]:
        return {
            "win_main": self.window_tos_main,
            "win_logon": self.window_tos_logon,
            "win_updater": self.window_tos_update,
            "win_export": self.window_tos_export,
            "win_wl_main": self.window_tos_wl_main,
            "win_wl_symbols": self.window_tos_wl_symbols,
            "win_wl_export": self.window_tos_wl_export_match,
        }

    # ------------------------------------------------------------
    # Temporary compatibility aliases for old code.
    # Prefer lowercase names above in new/refactored code.
    # ------------------------------------------------------------

    @property
    def WINDOW_TOS(self) -> str:
        return self.window_tos

    @property
    def WINDOW_TOS_MAIN(self) -> str:
        return self.window_tos_main

    @property
    def WINDOW_TOS_UPDATE(self) -> str:
        return self.window_tos_update

    @property
    def WINDOW_TOS_LOGON(self) -> str:
        return self.window_tos_logon

    @property
    def WINDOW_TOS_EXPORT(self) -> str:
        return self.window_tos_export

    @property
    def WINDOW_TOS_WL_MAIN(self) -> str:
        return self.window_tos_wl_main

    @property
    def WINDOW_TOS_WL_EXPORT(self) -> str:
        return self.window_tos_wl_export_match

    @property
    def WINDOW_TOS_WL_SYMBOLS(self) -> str:
        return self.window_tos_wl_symbols

    @property
    def WINDOW_ALL_MAX_DIMS_ERR(self) -> int:
        return self.win_all_max_dims_err

    @property
    def WINDOW_MAIN_REF_WIDTH(self) -> int:
        return self.win_main_ref_width

    @property
    def WINDOW_MAIN_REF_HEIGHT(self) -> int:
        return self.win_main_ref_height

    @property
    def WIDGET_STACK_YAML(self) -> str:
        """Legacy alias. Prefer pwidget_yaml."""
        return self.pwidget_yaml

    @property
    def TARGET_FOLDER(self) -> str:
        """Legacy alias. Prefer scans_dir."""
        return self.scans_dir

    @property
    def LOG_FOLDER(self) -> str:
        """Legacy alias. Prefer log_folder."""
        return self.log_folder

    def print_cfg(self) -> None:
        print("window_tos:                 " + self.window_tos)
        print("window_tos_main:            " + self.window_tos_main)
        print("window_tos_update:          " + self.window_tos_update)
        print("window_tos_logon:           " + self.window_tos_logon)
        print("window_tos_export:          " + self.window_tos_export)
        print("window_tos_wl_main:         " + self.window_tos_wl_main)
        print("window_tos_wl_export_match: " + self.window_tos_wl_export_match)
        print("window_tos_wl_symbols:      " + self.window_tos_wl_symbols)

        print("win_all_max_dims_err:       " + repr(self.win_all_max_dims_err))
        print("win_main_ref_width:         " + repr(self.win_main_ref_width))
        print("win_main_ref_height:        " + repr(self.win_main_ref_height))

        print("pwidget_yaml:               " + self.pwidget_yaml)
        print("pwidget_yaml_path:          " + str(self.pwidget_yaml_path))
        print("scans_dir:                  " + self.scans_dir)
        print("lan_scans_dir:              " + self.lan_scans_dir)
        print("vault_dir:                  " + self.vault_dir)
        print("log_folder:                 " + self.log_folder)

        print("notify_enable:              " + repr(self.notify_enable))
        print("notify_provider:            " + self.notify_provider)
        print("pushover_ecfg:              " + self.pushover_ecfg)

def _get_str(mb_cfg, key: str, default: str) -> str:
    value = mb_cfg.get(key)
    if value in (None, ""):
        return default
    return str(value)


def _get_int(mb_cfg, key: str, default: int) -> int:
    value = mb_cfg.get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_bool(mb_cfg, key: str, default: bool = False) -> bool:
    value = mb_cfg.get(key)
    if value in (None, ""):
        return default

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False

    return default



def load_scanner_config() -> ScannerConfig:
    mb_cfg = load_mb_config()

    return ScannerConfig(
        window_tos=_get_str(mb_cfg, "MB_WINDOW_TOS", "thinkorswim"),
        window_tos_main=_get_str(mb_cfg, "MB_WINDOW_TOS_MAIN", "Main@thinkorswim"),
        window_tos_update=_get_str(mb_cfg, "MB_WINDOW_TOS_UPDATE", "thinkorswim updater"),
        window_tos_logon=_get_str(mb_cfg, "MB_WINDOW_TOS_LOGON", "Logon to thinkorswim"),
        window_tos_export=_get_str(mb_cfg, "MB_WINDOW_TOS_EXPORT", "Watchlist Scanner"),
        window_tos_wl_main=_get_str(mb_cfg, "MB_WINDOW_TOS_WL_MAIN", "Watchlist Main@thinkorswim"),
        window_tos_wl_export_match=_get_str(mb_cfg, "MB_WINDOW_TOS_WL_EXPORT_MATCH", "Watchlist '"),
        window_tos_wl_symbols=_get_str(mb_cfg, "MB_WINDOW_TOS_WL_SYMBOLS", "Symbols Import"),

        win_all_max_dims_err=_get_int(mb_cfg, "MB_WIN_ALL_MAX_DIMS_ERR", 4),
        win_main_ref_width=_get_int(mb_cfg, "MB_WIN_MAIN_REF_WIDTH", 1190),
        win_main_ref_height=_get_int(mb_cfg, "MB_WIN_MAIN_REF_HEIGHT", 1080),

        pwidget_yaml=_get_str(mb_cfg, "MB_PWIDGET_YAML", "layout_scanner3_v1p0.yaml"),
        scans_dir=_get_str(mb_cfg, "MB_SCANS", r"C:\Users\DanLa\Documents\github\stockScans"),
        lan_scans_dir=_get_str(mb_cfg, "MB_LAN_SCANS", r"\\MASTERBOT\scans"),
        vault_dir=_get_str(mb_cfg, "MB_VAULT", r"C:\Users\DanLa\MBV"),
        log_folder=_get_str(mb_cfg, "MB_LOG_FOLDER", r".\logs"),

        notify_enable=_get_bool(mb_cfg, "MB_NOTIFY_ENABLE", False),
        notify_provider=_get_str(mb_cfg, "MB_NOTIFY_PROVIDER", "pushover"),
        pushover_ecfg=_get_str(
            mb_cfg,
            "MB_PUSHOVER_ECFG",
            r".\secure\pushover.ecfg",
        ),

    )


def WindowConfig() -> ScannerConfig:
    """
    Temporary compatibility wrapper.

    Old code may still do:
        cfg = WindowConfig()

    New code should do:
        cfg = load_scanner_config()
    """
    return load_scanner_config()


if __name__ == "__main__":
    cfg = load_scanner_config()
    cfg.print_cfg()

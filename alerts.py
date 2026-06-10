# alerts.py

"""Alert and notification support for the ToS scanner.

This module owns local alert behavior such as Windows popup/sound alerts and
optional Pushover notification delivery. It also loads encrypted Pushover
credentials from the configured .ecfg file.
"""

from __future__ import annotations

import ctypes
import logging
import urllib.parse
import urllib.request
import winsound
from dataclasses import dataclass
from typing import Optional

from config import ScannerConfig
from mb_tools.secure_config import EcfgError, load_ecfg
from mb_tools.secure_config.qt_ecfg_editor import get_password

logger = logging.getLogger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


@dataclass(frozen=True)
class PushoverCredentials:
    app_token: str
    user_key: str


def load_pushover_credentials(cfg: ScannerConfig) -> Optional[PushoverCredentials]:
    """
    Load encrypted Pushover credentials from cfg.pushover_ecfg_path.

    Expected keys in the .ecfg file:
      - MB_PUSHOVER_APP_TOKEN
      - MB_PUSHOVER_USER_KEY

    Successful loading only proves that the .ecfg file was decrypted and the
    expected fields were present. It does not prove that Pushover will accept
    the token/user-key pair.
    """
    ecfg_path = cfg.pushover_ecfg_path

    if not ecfg_path.exists():
        logger.info("Pushover .ecfg file not found; notifications unavailable: %s", ecfg_path)
        return None

    password = get_password(
        title="Pushover Credentials",
        message=f"Enter password for:\n{ecfg_path}",
    )

    if not password:
        logger.info("Pushover .ecfg password was not provided; notifications unavailable.")
        return None

    try:
        secrets = load_ecfg(ecfg_path, password)
    except EcfgError as exc:
        # logger.warning(
        #     "Pushover .ecfg could not be decrypted or parsed; "
        #     "notifications disabled for this run. path=%s reason=%s",
        #     ecfg_path,
        #     exc,
        # )
        return None
    except Exception as exc:
        logger.warning(
            "Unexpected error while loading Pushover .ecfg; "
            "notifications disabled for this run. path=%s reason=%s",
            ecfg_path,
            exc,
        )
        return None

    app_token = secrets.get("MB_PUSHOVER_APP_TOKEN")
    user_key = secrets.get("MB_PUSHOVER_USER_KEY")

    if not app_token or not user_key:
        logger.warning(
            "Pushover .ecfg decrypted, but required credential fields are missing."
        )
        return None

    logger.info(
        "Pushover .ecfg decrypted successfully; credential delivery has not been tested."
    )

    return PushoverCredentials(
        app_token=str(app_token),
        user_key=str(user_key),
    )


class AlertManager:
    def __init__(
        self,
        logger: logging.Logger,
        *,
        notifications_enabled: bool = False,
        pushover_credentials: Optional[PushoverCredentials] = None,
    ) -> None:
        self.logger = logger
        self.notifications_enabled = notifications_enabled
        self.pushover_credentials = pushover_credentials

    def popup(self, title: str, message: str) -> None:
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                message,
                title,
                0x10 | 0x1000,  # MB_ICONHAND | MB_SYSTEMMODAL
            )
        except Exception as exc:
            self.logger.exception("Popup dialog failed: %s", exc)

    def play_alert_sound(self) -> None:
        try:
            winsound.MessageBeep(winsound.MB_ICONHAND)
            winsound.Beep(1200, 500)
            winsound.Beep(1000, 700)
        except Exception as exc:
            self.logger.exception("Alert sound failed: %s", exc)

    def send_pushover(self, title: str, message: str) -> None:
        if not self.notifications_enabled:
            self.logger.info("Pushover skipped: notifications are disabled.")
            return

        if self.pushover_credentials is None:
            self.logger.error("Pushover requested but credentials are not loaded.")
            return

        payload = urllib.parse.urlencode(
            {
                "token": self.pushover_credentials.app_token,
                "user": self.pushover_credentials.user_key,
                "title": title,
                "message": message,
                "priority": 1,
            }
        ).encode("utf-8")

        try:
            req = urllib.request.Request(PUSHOVER_API_URL, data=payload, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                self.logger.info("Pushover sent successfully. Response=%s", body)
        except Exception as exc:
            self.logger.exception("Pushover send failed: %s", exc)

    def critical(self, title: str, message: str) -> None:
        self.logger.critical("%s | %s", title, message)
        self.play_alert_sound()
        self.send_pushover(title, message)
        self.popup(title, message)
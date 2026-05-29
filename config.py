# config.py
from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass
class WindowConfig:
    WINDOW_TOS: str = os.getenv("WINDOW_TOS", "thinkorswim")
    WINDOW_TOS_MAIN: str = os.getenv("WINDOW_TOS_MAIN", "Main@thinkorswim")
    WINDOW_TOS_UPDATE: str = os.getenv("WINDOW_TOS_UPDATE", "thinkorswim updater")
    WINDOW_TOS_LOGON: str = os.getenv("WINDOW_TOS_LOGON", "Logon to thinkorswim")
    WINDOW_TOS_EXPORT: str = os.getenv("WINDOW_TOS_EXPORT", "Watchlist Scanner")
    WINDOW_TOS_WL_MAIN: str = os.getenv("WINDOW_TOS_WL_MAIN", "Watchlist Main@thinkorswim")
    WINDOW_TOS_WL_EXPORT: str = os.getenv("WINDOW_TOS_WL_EXPORT", "Watchlist 'Default'")
    WINDOW_TOS_WL_SYMBOLS: str = os.getenv("WINDOW_TOS_WL_SYMBOLS", "Symbols import")
    
    WINDOW_ALL_MAX_DIMS_ERR: int = int(os.getenv("WINDOW_ALL_MAX_DIMS_ERR", 4))
    WINDOW_MAIN_REF_WIDTH: int = int(os.getenv("WINDOW_MAIN_REF_WIDTH", 1190))
    WINDOW_MAIN_REF_HEIGHT: int = int(os.getenv("WINDOW_MAIN_REF_HEIGHT", 1080))
    TARGET_FOLDER: str = os.getenv("TARGET_FOLDER", r"\\MASTERBOT\ToS_scans\scanner_survey")
#    WIDGET_STACK_YAML: str = os.getenv("WIDGET_STACK_YAML", "layout.yaml")
    WIDGET_STACK_YAML: str = os.getenv("WIDGET_STACK_YAML", "layout_scanner3_v1p0.yaml")
    
    MKTBOT_SCANS: str = os.getenv("MKTBOT_SCANS", r"C:\Users\DanLa\Documents\github\stockScans")
    MKTBOT_VAULT: str = os.getenv("MKTBOT_VAULT", r"C:\Users\DanLa\MBV")

#    title_map = {
#        "win_main": WINDOW_TOS_MAIN,
#        "win_logon": WINDOW_TOS_LOGON,
#        "win_updater": WINDOW_TOS_UPDATE,
#        "win_saver": WINDOW_TOS_SAVER
#    }

    title_map = {
        "win_main": WINDOW_TOS_MAIN,
        "win_logon": WINDOW_TOS_LOGON,
        "win_updater": WINDOW_TOS_UPDATE,
        "win_export": WINDOW_TOS_EXPORT,
        "win_wl_main": WINDOW_TOS_WL_MAIN,
        "win_wl_symbols": WINDOW_TOS_WL_SYMBOLS,
        "win_wl_export": WINDOW_TOS_WL_EXPORT,
    }

    def print_cfg(self):
        # pass
        print("WINDOW_TOS:              " + self.WINDOW_TOS )
        print("WINDOW_TOS_MAIN:         " + self.WINDOW_TOS_MAIN )
        print("WINDOW_TOS_UPDATE:       " + self.WINDOW_TOS_UPDATE )
        print("WINDOW_TOS_LOGON:        " + self.WINDOW_TOS_LOGON )
        print("WINDOW_TOS_EXPORT:       " + self.WINDOW_TOS_EXPORT )
        print("WINDOW_TOS_WL_MAIN:      " + self.WINDOW_TOS_WL_MAIN )
        print("WINDOW_TOS_WL_EXPORT:    " + self.WINDOW_TOS_WL_EXPORT )
        print("WINDOW_TOS_WL_SYMBOLS:   " + self.WINDOW_TOS_WL_SYMBOLS )
        print("WINDOW_ALL_MAX_DIMS_ERR: " + repr(self.WINDOW_ALL_MAX_DIMS_ERR ))
        print("WINDOW_MAIN_REF_WIDTH:   " + repr(self.WINDOW_MAIN_REF_WIDTH ))
        print("WINDOW_MAIN_REF_HEIGHT:  " + repr(self.WINDOW_MAIN_REF_HEIGHT ))
        print("TARGET_FOLDER:           " + self.TARGET_FOLDER )
        print("WIDGET_STACK_YAML:       " + self.WIDGET_STACK_YAML )
        print("MKTBOT_SCANS:            " + self.MKTBOT_SCANS )
        print("MKTBOT_VAULT:            " + self.MKTBOT_VAULT )

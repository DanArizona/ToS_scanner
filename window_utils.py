# window_utils.py

import logging
import time
import win32process
import psutil
import pygetwindow as gw
from typing import Optional
import pandas as pd
from models import WidgetStack

import win32gui
import win32con
import win32api

def get_process_name(hwnd) -> Optional[str]:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name()
    except Exception:
        return None

def get_windows_dataframe() -> pd.DataFrame:
    windows = gw.getAllWindows()
    window_data = []

    for w in windows:
        if w.title:
            hwnd = w._hWnd
            process_name = get_process_name(hwnd)
            is_uwp = process_name and process_name.lower() == "applicationframehost.exe"

            window_data.append({
                'Title': w.title,
                'Left': w.left,
                'Top': w.top,
                'Width': w.width,
                'Height': w.height,
                'IsActive': w.isActive,
                'IsMaximized': w.isMaximized,
                'IsMinimized': w.isMinimized,
                'ProcessName': process_name,
                'IsUWP': is_uwp
            })

    df = pd.DataFrame(window_data)
    return df.sort_values(by='Title').reset_index(drop=True)


def bring_window_to_front(widget_name: str, widget_stacks: dict[str, WidgetStack], title_map: dict[str, str]):
    if widget_name not in widget_stacks:
        return

    stack = widget_stacks[widget_name]
    while stack.parent:
        stack = stack.parent

    # search_title = title_map.get(stack.bbox.name, stack.bbox.name)
    search_title = title_map.get(stack.name, stack.name)
    windows = gw.getWindowsWithTitle(search_title)
    if windows:
        win = windows[0]
        if win.isMinimized:
            win.restore()
        try:
            win.activate()
        except Exception as e:
            logging.error(f"Failed to activate window '{search_title}': {e}")


def is_window_visible(widget_name: str, widget_stacks: dict[str, WidgetStack], title_map: dict[str, str]) -> bool:
    if widget_name not in widget_stacks:
        return False

    stack = widget_stacks[widget_name]
    while stack.parent:
        stack = stack.parent

    search_title = title_map.get(stack.name, stack.name)
    # titles = [title.strip().lower() for title in gw.getAllTitles() if title.strip()]
    titles = [
        title.strip().lower()
        for title in gw.getAllTitles()
        if title and title.strip()
    ]
    return any(search_title.lower() in t for t in titles)


def bring_window_to_top(window_title: str) -> bool:
    """Bring a window to the front given its title. Return True on success."""
    windows = gw.getWindowsWithTitle(window_title)
    if not windows:
        return False

    try:
        win = windows[0]
        win.activate()
        return True
    except Exception as e:
        logging.error(f"Failed to bring window '{window_title}' to top: {e}")
        return False

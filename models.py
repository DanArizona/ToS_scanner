# models.py

"""Scanner-local pseudo-widget geometry and hierarchy models.

This module defines WidgetBBox for rectangular screen geometry and WidgetStack
for named pseudo-widget nodes arranged in a parent/child hierarchy. These models
support coordinate calculations, live root-window position lookup, simple screen
capture/OCR diagnostics, and tree reporting for ToS GUI automation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
# from typing import Optional, Union
from typing import Any, Optional
import numpy as np
import pyautogui
import pytesseract


@dataclass
class WidgetBBox:
    """
    Geometry only.
    No widget name or expected text lives here.
    """
    width: int
    height: int
    Xtl: int
    Ytl: int

    @classmethod
    # def from_dataframe_row(cls, row: Union[dict, "pd.Series"]):
    def from_dataframe_row(cls, row: Any) -> "WidgetBBox":
        return cls(
            width=int(row["Width"]),
            height=int(row["Height"]),
            Xtl=int(row["Left"]),
            Ytl=int(row["Top"]),
        )

    def center(self) -> tuple[int, int]:
        return (
            self.Xtl + self.width // 2,
            self.Ytl + self.height // 2,
        )

    def compare_to(self, other: "WidgetBBox") -> dict:
        return {
            "width_diff": self.width - other.width,
            "height_diff": self.height - other.height,
            "Xtl_diff": self.Xtl - other.Xtl,
            "Ytl_diff": self.Ytl - other.Ytl,
        }

    def capture_and_analyze(self) -> dict:
        """
        Capture this bbox on screen and return simple OCR / contrast metrics.
        """
        bbox = (self.Xtl, self.Ytl, self.Xtl + self.width, self.Ytl + self.height)
        screenshot = pyautogui.screenshot(region=bbox)

        text = pytesseract.image_to_string(screenshot)
        gray = screenshot.convert("L")
        pixels = np.array(gray)
        stddev = float(np.std(pixels))

        return {
            "ocr_text": text.strip(),
            "grayscale_stddev": stddev,
            "width": self.width,
            "height": self.height,
        }


@dataclass(eq=False)
class WidgetStack:
    """
    Semantic widget node:
      - name lives here
      - ptxt lives here
      - geometry lives in bbox
      - hierarchy lives here
    """
    name: str
    bbox: WidgetBBox
    parent: Optional["WidgetStack"] = None
    window_title: Optional[str] = None
    ptxt: str = ""
    children: list["WidgetStack"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.parent is not None:
            self.parent.children.append(self)

    def is_root(self) -> bool:
        return self.parent is None

    def has_ptxt(self) -> bool:
        return bool(self.ptxt.strip())

    def ancestry(self) -> list[str]:
        lineage: list[str] = []
        current: Optional["WidgetStack"] = self
        while current is not None:
            lineage.append(current.name)
            current = current.parent
        return lineage[::-1]

    def _find_live_window_top_left(self) -> Optional[tuple[int, int]]:
        """
        For a root window, try to find its live on-screen top-left corner.
        Uses window_title as a prefix match.
        """
        if not self.window_title:
            return None

        try:
            import pygetwindow as gw

            title_prefix = self.window_title.strip()
            for win in gw.getAllWindows():
                title = (win.title or "").strip()
                if title.startswith(title_prefix):
                    return int(win.left), int(win.top)
        except Exception:
            pass

        return None

    def get_absolute_position(self) -> tuple[int, int]:
        """
        Return the widget's absolute on-screen position.

        Important behavior:
        - For root windows with a window_title, prefer the live window position
          from pygetwindow.
        - Fall back to YAML bbox if the live window is not found.
        """
        x, y = self.bbox.Xtl, self.bbox.Ytl

        if self.parent is None:
            live_pos = self._find_live_window_top_left()
            if live_pos is not None:
                return live_pos
            return x, y

        current = self.parent
        while current is not None:
            if current.parent is None:
                live_pos = current._find_live_window_top_left()
                if live_pos is not None:
                    x += live_pos[0]
                    y += live_pos[1]
                    return x, y

            x += current.bbox.Xtl
            y += current.bbox.Ytl
            current = current.parent

        return x, y

    def get_absolute_center(self) -> tuple[int, int]:
        x, y = self.get_absolute_position()
        return x + self.bbox.width // 2, y + self.bbox.height // 2

    def capture_and_analyze(self) -> dict:
        """
        Capture/analyze using the widget's own bbox.
        Include name and ptxt for easier downstream debugging.
        """
        result = self.bbox.capture_and_analyze()
        result["name"] = self.name
        result["ptxt"] = self.ptxt
        return result

    def print_tree(
        self,
        prefix: str = "",
        is_last: bool = True,
        log: bool = True,
        logger=None,
    ) -> None:
        """
        Print or log the widget tree. If `logger` is provided and `log=True`,
        use logger; otherwise print.
        """
        from pygetwindow import getAllTitles

        connector = "└── " if is_last else "├── "
        abs_x, abs_y = self.get_absolute_position()
        position_str = f"<{abs_x}, {abs_y}>"

        visibility_note = ""
        if self.parent is None:
            # all_titles = [title.strip().lower() for title in getAllTitles() if title.strip()]
            all_titles = [
                title.strip().lower()
                for title in getAllTitles()
                if title and title.strip()
            ]
            search_title = self.window_title or self.name
            is_visible = any(search_title.lower() in title for title in all_titles)
            visibility_note = " [VISIBLE]" if is_visible else " [HIDDEN]"

        ptxt_note = f' ptxt="{self.ptxt}"' if self.ptxt else ""

        line = (
            f"{prefix}{connector}{self.name} "
            f"[{self.bbox.Xtl}, {self.bbox.Ytl}] "
            f"{self.bbox.width}x{self.bbox.height} "
            f"{position_str}{ptxt_note}{visibility_note}"
        )

        if log and logger:
            logger.info(line)
        else:
            print(line)

        new_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(self.children):
            child.print_tree(
                prefix=new_prefix,
                is_last=(i == len(self.children) - 1),
                log=log,
                logger=logger,
            )

    def find_widget(self, name: str) -> Optional["WidgetStack"]:
        if self.name == name:
            return self
        for child in self.children:
            found = child.find_widget(name)
            if found:
                return found
        return None

    def __hash__(self):
        return hash(id(self))

    def __eq__(self, other):
        return self is other
    
    
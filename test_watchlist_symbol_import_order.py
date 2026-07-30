from __future__ import annotations

from typing import Any

import pytest

from tos_pwidget_actions import ToSActionsController


@pytest.mark.parametrize(
    ("mode", "mode_widget"),
    [
        ("replace", "rbutt_si_replace"),
        ("add", "rbutt_si_add"),
    ],
)
def test_watchlist_symbol_import_uses_safe_action_order(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    mode_widget: str,
) -> None:
    controller = object.__new__(ToSActionsController)
    events: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        controller,
        "_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        controller,
        "_bring_named_window_to_front",
        lambda window_name: events.append(
            ("bring_to_front", window_name)
        ),
    )
    monkeypatch.setattr(
        controller,
        "_move_center",
        lambda widget_name: events.append(
            ("move_center", widget_name)
        ),
    )
    monkeypatch.setattr(
        controller,
        "_move_vh",
        lambda widget_name: events.append(
            ("move_vh", widget_name)
        ),
    )
    monkeypatch.setattr(
        controller,
        "_click",
        lambda: events.append(("click",)),
    )

    def wait_for_window_to_close(
        window_name: str,
        *,
        timeout_s: float,
    ) -> bool:
        events.append(
            ("wait_for_close", window_name, timeout_s)
        )
        return True

    monkeypatch.setattr(
        controller,
        "_wait_for_window_to_close",
        wait_for_window_to_close,
    )

    controller._apply_watchlist_symbols_from_clipboard(mode=mode)

    assert events == [
        ("bring_to_front", "win_wl_symbols_import"),
        ("move_center", "rbutt_si_paste"),
        ("click",),
        ("move_center", mode_widget),
        ("click",),
        ("move_vh", "btn_si_save"),
        ("click",),
        (
            "wait_for_close",
            "win_wl_symbols_import",
            5.0,
        ),
    ]

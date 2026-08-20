from __future__ import annotations

from typing import Any

import pytest

from tos_pwidget_actions import ToSActionsController


@pytest.mark.parametrize(
    (
        "mode",
        "mode_widget",
        "opposite_widget",
    ),
    [
        (
            "replace",
            "rbutt_si_replace",
            "rbutt_si_add",
        ),
        (
            "add",
            "rbutt_si_add",
            "rbutt_si_replace",
        ),
    ],
)
def test_watchlist_symbol_import_uses_safe_action_order(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    mode_widget: str,
    opposite_widget: str,
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

    def ensure_selected(
        widget_name: str,
        *,
        opposite_widget_name: str | None = None,
        attempts: int = 3,
        settle_s: float = 0.20,
    ) -> None:
        events.append(
            (
                "ensure_selected",
                widget_name,
                opposite_widget_name,
            )
        )

    monkeypatch.setattr(
        controller,
        "_ensure_radio_button_selected",
        ensure_selected,
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

    controller._apply_watchlist_symbols_from_clipboard(
        mode=mode
    )

    assert events == [
        (
            "bring_to_front",
            "win_wl_symbols_import",
        ),
        (
            "ensure_selected",
            "rbutt_si_paste",
            "rbutt_si_file",
        ),
        (
            "ensure_selected",
            mode_widget,
            opposite_widget,
        ),
        ("move_vh", "btn_si_save"),
        ("click",),
        (
            "wait_for_close",
            "win_wl_symbols_import",
            5.0,
        ),
    ]


def test_radio_selection_retries_until_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = object.__new__(ToSActionsController)

    moves: list[str] = []
    clicks: list[str] = []

    monkeypatch.setattr(
        controller,
        "_log",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        controller,
        "_move_center",
        lambda widget_name: moves.append(widget_name),
    )

    monkeypatch.setattr(
        controller,
        "_click",
        lambda: clicks.append("click"),
    )

    monkeypatch.setattr(
        "tos_pwidget_actions.time.sleep",
        lambda seconds: None,
    )

    states = iter(
        [
            False,
            True,
        ]
    )

    monkeypatch.setattr(
        controller,
        "_radio_button_is_selected",
        lambda widget_name: next(states),
    )

    controller._ensure_radio_button_selected(
        "rbutt_si_paste",
        attempts=3,
    )

    assert moves == [
        "rbutt_si_paste",
        "rbutt_si_paste",
    ]
    assert clicks == [
        "click",
        "click",
    ]


def test_radio_selection_retries_when_opposite_is_also_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = object.__new__(ToSActionsController)

    moves: list[str] = []
    clicks: list[str] = []

    monkeypatch.setattr(
        controller,
        "_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        controller,
        "_move_center",
        lambda widget_name: moves.append(widget_name),
    )
    monkeypatch.setattr(
        controller,
        "_click",
        lambda: clicks.append("click"),
    )
    monkeypatch.setattr(
        "tos_pwidget_actions.time.sleep",
        lambda seconds: None,
    )

    # Attempt 1:
    #   requested radio = selected
    #   opposite radio  = ALSO selected -> reject
    #
    # Attempt 2:
    #   requested radio = selected
    #   opposite radio  = unselected -> accept
    states = iter(
        [
            True,
            True,
            True,
            False,
        ]
    )

    monkeypatch.setattr(
        controller,
        "_radio_button_is_selected",
        lambda widget_name: next(states),
    )

    controller._ensure_radio_button_selected(
        "rbutt_si_add",
        opposite_widget_name="rbutt_si_replace",
        attempts=3,
    )

    assert moves == [
        "rbutt_si_add",
        "rbutt_si_add",
    ]
    assert clicks == [
        "click",
        "click",
    ]


def test_radio_selection_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = object.__new__(ToSActionsController)

    monkeypatch.setattr(
        controller,
        "_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        controller,
        "_move_center",
        lambda widget_name: None,
    )
    monkeypatch.setattr(
        controller,
        "_click",
        lambda: None,
    )
    monkeypatch.setattr(
        "tos_pwidget_actions.time.sleep",
        lambda seconds: None,
    )
    monkeypatch.setattr(
        controller,
        "_radio_button_is_selected",
        lambda widget_name: False,
    )

    with pytest.raises(
        RuntimeError,
        match="refusing to continue",
    ):
        controller._ensure_radio_button_selected(
            "rbutt_si_add",
            attempts=3,
        )


def test_import_does_not_click_ok_if_verification_fails(
    monkeypatch: pytest.MonkeyPatch,
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
        lambda window_name: None,
    )

    def fail_verification(
        widget_name: str,
        **kwargs: Any,
    ) -> None:
        raise RuntimeError("verification failed")

    monkeypatch.setattr(
        controller,
        "_ensure_radio_button_selected",
        fail_verification,
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

    with pytest.raises(
        RuntimeError,
        match="verification failed",
    ):
        controller._apply_watchlist_symbols_from_clipboard(
            mode="add"
        )

    # Most important safety assertion:
    # the OK button was never approached or clicked.
    assert events == []

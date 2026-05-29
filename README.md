# ToS Scanner

Automation support code for ThinkOrSwim scanner export workflows.

This project is being extracted from the older `thousand_miles` repository and simplified to use reusable infrastructure from `mb_tools`.

## Goals

- Keep ThinkOrSwim-specific scanner logic here.
- Move reusable pseudo-widget, logging, window-survey, and configuration tools into `mb_tools`.
- Simplify `scan_main_v1p2.py` into a small orchestration script.

## Related repositories

- `mb_tools`
- `mb_tools_tests`

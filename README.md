# ToS_scanner

`ToS_scanner` automates selected ThinkOrSwim scanner workflows using pseudo-widget coordinates defined in an external YAML layout file.

The current main entry point is:

```cmd
python scan_main_v1p2.py
```

The scanner is designed to export ThinkOrSwim scan results to CSV files on a timed schedule, and also supports manual scan/export actions from a small Qt control panel.

## Current status

The current working branch is focused on `scan_main_v1p2.py`.

Implemented and tested:

* Qt control panel for starting and stopping scan exports
* ThinkOrSwim pseudo-widget export workflow
* Editable output directory
* Manual export-dialog initialization
* Manual "Scan and Export CSV" action
* Scheduled CSV export loop
* Graceful stop behavior
* Global hotkeys
* Queued logging through `mb_tools.logging_queue`
* Startup checks for required windows, output directory, and plain-text secret risks

## Related repositories

This project depends on related local repositories:

* `mb_tools` — shared utilities, configuration loading, pseudo-widget helpers, logging helpers
* `pwidget_layouts` — shared pseudo-widget YAML layouts
* `ToS_gui_survey` — tools for inspecting and validating ThinkOrSwim pseudo-widget layouts

The scanner layout should normally be selected through `MB_PWIDGET_YAML`.

Current test layout:

```text
C:\Users\DanLa\Documents\github\pwidget_layouts\layout_scanner3_v1p1dev2.yaml
```

## Configuration

Configuration uses the `mb_tools` configuration precedence model:

```text
local .env > Windows environment > mb_tools defaults.env
```

Important environment/configuration values include:

```text
MB_PWIDGET_YAML
MB_SCANS
MB_LOG_FOLDER

MB_WINDOW_TOS
MB_WINDOW_TOS_MAIN
MB_WINDOW_TOS_LOGON
MB_WINDOW_TOS_UPDATE
MB_WINDOW_TOS_EXPORT
MB_WINDOW_TOS_WL_MAIN
MB_WINDOW_TOS_WL_EXPORT_MATCH
MB_WINDOW_TOS_WL_SYMBOLS

MB_WIN_ALL_MAX_DIMS_ERR
MB_WIN_MAIN_REF_WIDTH
MB_WIN_MAIN_REF_HEIGHT
```

Typical values used during development:

```text
MB_WINDOW_TOS_MAIN=Main@thinkorswim
MB_WINDOW_TOS_EXPORT=Watchlist Scanner
MB_WIN_MAIN_REF_WIDTH=1190
MB_WIN_MAIN_REF_HEIGHT=1080
MB_WIN_ALL_MAX_DIMS_ERR=4
```

## Running the scanner

Typical run command:

```cmd
python scan_main_v1p2.py --layout-path C:\Users\DanLa\Documents\github\pwidget_layouts\layout_scanner3_v1p1dev2.yaml
```

Dry-run command:

```cmd
python scan_main_v1p2.py --dry-run --layout-path C:\Users\DanLa\Documents\github\pwidget_layouts\layout_scanner3_v1p1dev2.yaml
```

Dry run does not control ThinkOrSwim export dialogs. It creates stub CSV files for validating scheduler, output path, logging, and GUI behavior.

Useful compile check:

```cmd
python -m py_compile scan_main_v1p2.py control_panel.py control_manager.py exporter.py scan_runner.py tos_pwidget_actions.py scanner_logging.py gui_support.py
```

## Control panel

The control panel is titled:

```text
JTM Scan Manager
```

Main controls:

| Control             | Purpose                                                                                                   |
| :------------------ | :-------------------------------------------------------------------------------------------------------- |
| Output directory    | Select where scanner CSV files are written                                                                |
| Apply               | Apply the edited output directory                                                                         |
| Manual init         | Open the ThinkOrSwim export dialog, set the export directory, save a setup CSV, verify it, then remove it |
| Unlock ToS Scan     | Clear internal scan gating state                                                                          |
| Press ToS Scan      | Press the ThinkOrSwim scan button without exporting                                                       |
| Scan and Export CSV | Immediately press the ThinkOrSwim scan button and export a CSV                                            |
| Start Scan          | Start the scheduled scanner loop                                                                          |
| Stop Scan           | Gracefully stop the scheduled scanner loop                                                                |
| Exit Scan Manager   | Stop the runner if needed and exit the GUI                                                                |

After changing the output directory, run **Manual init** before relying on real ThinkOrSwim exports. This applies the selected directory to the ThinkOrSwim export dialog.

## Hotkeys

Global hotkeys:

| Hotkey       | Action                               |
| :----------- | :----------------------------------- |
| `ESC`        | Gracefully stop the active scan loop |
| `Ctrl+Alt+E` | Run Scan and Export CSV              |
| `Ctrl+Alt+Q` | Exit Scan Manager                    |

`Ctrl+Alt+E` is ignored while the scheduled scan loop is active. Stop the scan loop first before running a manual scan/export.

## Output files

Scheduled exports use filenames like:

```text
scan-YYYY-MM-DD-HH-MM-SS-ToS.csv
```

Manual scan/export files use filenames like:

```text
scan-YYYY-MM-DD-HH-MM-SS-ToS-manual.csv
```

The timestamp is based on the scanner's Eastern Time scheduling logic.

Manual init temporarily creates:

```text
__scan_export_setup__.csv
```

After verification, this setup file is removed.

## Logs

Logs are written to the configured log directory, usually:

```text
logs
```

The current queued logging setup creates:

```text
scan-ToS YYYY-MM-DD_HH-MM-SS ALL.log
scan-ToS YYYY-MM-DD_HH-MM-SS MAIN.log
scan-ToS YYYY-MM-DD_HH-MM-SS thread <ThreadName>.log
```

Typical worker thread logs include:

```text
thread Heartbeat.log
thread ManualInit.log
thread ManualScanExport.log
thread ScanRunnerMain.log
```

## Startup checks

Startup checks verify items such as:

* Required plain-text secrets are not present
* The main ThinkOrSwim window is open
* The main ThinkOrSwim window size is within tolerance
* The output directory is accessible

## Repository notes

Generated/private files should not be committed:

```text
.env
secure/
runtime/
logs/
__pycache__/
client.log
*.pyc
```

Historical notes and old layout/debug references are stored under `docs/archive`.

## Development workflow

Recommended quick checks before committing:

```cmd
git status --short
python -m py_compile scan_main_v1p2.py control_panel.py control_manager.py exporter.py scan_runner.py tos_pwidget_actions.py scanner_logging.py gui_support.py
```

Recommended smoke test before merging:

1. Start the scanner in real mode.
2. Change output directory.
3. Run Manual init.
4. Run Scan and Export CSV.
5. Start scheduled scan loop.
6. Let at least two scheduled exports complete.
7. Confirm `Ctrl+Alt+E` is ignored while running.
8. Stop with `ESC`.
9. Start again.
10. Exit with `Ctrl+Alt+Q`.
11. Confirm CSV files and logs look correct.

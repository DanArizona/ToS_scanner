# ToS_scanner

`ToS_scanner` automates selected ThinkOrSwim scanner and Watchlist workflows using pseudo-widget coordinates defined in an external YAML layout file.

The current v2 development work is centered on a file-based command loop that runs on the ThinkOrSwim computer and accepts commands from another computer on the local network.

## Current entry points

### V2 command loop

The current v2 command-loop entry point is:

```cmd
python scan_command_loop.py
```

This program:

* watches a command directory for JSON command files;
* dispatches scanner and Watchlist actions;
* controls ThinkOrSwim through pseudo-widget actions;
* publishes a periodic scanner heartbeat;
* reports runtime state such as running, paused, busy, or stopped.

### Legacy v1 GUI

The earlier Qt control-panel entry point remains available:

```cmd
python scan_main_v1p2.py
```

The v1 program provides scheduled scan exports, manual export controls, hotkeys, and queued logging. It remains useful, but current development on branch `scan_main_v2p0dev0` is focused on the v2 command architecture.

## Related repositories

This project uses several related repositories:

* `mb_tools` — shared configuration, pseudo-widget, windowing, logging, scanner-command, and scanner-status utilities;
* `pwidget_layouts` — shared pseudo-widget YAML layouts;
* `ToS_gui_survey` — tools for inspecting and validating ThinkOrSwim pseudo-widget layouts.

The scanner layout is normally selected through:

```text
MB_PWIDGET_YAML
```

Current development layout:

```text
C:\Users\DanLa\Documents\github\pwidget_layouts\layout_scanner3_v1p1dev2.yaml
```

## Configuration

Scanner configuration uses the `mb_tools` precedence model:

```text
project .env > Windows environment > mb_tools defaults.env
```

Important scanner values include:

```text
MB_PWIDGET_YAML
MB_SCANS
MB_LAN_SCANS
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

Typical development values include:

```dotenv
MB_WINDOW_TOS_MAIN=Main@thinkorswim
MB_WINDOW_TOS_WL_MAIN=Watchlist Main@thinkorswim
MB_WINDOW_TOS_WL_EXPORT_MATCH=Watchlist '
MB_WINDOW_TOS_WL_SYMBOLS=Symbols Import

MB_WIN_MAIN_REF_WIDTH=1190
MB_WIN_MAIN_REF_HEIGHT=1080
MB_WIN_ALL_MAX_DIMS_ERR=4
```

Configured window names are treated as stable title prefixes. For example:

```text
Main@thinkorswim
```

can match a changing title such as:

```text
Main@thinkorswim [build 1992]
```

## V2 command architecture

The current development arrangement uses two Windows computers:

```text
MasterBot   Sends scanner commands and reads scanner status
El-Cheapo   Runs ThinkOrSwim and scan_command_loop.py
```

MasterBot reaches the command directory through:

```text
\\El-Cheapo\SCANCTRL
```

The corresponding local directory on El-Cheapo is currently:

```text
C:\Users\DanLa\Documents\github\stockScans_control
```

The command root is resolved in this order:

1. The `--root` command-line argument
2. The `MB_SCAN_CONTROL` environment variable
3. The local El-Cheapo development default:
   `C:\Users\DanLa\Documents\github\stockScans_control`


### Command-directory layout

The command root contains:

```text
stockScans_control\
    incoming\
    processing\
    processed\
    failed\
    status\
        scanner_heartbeat.json
```

Directory purposes:

| Directory    | Purpose                                                |
| ------------ | ------------------------------------------------------ |
| `incoming`   | Newly published JSON commands                          |
| `processing` | Commands currently being read and validated            |
| `processed`  | Commands accepted and submitted to the job queue       |
| `failed`     | Commands rejected because of parsing or ingress errors |
| `status`     | Scanner heartbeat and runtime-status files             |

Commands are published atomically as temporary files and then renamed to `.json`. The ingress process also retries transient Windows or SMB file-lock errors on a later poll.

## Starting the v2 command loop

Before starting:

1. Open the ThinkOrSwim Main scanner window.
2. Open the ThinkOrSwim Watchlist window.
3. Place the windows in the positions and dimensions expected by the configured YAML layout.
4. Confirm the local command root is available.
5. Confirm the MasterBot share points to the same command root.

Start the loop from the repository root:

```cmd
python scan_command_loop.py
```

Use an explicit command root:

```cmd
python scan_command_loop.py --root C:\path\to\scanner-control
```

The program displays a setup checklist and waits at:

```text
Press Enter when ready...
```

While it is waiting, the heartbeat state is:

```text
waiting_for_operator
```

Press Enter to begin polling the command directory.

The loop can be stopped by:

* sending a `stop` command from MasterBot; or
* pressing `Ctrl+C` in the El-Cheapo console.

A `stop` command sets the shutdown flag and exits the command loop. `Ctrl+C` also publishes a final stopped heartbeat.

## Supported MasterBot commands

The current `mb-scan-command` sender supports:

| Command              | Purpose                                                 |
| -------------------- | ------------------------------------------------------- |
| `start`              | Mark the scanner as running                             |
| `stop`               | Request shutdown of the command loop                    |
| `pause`              | Mark the scanner as paused                              |
| `resume`             | Resume scanner operation                                |
| `export_wl`          | Export the current ThinkOrSwim Watchlist                |
| `replace_wl_symbols` | Replace the symbols in the personal `Default` Watchlist |
| `add_wl_symbols`     | Add symbols to the personal `Default` Watchlist         |

Example commands from MasterBot:

```cmd
mb-scan-command start --wait 10
mb-scan-command export_wl --wait 10
mb-scan-command pause --wait 10
mb-scan-command resume --wait 10
mb-scan-command stop --wait 10
```

Replace the Default Watchlist:

```cmd
mb-scan-command replace_wl_symbols --symbols AAPL MSFT NVDA --wait 10
```

Add symbols:

```cmd
mb-scan-command add_wl_symbols --symbols AMD,ORCL IBM --wait 10
```

Symbol input may be separated by spaces or commas. Symbols are:

* converted to uppercase;
* de-duplicated;
* retained in first-seen order;
* placed on the clipboard as newline-separated text.

The ThinkOrSwim workflow selects the personal `Default` Watchlist, opens **Import**, selects either **Replace** or **Add**, chooses **Paste Symbols**, accepts the dialog, and verifies that the Symbols Import window closes.

### Meaning of `--wait`

`mb-scan-command --wait` waits for the command file to reach either the `processed` or `failed` ingress directory.

A `processed` result confirms that the command was accepted and submitted to the scanner job queue. It does not by itself prove that a longer ThinkOrSwim GUI action has finished. The heartbeat `current_job`, `loop_state`, and `last_job` fields provide additional runtime information.

## Runtime flags

The v2 dispatcher tracks:

```text
running
paused
shutdown_requested
```

A fresh heartbeat can therefore report:

```text
loop_state : idle
running    : true
paused     : false
```

or:

```text
loop_state : paused
running    : true
paused     : true
```

It is also possible to have:

```text
loop_state : idle
running    : false
```

This means the command loop is alive and responsive, but a `start` command has not yet marked the scanner as running.

## Scanner heartbeat

`scan_command_loop.py` publishes:

```text
<command-root>\status\scanner_heartbeat.json
```

The default publication interval is five seconds. Important state transitions are published immediately.

Typical heartbeat fields include:

```json
{
  "application": "ToS_scanner",
  "host": "El-Cheapo",
  "pid": 24168,
  "started_at_utc": "2026-07-26T08:23:19Z",
  "heartbeat_at_utc": "2026-07-26T08:24:34Z",
  "heartbeat_sequence": 20,
  "heartbeat_interval_s": 5.0,
  "loop_state": "idle",
  "running": true,
  "paused": false,
  "shutdown_requested": false,
  "current_job": null,
  "last_job": {
    "kind": "resume",
    "command_id": "mb-resume-example",
    "ok": true,
    "message": "Scanner resumed.",
    "error": null
  }
}
```

The heartbeat file is written to a temporary file and atomically replaced, preventing another computer from reading a partially written JSON document.

### Heartbeat loop states

| Loop state             | Meaning                                                   |
| ---------------------- | --------------------------------------------------------- |
| `waiting_for_operator` | Program started and is waiting for Enter                  |
| `idle`                 | Command loop is polling and not currently executing a job |
| `busy`                 | A command is being executed                               |
| `paused`               | Scanner runtime state is paused                           |
| `stopped`              | Loop exited or shutdown was requested                     |

## Checking status from MasterBot

With `MB_SCAN_CONTROL` set to the El-Cheapo share:

```cmd
mb-scan-status
```

Example healthy result:

```text
Scanner status : HEALTHY
Host           : El-Cheapo
Loop state     : idle
Running        : yes
Paused         : no
Heartbeat age  : 1.2 seconds
```

Example paused result:

```text
Scanner status : PAUSED
Loop state     : paused
Running        : yes
Paused         : yes
```

Example stopped result:

```text
Scanner status : STOPPED
Loop state     : stopped
Running        : no
Paused         : no
```

Raw JSON status output is available with:

```cmd
mb-scan-status --json
```

## V2 output filenames

The v2 filename format is:

```text
YYYY-MM-DD-HH-MM-SS-XX.csv
```

Current source codes include:

```text
TS   ThinkOrSwim scheduled scan
TM   ThinkOrSwim manual scan
WL   ThinkOrSwim Watchlist
SA   Schwab API
MA   Massive API
```

Example Watchlist export:

```text
2026-07-25-00-11-06-WL.csv
```

`TS`, `TM`, and `WL` files are produced by this scanner project. `SA` and `MA` files are expected to be produced elsewhere on the LAN.

## Legacy v1 GUI

The v1 Qt control panel is titled:

```text
JTM Scan Manager
```

Its controls include:

| Control             | Purpose                                                |
| ------------------- | ------------------------------------------------------ |
| Output directory    | Select where CSV files are written                     |
| Apply               | Apply the edited output directory                      |
| Manual init         | Initialize and verify the ThinkOrSwim export directory |
| Unlock ToS Scan     | Clear internal scan gating                             |
| Press ToS Scan      | Press the ThinkOrSwim scan button                      |
| Scan and Export CSV | Run an immediate scan and export                       |
| Start Scan          | Start the scheduled scanner loop                       |
| Stop Scan           | Gracefully stop the scheduled loop                     |
| Exit Scan Manager   | Stop active work and exit the GUI                      |

Legacy hotkeys:

| Hotkey       | Action                         |
| ------------ | ------------------------------ |
| `ESC`        | Stop the active scheduled loop |
| `Ctrl+Alt+E` | Run Scan and Export CSV        |
| `Ctrl+Alt+Q` | Exit Scan Manager              |

The legacy entry point can be run with:

```cmd
python scan_main_v1p2.py --layout-path C:\Users\DanLa\Documents\github\pwidget_layouts\layout_scanner3_v1p1dev2.yaml
```

## Tests and development checks

Run the complete test suite:

```cmd
python -m pytest -q
```

Compile the v2 command-loop components:

```cmd
python -m py_compile scan_command_loop.py scanner_heartbeat.py file_command_ingress.py scan_dispatcher.py tos_scan_action_executor.py tos_pwidget_actions.py
```

Check Git whitespace:

```cmd
git diff --check
```

A useful v2 smoke test is:

1. Start `scan_command_loop.py`.
2. Confirm `mb-scan-status` reports `WAITING`.
3. Press Enter.
4. Confirm status changes to `HEALTHY`.
5. Send `start`.
6. Confirm `Running: yes`.
7. Send `pause`.
8. Confirm status changes to `PAUSED`.
9. Send `resume`.
10. Export a Watchlist.
11. Test Replace and Add Watchlist symbols.
12. Send `stop`.
13. Confirm status changes to `STOPPED`.

## Repository notes

Generated or private files should not be committed:

```text
.env
secure/
runtime/
logs/
__pycache__/
client.log
*.pyc
```

Historical notes and older layout/debug references are stored under:

```text
docs\archive
```

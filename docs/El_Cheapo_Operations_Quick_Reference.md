# El-Cheapo Operations Quick Reference

Day-to-day operator guide for the ThinkOrSwim automation processes in
`ToS_scanner`.

Last verified against branch `scan_main_v2p0dev0` on 2026-09-19.

This document is the El-Cheapo companion to the MasterBot
`mb_market_data` Operations Quick Reference. Commands are labeled by the
machine on which they run. Do not run MasterBot commands on El-Cheapo or
El-Cheapo commands on MasterBot.

## What runs where

| Component | Machine | Purpose |
| --- | --- | --- |
| ThinkOrSwim desktop | El-Cheapo | Owns the scanner and personal `Default` Watchlist GUI |
| `scan_main_v2p0dev0.py` | El-Cheapo | Scheduled Watchlist and scanner exports; JTM Scan Manager |
| `scan_command_loop.py` | El-Cheapo | Receives MasterBot commands, mutates/exports the Watchlist, publishes heartbeat |
| `mb-scan-command` | MasterBot | Sends commands to El-Cheapo |
| `mb-scan-status` | MasterBot | Reads the El-Cheapo command-loop heartbeat |
| OV/Focus production and market-data polling | MasterBot | Builds membership and records observations |

The current proof of concept therefore requires three live applications on
El-Cheapo:

1. ThinkOrSwim.
2. `scan_main_v2p0dev0.py`.
3. `scan_command_loop.py`.

`mb-scan-status` proves that the command loop is alive. It does **not** prove
that `scan_main_v2p0dev0.py` or ThinkOrSwim is alive.

## Time convention

The scanner scheduler uses US Eastern Time (`America/New_York`), regardless of
the operator's location.

| Scheduler time | Minnesota time |
| --- | --- |
| 09:24 ET | 08:24 CT |
| 09:30 ET market open | 08:30 CT |
| 16:00 ET market close | 15:00 CT |
| 16:02 ET scheduler stop | 15:02 CT |

US Eastern and Central time normally change between daylight and standard time
on the same dates, so Minnesota remains one hour behind the scheduler.

Production scheduling is active on weekdays from 09:24:00 ET through, but not
including, 16:02:00 ET. Every minute:

| Second | Export |
| --- | --- |
| `:05` | Watchlist (`WL`) |
| `:20` | Watchlist (`WL`) |
| `:35` | Watchlist (`WL`) |
| `:50` | ThinkOrSwim scanner (`TS`) |

Files are named `YYYY-MM-DD-HH-MM-SS-XX.csv`, where `XX` is `WL`, `TS`, or
`TM` for a manual scanner export.

## Before each trading day

Do this on **El-Cheapo**, early enough to finish before 09:24 ET / 08:24 CT.

### 1. Update the active scanner branch

Open a Command Prompt:

```cmd
cd /d C:\Users\DanLa\Documents\github\ToS_scanner
conda activate sea-green
git status --short
git switch scan_main_v2p0dev0
git pull --ff-only origin scan_main_v2p0dev0
```

Stop if `git status --short` shows an unexpected change that a pull would
overwrite. Preserve operator files; do not delete them merely to make a pull
succeed.

### 2. Start and prepare ThinkOrSwim

1. Start ThinkOrSwim and complete login.
2. Open the expected main scanner window (`Main@thinkorswim`).
3. Open the expected Watchlist window (`Watchlist Main@thinkorswim`).
4. Select the personal `Default` Watchlist.
5. Restore the window sizes and positions expected by the active pseudo-widget
   layout.
6. Keep the JTM Scan Manager and unrelated windows away from ThinkOrSwim menus
   and dialogs used by automation.

The automation validates the main window's presence and dimensions when the
scheduled scanner starts. GUI automation can still fail if a window or dialog
is obscured later.

### 3. Start the scheduled scanner

In the first El-Cheapo Command Prompt:

```cmd
cd /d C:\Users\DanLa\Documents\github\ToS_scanner
conda activate sea-green
python scan_main_v2p0dev0.py
```

In the **JTM Scan Manager**:

1. Leave mode set to **Production**.
2. Confirm the output directory. Its normal configured default is:

   ```text
   C:\Users\DanLa\Documents\github\stockScans
   ```

3. If the output directory was changed, click **Apply**, then **Manual init**,
   and wait for setup to complete before starting.
4. Click **Start Scan**.
5. Confirm **Scan status** shows `Running` during the production window or
   `Wait` before it.

`Manual init` is the export-dialog setup operation. Run it after changing the
output directory, after rebuilding the ThinkOrSwim layout, or while diagnosing
an export-path failure. The scan loop must be stopped while it runs.

Do not use **Debug** for a production day. Debug disables the weekday and
09:24-16:02 ET scheduling gate.

### 4. Start the command loop

In a second El-Cheapo Command Prompt:

```cmd
cd /d C:\Users\DanLa\Documents\github\ToS_scanner
conda activate sea-green
python scan_command_loop.py
```

The normal command root is:

```text
C:\Users\DanLa\Documents\github\stockScans_control
```

To select it explicitly:

```cmd
python scan_command_loop.py --root C:\Users\DanLa\Documents\github\stockScans_control
```

Read the displayed checklist. When both ThinkOrSwim windows are ready, press
Enter at:

```text
Press Enter when ready...
```

Until Enter is pressed, the heartbeat state is `waiting_for_operator`.

### 5. Enable and validate remote control

Run these commands on **MasterBot**, not El-Cheapo:

```cmd
mb-scan-command start --wait 10
mb-scan-status
```

A normal result has:

```text
Scanner status    : HEALTHY
Loop state        : idle
Running           : yes
Paused            : no
Exports suspended : no
State health      : NORMAL
```

Then independently confirm on El-Cheapo that ThinkOrSwim, the JTM Scan Manager,
and both Python console windows are present.

## Fast health checks

### El-Cheapo: confirm both Python processes

```cmd
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match 'scan_main_v2p0dev0.py|scan_command_loop.py'} | Select-Object ProcessId,CommandLine | Format-Table -AutoSize"
```

Expect one row for each script. This is a process check only; also inspect the
JTM Scan Manager and ThinkOrSwim windows.

### El-Cheapo: inspect recent CSV output

```cmd
powershell -NoProfile -Command "Get-ChildItem 'C:\Users\DanLa\Documents\github\stockScans\*.csv' | Sort-Object LastWriteTime -Descending | Select-Object -First 12 Name,Length,LastWriteTime | Format-Table -AutoSize"
```

During the production window, expect three `WL` files and one `TS` file per
minute, subject to a deliberate export suspension or a recorded failure.

Zero-byte, stale, missing, or irregularly timed files require investigation.

### El-Cheapo: inspect scanner state and logs

The scheduled scanner's default local state and logs are:

```text
runtime\scan_runner_state.json
logs\
```

Useful commands:

```cmd
type runtime\scan_runner_state.json
dir /o-d logs
```

The state file describes the scheduled scanner. The command-loop heartbeat is
separate under the scanner-control root.

### MasterBot: inspect command-loop status

```cmd
mb-scan-status
mb-scan-status --json
```

Common loop states:

| State | Meaning |
| --- | --- |
| `waiting_for_operator` | El-Cheapo command loop is open but Enter has not been pressed |
| `idle` | Ready for commands |
| `busy` | Executing a command |
| `paused` | Logical command-loop state is paused |
| `exports_suspended` | Scheduled exports are deliberately blocked |
| `stopped` | Command loop exited or was told to stop |

`state_health=WARNING` or `DEGRADED` can mean export suspension has lasted too
long even though the heartbeat itself is current.

## Routine operation

Once started, leave the two El-Cheapo Python processes running and leave the
required ThinkOrSwim windows available to automation.

The JTM Scan Manager hotkeys are:

| Key | Action |
| --- | --- |
| `Esc` | Stop the scheduled scan loop |
| `Ctrl+Alt+E` | Manual scan and CSV export |
| `Ctrl+Alt+Q` | Exit the JTM Scan Manager |

Avoid manual GUI work near a scheduled slot. A **Press ToS Scan** request made
less than seven seconds before the next export is ignored. **Scan and Export
CSV** requires the scheduled loop to be stopped.

MasterBot sends the following commands; they are listed here so the El-Cheapo
operator can interpret console activity:

| MasterBot command | El-Cheapo effect |
| --- | --- |
| `start` | Marks the command-loop runtime as running |
| `stop` | Shuts down the command loop |
| `pause` / `resume` | Changes command-loop logical state |
| `export_wl` | Performs an explicit Watchlist export |
| `suspend_exports` | Blocks scheduled `WL` and `TS` exports |
| `resume_exports` | Re-enables scheduled exports |
| `replace_wl_symbols` | Replaces the personal `Default` Watchlist membership |
| `add_wl_symbols` | Adds symbols to the personal `Default` Watchlist |

`mb-scan-command --wait` means the command file was processed. It is not, by
itself, proof that ThinkOrSwim reached the requested final state. Explicit
Watchlist export and full-target comparison are authoritative.

## Protected Watchlist update

The normal BASE_SET/Focus update is coordinated from MasterBot. The intended
sequence is:

1. MasterBot suspends scheduled exports.
2. El-Cheapo replaces or adds Watchlist symbols through ThinkOrSwim.
3. El-Cheapo performs an explicit verification export.
4. MasterBot compares the complete observed set with the complete target.
5. MasterBot resumes scheduled exports.

During this sequence, do not click in ThinkOrSwim or cover its dialogs. Watch
the El-Cheapo command-loop console for the job result, but treat the resulting
verification evidence as the final authority.

Export suspension is persisted. If either Python process restarts while the
gate is suspended, scheduled exports remain blocked until MasterBot sends:

```cmd
mb-scan-command resume_exports --wait 10
```

Never resume exports merely to clear a warning if a Watchlist transaction is
still in progress. First determine whether the protected transaction completed
or failed.

## Normal shutdown

### 1. Confirm the export gate is open

On MasterBot:

```cmd
mb-scan-status
```

For a routine shutdown, confirm `Exports suspended: no`. If it is `yes`, first
resolve the protected Watchlist operation; do not blindly clear it.

### 2. Stop the scheduled scanner on El-Cheapo

In JTM Scan Manager:

1. Click **Stop Scan** or press `Esc`.
2. Wait for **Scan status** to show `Stopped`.
3. Click **Exit Scan Manager** or press `Ctrl+Alt+Q`.

### 3. Stop the command loop

Preferred from MasterBot:

```cmd
mb-scan-command stop --wait 10
mb-scan-status
```

Alternatively, press `Ctrl+C` in the El-Cheapo command-loop console. Confirm
the console reports that the v2 command loop stopped.

### 4. Close ThinkOrSwim

Close ThinkOrSwim only after the final scheduled/export evidence has been
written and both automation processes have stopped.

## Recovery guide

### `mb-scan-status` is stale or unavailable

1. On El-Cheapo, inspect the `scan_command_loop.py` console.
2. If it is absent or stopped, restart it and press Enter after checking the
   ThinkOrSwim windows.
3. On MasterBot, send `mb-scan-command start --wait 10` and check status again.
4. Check whether persisted export suspension was restored.

### Status is healthy but scheduled files stopped

The command-loop heartbeat does not cover the scheduled-scanner process.

1. Check whether `scan_main_v2p0dev0.py` is running.
2. Inspect JTM Scan Manager status, warnings, and errors.
3. Check whether exports are suspended.
4. Inspect `runtime\scan_runner_state.json` and the newest log.
5. If the scheduled scanner died, restart it, confirm **Production**, and click
   **Start Scan**.

### ThinkOrSwim dialog was covered or automation clicked the wrong place

1. Stop manual interaction immediately.
2. Preserve the failed command result and any partial evidence.
3. Clear or reposition the obstructing window.
4. Restore the expected ThinkOrSwim windows and geometry.
5. Repeat the controlled operation from MasterBot.
6. Require a new explicit export and full-target verification.

Do not infer success from a processed command or from a partially changed
Watchlist.

### Output path is wrong or files are missing

1. Stop the scheduled loop.
2. Confirm the JTM Scan Manager output directory.
3. Click **Apply** if it changed.
4. Run **Manual init** and wait for completion.
5. Use **Scan and Export CSV** for one controlled test.
6. Confirm a new `TM` file exists in the intended directory.
7. Return to **Production** and restart the scheduled loop.

### Exports remain suspended unexpectedly

1. Use `mb-scan-status --json` on MasterBot to inspect the suspension age and
   command identifier.
2. Determine whether a Watchlist coordinator run is still active.
3. If no protected transaction remains, send
   `mb-scan-command resume_exports --wait 10` from MasterBot.
4. Confirm `Exports suspended: no` and `State health: NORMAL`.
5. Confirm new scheduled CSV files appear.

## Change validation

After changing El-Cheapo scanner code, run on El-Cheapo from the repository:

```cmd
python -m pytest -q --ignore=docs\archive
```

Then perform a controlled GUI smoke test outside a critical scheduled slot:

1. Start ThinkOrSwim and both Python processes.
2. Confirm the heartbeat.
3. Test an explicit Watchlist export.
4. Test a controlled ADD.
5. Test a controlled REPLACE.
6. Confirm full-target verification.
7. Confirm scheduled exports resume and the command loop returns to `idle`.

## Configuration and security

Configuration is loaded through `mb_tools` using project `.env`, Windows
environment variables, and package defaults. Important settings include:

```text
MB_PWIDGET_YAML
MB_SCANS
MB_LAN_SCANS
MB_LOG_FOLDER
MB_SCAN_CONTROL
MB_WINDOW_TOS*
MB_WINDOW_TOS_WL*
```

Do not place passwords, API tokens, brokerage credentials, or Pushover keys in
tracked scripts, `.cmd` files, logs, command JSON, or documentation. Use the
encrypted configuration supported by the scanner or an untracked local
environment file.

The tracked `set_env.cmd` requires remediation because it currently contains
plaintext credential material. Do not copy those values into new files. Rotate
the affected credentials and remove the material from the tracked tree and Git
history as a separate security operation.

## Known proof-of-concept limitations

- ThinkOrSwim is controlled through screen coordinates and GUI state.
- The scheduled scanner and command loop are separate processes.
- No single heartbeat proves that all three El-Cheapo components are healthy.
- GUI actions can fail when windows move, resize, or obscure a target dialog.
- Command acceptance is not the same as successful final-state verification.
- Some startup, recovery, and shutdown work remains operator-driven.

Update this quick reference whenever an operator command, path, startup step,
schedule, status meaning, or recovery procedure changes.

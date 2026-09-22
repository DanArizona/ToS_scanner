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
| `sync_csv_v2.exe` | El-Cheapo | Independently copies completed `TS`, `TM`, and `WL` CSVs to MasterBot |
| `archive_scans.py` | El-Cheapo and MasterBot | Safely moves completed scan files into date-organized archive trees |
| `mb-scan-command` | MasterBot | Sends commands to El-Cheapo |
| `mb-scan-status` | MasterBot | Reads the El-Cheapo command-loop heartbeat |
| OV/Focus production and market-data polling | MasterBot | Builds membership and records observations |

Normal production operation therefore requires four live applications on
El-Cheapo:

1. ThinkOrSwim.
2. `scan_main_v2p0dev0.py`.
3. `scan_command_loop.py`.
4. `sync_csv_v2.exe`, normally started by
   `sync_scans_to_masterbot_v2.cmd`.

`mb-scan-status` proves that the command loop is alive. It does **not** prove
that `scan_main_v2p0dev0.py`, `sync_csv_v2.exe`, or ThinkOrSwim is alive.

`archive_scans.py` is an end-of-day command, not a process left running during
the market session.

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

## One-time auxiliary utility setup

If the transport and archive repositories are not yet present on El-Cheapo:

```cmd
cd /d C:\Users\DanLa\Documents\github
git clone https://github.com/DanArizona/mb_synccsv.git
git clone https://github.com/DanArizona/mb_archive_scans.git
```

Build the transport executable once:

```cmd
cd /d C:\Users\DanLa\Documents\github\mb_synccsv
gcc -O2 -Wall -Wextra -municode -mconsole sync_csv_v2_20260830.c -o sync_csv_v2.exe
```

Create the selected El-Cheapo archive root:

```cmd
mkdir C:\Users\DanLa\Documents\github\stockScans_archive
```

The executable, logs, stop file, active scan CSVs, and archive data are local
runtime artifacts. Do not commit them to a source repository.

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
2. Select the personal ThinkOrSwim Setup named **Scanner3**.
3. Confirm that `MB_PWIDGET_YAML` resolves to the pseudo-widget layout intended
   for Setup **Scanner3**. Do not start automation when the selected Setup and
   layout do not match.
4. Open the expected main scanner window (`Main@thinkorswim`).
5. Open the expected Watchlist window (`Watchlist Main@thinkorswim`).
6. Select the personal `Default` Watchlist.
7. Restore the window sizes and positions expected by the active pseudo-widget
   layout.
8. Keep the JTM Scan Manager and unrelated windows away from ThinkOrSwim menus
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

### 5. Start live CSV transport

In a third El-Cheapo Command Prompt:

```cmd
cd /d C:\Users\DanLa\Documents\github\mb_synccsv
sync_scans_to_masterbot_v2.cmd
```

The production transport copies completed ordinary scan files from:

```text
C:\Users\DanLa\Documents\github\stockScans
```

to:

```text
\\MasterBot\SCANS
```

It polls every five seconds, retries network outages, verifies copied bytes,
never overwrites a different destination file, and never deletes or moves the
El-Cheapo source file.

The normal filter accepts only `TS`, `TM`, and `WL` filenames. Do not add
`--all-csv` to the production launcher.

If `sync_csv_v2.exe` is missing, build it once from the repository source using
MinGW-w64:

```cmd
cd /d C:\Users\DanLa\Documents\github\mb_synccsv
gcc -O2 -Wall -Wextra -municode -mconsole sync_csv_v2_20260830.c -o sync_csv_v2.exe
```

The source filename is `sync_csv_v2_20260830.c`; it is not the command used for
normal daily startup.

### 6. Enable and validate remote control

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
both Python console windows, and the CSV transport console are present.

## Fast health checks

### El-Cheapo: confirm all automation processes

```cmd
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object {($_.Name -match 'python|sync_csv_v2') -and ($_.CommandLine -match 'scan_main_v2p0dev0.py|scan_command_loop.py|sync_csv_v2.exe')} | Select-Object ProcessId,Name,CommandLine | Format-Table -AutoSize"
```

Expect one row for each of the two Python scripts and one for
`sync_csv_v2.exe`. This is a process check only; also inspect the JTM Scan
Manager and ThinkOrSwim windows.

### El-Cheapo: inspect recent CSV output

```cmd
powershell -NoProfile -Command "Get-ChildItem 'C:\Users\DanLa\Documents\github\stockScans\*.csv' | Sort-Object LastWriteTime -Descending | Select-Object -First 12 Name,Length,LastWriteTime | Format-Table -AutoSize"
```

During the production window, expect three `WL` files and one `TS` file per
minute, subject to a deliberate export suspension or a recorded failure.

Zero-byte, stale, missing, or irregularly timed files require investigation.

### El-Cheapo: inspect CSV transport

The transport log is:

```text
C:\Users\DanLa\Documents\github\mb_synccsv\sync_csv_v2.log
```

Inspect the newest entries:

```cmd
powershell -NoProfile -Command "Get-Content 'C:\Users\DanLa\Documents\github\mb_synccsv\sync_csv_v2.log' -Tail 30"
```

Inspect the newest files visible on MasterBot:

```cmd
powershell -NoProfile -Command "Get-ChildItem '\\MasterBot\SCANS\*.csv' | Sort-Object LastWriteTime -Descending | Select-Object -First 12 Name,Length,LastWriteTime | Format-Table -AutoSize"
```

An unreachable destination should cause retries, not stop local scanner
production. A reported content conflict requires investigation; the transport
will not overwrite the destination.

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

Once started, leave the two El-Cheapo Python processes and the CSV transport
worker running, and leave the required ThinkOrSwim windows available to
automation.

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
Watchlist export and full-target comparison apply only to retained diagnostic
workflows.

## Production display-only operation

Run only the command loop for normal production display publication:

```cmd
cd /d C:\Users\DanLa\Documents\github\ToS_scanner
python scan_command_loop.py --display-only
```

Then use MasterBot to mark the command-loop runtime as running. MasterBot sends
complete Focus snapshots with `replace_wl_symbols`. ThinkOrSwim is not read
back, and no Watchlist or scanner CSV is exported or ingested.

Display-only mode persistently blocks scheduled exports and rejects
`export_wl`, scanner exports, `add_wl_symbols`, and `resume_exports`. Do not
start `scan_main_v2p0dev0.py` during routine display-only operation. Start the
older scanner/export process only for a deliberate diagnostic session outside
display-only mode.

The console result means the GUI submission sequence returned without raising
an error. Membership is explicitly unverified. A stale or extra ToS symbol is
a display defect and does not alter MasterBot's canonical hierarchy or API
journal.

## Legacy protected Watchlist update

This procedure is retained only for troubleshooting ThinkOrSwim. It is not a
production startup or data-collection requirement.

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

### 4. Confirm the final CSV reached MasterBot

Compare the newest local and destination files:

```cmd
powershell -NoProfile -Command "$a=Get-ChildItem 'C:\Users\DanLa\Documents\github\stockScans\*.csv' | Sort-Object LastWriteTime -Descending | Select-Object -First 1; $b=Get-ChildItem '\\MasterBot\SCANS\*.csv' | Sort-Object LastWriteTime -Descending | Select-Object -First 1; 'LOCAL'; $a | Format-List Name,Length,LastWriteTime; 'MASTERBOT'; $b | Format-List Name,Length,LastWriteTime"
```

The latest filenames and lengths should agree. If they do not, leave the
transport running and inspect `sync_csv_v2.log`.

### 5. Stop live CSV transport

In any El-Cheapo Command Prompt:

```cmd
cd /d C:\Users\DanLa\Documents\github\mb_synccsv
sync_csv_v2_stop.cmd
```

This creates the worker's stop file. Wait for the transport console to report
its exit code. Do not close the console merely because the stop file was
written; shutdown may wait for an active scan, copy, verification, or network
operation.

### 6. Close ThinkOrSwim

Close ThinkOrSwim only after the final scheduled/export evidence has been
written, copied to MasterBot, and all three automation processes have stopped.

## End-of-day El-Cheapo archival

Run archival only after the scanner, command loop, and live CSV transport have
stopped and the final MasterBot copy has been verified.

The fixed El-Cheapo locations are:

```text
Source       C:\Users\DanLa\Documents\github\stockScans
Archive root C:\Users\DanLa\Documents\github\stockScans_archive
```

The archive tree is organized as:

```text
C:\Users\DanLa\Documents\github\stockScans_archive\YYYY\MM\DD\
```

Use the market-session date explicitly. For example, first dry-run the
2026-09-18 session:

```cmd
cd /d C:\Users\DanLa\Documents\github\mb_archive_scans
conda activate sea-green
python archive_scans.py --source C:\Users\DanLa\Documents\github\stockScans --archive-root C:\Users\DanLa\Documents\github\stockScans_archive --date 2026-09-18
```

Review every reported `WOULD_MOVE`, `DUPLICATE`, and `CONFLICT`. If the dry-run
is correct and reports no conflict, perform the move by adding `--move`:

```cmd
python archive_scans.py --source C:\Users\DanLa\Documents\github\stockScans --archive-root C:\Users\DanLa\Documents\github\stockScans_archive --date 2026-09-18 --move
```

Change `2026-09-18` to the session being archived. Prefer `--date` over
`--today`; the explicit date is reproducible and cannot silently select the
wrong session after midnight.

Safety behavior:

- Dry-run is the default; no source file moves without `--move`.
- A conflicting destination aborts the complete selected move batch before
  any selected source is changed.
- New destinations are copied to staging, SHA-256 verified, atomically renamed,
  verified again, and only then removed from the active source directory.
- An identical existing destination is treated as a duplicate; in move mode,
  the redundant source is removed after equality is confirmed.
- Only top-level files matching `YYYY-MM-DD-HH-MM-SS-??.csv` are eligible.
- Never place the archive root inside the active `stockScans` source directory.

MasterBot should run a separate archive operation against its own `MB_SCANS`
directory after its archive root is standardized. Do not use the El-Cheapo
archive directory as MasterBot's archive root.

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

### Local CSVs exist but MasterBot copies are stale

1. Confirm `sync_csv_v2.exe` is running.
2. Inspect the last 30 lines of `sync_csv_v2.log`.
3. Confirm `\\MasterBot\SCANS` is reachable from El-Cheapo.
4. Leave the source files in `stockScans`; do not move or delete them while the
   transport is recovering.
5. Allow the worker to retry. It treats an identical destination as delivered
   and refuses to overwrite a different destination.
6. If a conflict is reported, compare the two files before taking further
   action.

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

Validate the live transport separately in `mb_synccsv`:

```cmd
cd /d C:\Users\DanLa\Documents\github\mb_synccsv
sync_csv_v2.exe --help
```

Validate archival separately in `mb_archive_scans`:

```cmd
cd /d C:\Users\DanLa\Documents\github\mb_archive_scans
python -m pytest -q
```

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

Until active Pushover credentials are available, leave notifications disabled:

```text
MB_NOTIFY_ENABLE=false
```

No dummy credential is required for normal scanner operation. If encrypted
configuration loading itself needs to be tested, use dummy values such as
`XXXX` only inside an untracked encrypted `.ecfg` file.

The former tracked `set_env.cmd` contained plaintext credential material. It
has been removed from the current tree and its filename is ignored. The old
values must not be copied into a replacement file. History containing the
removed file must be rewritten separately, after which existing clones will
need controlled resynchronization.

## Known proof-of-concept limitations

- ThinkOrSwim is controlled through screen coordinates and GUI state.
- The scheduled scanner and command loop are separate processes.
- No single heartbeat proves that all four live El-Cheapo components are healthy.
- GUI actions can fail when windows move, resize, or obscure a target dialog.
- Command acceptance is not the same as successful final-state verification.
- Some startup, recovery, and shutdown work remains operator-driven.

Update this quick reference whenever an operator command, path, startup step,
schedule, status meaning, or recovery procedure changes.

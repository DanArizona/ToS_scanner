# ToS_scanner

ThinkOrSwim GUI automation, scheduled CSV export, and remote Watchlist-control services for the MasterBot project.

> **Development status:** Active proof of concept on branch `scan_main_v2p0dev0`.
>
> The current POC uses two El-Cheapo Python processes:
>
> * `scan_main_v2p0dev0.py` — the scanner/control-panel application and scheduled export process;
> * `scan_command_loop.py` — the MasterBot command/control sidecar.
>
> ThinkOrSwim itself must also be running with the expected scanner and Watchlist windows.

## Purpose

`ToS_scanner` is the El-Cheapo-side ThinkOrSwim automation layer.

It performs GUI-driven operations that currently cannot be delegated directly to an API, including:

* scheduled ThinkOrSwim scanner exports;
* scheduled Watchlist exports;
* explicit Watchlist exports requested by MasterBot;
* replacement of Watchlist membership;
* addition of Watchlist symbols;
* temporary suspension of scheduled exports while MasterBot performs protected Watchlist reconciliation.

The project is one component of a larger MasterBot architecture.

Current high-level flow:

```text
MasterBot
   |
   |  \\El-Cheapo\SCANCTRL
   v
scan_command_loop.py
   |
   v
ThinkOrSwim GUI

scan_main_v2p0dev0.py
   |
   +--> scheduled WL exports
   |
   +--> scheduled scanner exports
   |
   v
ThinkOrSwim GUI
```

For the current proof of concept, these remain separate processes.

Long term, if the architecture proves successful, their responsibilities are expected to be merged into one El-Cheapo scanner service/process.

---

# Current POC operating model

## El-Cheapo

El-Cheapo currently runs:

```text
ThinkOrSwim
scan_main_v2p0dev0.py
scan_command_loop.py
```

The two Python programs have distinct responsibilities.

### `scan_main_v2p0dev0.py`

Current v2 scanner/control-panel entry point:

```cmd
python scan_main_v2p0dev0.py
```

This process creates the Qt scanner application and control panel and owns the scheduled scanner/export behavior.

Its responsibilities include:

* scanner control GUI;
* scheduled ThinkOrSwim scanner exports;
* scheduled Watchlist exports;
* runtime scanner state used by the GUI application;
* output-file generation through ThinkOrSwim automation.

### `scan_command_loop.py`

Remote command/control entry point:

```cmd
python scan_command_loop.py
```

This process:

* watches the scanner-control directory for MasterBot commands;
* dispatches explicit ThinkOrSwim actions;
* handles Watchlist ADD and REPLACE requests;
* handles explicit Watchlist exports;
* implements the scheduled-export suspension gate;
* publishes the scanner-command heartbeat consumed by `mb-scan-status`.

The two processes are separate for historical/development reasons.

For the current POC this is accepted operationally rather than adding temporary lifecycle infrastructure.

---

# Important status limitation

The heartbeat published under the scanner command root belongs to:

```text
scan_command_loop.py
```

Therefore:

```cmd
mb-scan-status
```

primarily tells MasterBot that the **command-loop process** is alive and reports its logical runtime flags.

For example:

```text
Scanner status : HEALTHY
Loop state     : idle
Running        : yes
Paused         : no
```

does **not** independently prove that:

```text
scan_main_v2p0dev0.py
```

is running.

In the current POC, the operator manually ensures both El-Cheapo processes are running.

Do not interpret:

```text
Running : yes
```

as process-level proof that `scan_main_v2p0dev0.py` is alive.

This limitation is intentionally deferred because the long-term design is expected to merge the command loop and scanner application rather than add permanent coordination machinery between two processes that may eventually disappear.

---

# Long-term El-Cheapo direction

If the MasterBot coordinator architecture proves successful, the preferred long-term architecture is:

```text
scan_main_v2p0dev0.py
        +
scan_command_loop.py
        |
        v
one scanner service/process
```

Desired properties:

* one process lifecycle;
* one authoritative heartbeat/status;
* one GUI-action arbiter;
* one scheduled-export scheduler;
* one remote command interface;
* one shutdown/startup model.

The current two-process architecture should be considered a POC/development arrangement rather than a final production design.

---

# Related projects

`ToS_scanner` works with several MasterBot repositories.

## mb_tools

Provides shared functionality including:

```text
mb-scan-command
mb-scan-status
```

as well as configuration, window-management, pseudo-widget, logging, and secure-configuration utilities.

## mb_watchlist_coordinator

Owns:

* producer intents;
* canonical Watchlist revisions;
* adapter targets;
* reconciliation;
* transactions;
* verification;
* confirmed/observed adapter state.

`ToS_scanner` is a downstream materialization mechanism for that architecture.

## schwab_watchlists

Currently hosts the live POC application layer including:

* Overnight Volume producer;
* Nasdaq LUDP/M producer;
* live ToS coordinator executor;
* protected observation/materialization;
* Watchlist evidence transport;
* `mb-wl-recovery`.

## mb_market_data

Provides reusable market-data acquisition including:

* Nasdaq Trade Halt data;
* Schwab quotes;
* price-history probes;
* ToS decision snapshots;
* future historical Overnight Volume infrastructure.

## pwidget_layouts

Stores shared pseudo-widget YAML layouts used to locate ThinkOrSwim GUI elements.

## ToS_gui_survey

Used to inspect, measure, and validate ThinkOrSwim GUI geometry and pseudo-widget layouts.

---

# Configuration

Scanner configuration follows the `mb_tools` precedence model:

```text
project .env
    >
Windows environment
    >
mb_tools defaults.env
```

Important variables include:

```text
MB_PWIDGET_YAML
MB_SCANS
MB_LAN_SCANS
MB_LOG_FOLDER
MB_SCAN_CONTROL

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

Typical ThinkOrSwim window values include:

```dotenv
MB_WINDOW_TOS_MAIN=Main@thinkorswim
MB_WINDOW_TOS_WL_MAIN=Watchlist Main@thinkorswim
MB_WINDOW_TOS_WL_EXPORT_MATCH=Watchlist '
MB_WINDOW_TOS_WL_SYMBOLS=Symbols Import
```

Configured ThinkOrSwim window names are generally treated as title prefixes.

For example:

```text
Main@thinkorswim
```

may match a changing title such as:

```text
Main@thinkorswim [build 1992]
```

---

# Pseudo-widget layout

The ThinkOrSwim layout is normally selected using:

```text
MB_PWIDGET_YAML
```

A development layout has historically been located under:

```text
C:\Users\DanLa\Documents\github\pwidget_layouts\
```

The exact active YAML file may change during development.

GUI automation depends on:

* expected windows being open;
* expected dimensions;
* expected relative locations;
* correct pseudo-widget definitions.

Use `ToS_gui_survey` and `pwidget_layouts` when GUI geometry needs to be revalidated.

---

# Scanner-control share

The command loop normally uses the local El-Cheapo directory:

```text
C:\Users\DanLa\Documents\github\stockScans_control
```

MasterBot accesses that directory through:

```text
\\El-Cheapo\SCANCTRL
```

On MasterBot:

```text
MB_SCAN_CONTROL=\\El-Cheapo\SCANCTRL
```

The command root is resolved in this order:

1. explicit `--root`;
2. `MB_SCAN_CONTROL`;
3. local El-Cheapo development default.

Example:

```cmd
python scan_command_loop.py --root C:\Users\DanLa\Documents\github\stockScans_control
```

---

# Starting the current POC

## 1. Start ThinkOrSwim

Open the expected:

* Main scanner window;
* Watchlist window.

Position them according to the active pseudo-widget layout.

## 2. Start the scanner application

From the `ToS_scanner` repository:

```cmd
python scan_main_v2p0dev0.py
```

## 3. Start the command loop

In a second El-Cheapo console:

```cmd
python scan_command_loop.py
```

The command loop displays an operator checklist and may wait at:

```text
Press Enter when ready...
```

During this period its heartbeat reports:

```text
waiting_for_operator
```

Press Enter after the ThinkOrSwim windows are ready.

## 4. Mark the command-loop runtime as running

From MasterBot:

```cmd
mb-scan-command start --wait 10
```

Then:

```cmd
mb-scan-status
```

A normal result resembles:

```text
Scanner status : HEALTHY
Loop state     : idle
Running        : yes
Paused         : no
Exports suspended: no
State health   : NORMAL
```

Remember that this verifies the command-loop heartbeat, not the independent `scan_main_v2p0dev0.py` process.

---

# Supported MasterBot commands

Current command/control operations include:

| Command              | Purpose                                                |
| -------------------- | ------------------------------------------------------ |
| `start`              | Mark command-loop scanner state as running             |
| `stop`               | Request shutdown of the command loop                   |
| `pause`              | Mark runtime paused                                    |
| `resume`             | Resume runtime                                         |
| `export_wl`          | Explicitly export the current ThinkOrSwim Watchlist    |
| `suspend_exports`    | Block scheduled WL/scan exports                        |
| `resume_exports`     | Re-enable scheduled exports                            |
| `replace_wl_symbols` | Replace membership of the personal `Default` Watchlist |
| `add_wl_symbols`     | Add symbols to the personal `Default` Watchlist        |

Examples:

```cmd
mb-scan-command start --wait 10
```

```cmd
mb-scan-command suspend_exports --wait 10
```

```cmd
mb-scan-command resume_exports --wait 10
```

```cmd
mb-scan-command export_wl --wait 10
```

Replace Watchlist membership:

```cmd
mb-scan-command replace_wl_symbols --symbols AAPL MSFT NVDA --wait 30
```

Add symbols:

```cmd
mb-scan-command add_wl_symbols --symbols AMD ORCL IBM --wait 30
```

Symbol input may be separated by spaces or commas.

The command path normalizes and de-duplicates symbols before placing newline-separated symbols on the clipboard for ThinkOrSwim import.

---

# Watchlist mutation

ThinkOrSwim Watchlist mutation uses the personal:

```text
Default
```

Watchlist.

The automated workflow opens the ThinkOrSwim Watchlist Import dialog and performs either:

```text
Replace
```

or:

```text
Add
```

using symbols placed on the Windows clipboard.

The current coordinator architecture decides whether a downstream operation should be ADD or REPLACE.

Producers such as Overnight Volume and Nasdaq LUDP/M do not directly make that decision.

---

# Known GUI hazard

A critical GUI failure mode has been observed during Watchlist import.

The scanner/control GUI can remain above ThinkOrSwim and partially cover the:

```text
Symbols Import
```

dialog.

In that condition, an automated click may hit the scanner window instead of the intended ThinkOrSwim control.

This can result in a command appearing to progress through part of the dialog without actually applying the intended Watchlist mutation.

For this reason:

* GUI visibility remains important;
* Watchlist mutation is followed by explicit observation;
* coordinator-driven workflows use full-target verification rather than trusting command acceptance alone.

---

# Scheduled exports

Scheduled export timing is evaluated in New York market time.

Current schedule:

```text
:05  Watchlist export (WL)
:20  Watchlist export (WL)
:35  Watchlist export (WL)
:50  ThinkOrSwim scanner export (TS)
```

Example one-minute sequence:

```text
...-05-WL.csv
...-20-WL.csv
...-35-WL.csv
...-50-TS.csv
```

Current source codes include:

```text
TS   ThinkOrSwim scheduled scan
TM   ThinkOrSwim manual scan
WL   ThinkOrSwim Watchlist
SA   Schwab API
MA   Massive API
```

`TS`, `TM`, and `WL` are produced by `ToS_scanner`.

`SA` and `MA` are expected to be produced by other MasterBot/LAN components.

---

# V2 filenames

Current format:

```text
YYYY-MM-DD-HH-MM-SS-XX.csv
```

Example:

```text
2026-08-27-09-30-05-WL.csv
```

Times are intended to represent Eastern/New York market time for market-session workflows.

---

# Export suspension

MasterBot can temporarily prevent scheduled ThinkOrSwim exports:

```cmd
mb-scan-command suspend_exports --wait 10
```

Resume them with:

```cmd
mb-scan-command resume_exports --wait 10
```

This is distinct from:

```text
pause
```

`pause` changes command-loop runtime state.

`suspend_exports` is specifically an export-collision control.

The main use is protected Watchlist reconciliation:

```text
suspend scheduled exports
        |
        v
perform explicit Watchlist action
        |
        v
perform explicit verification export
        |
        v
resume scheduled exports
```

This prevents a scheduled `WL` or `TS` export from colliding with coordinator-driven ThinkOrSwim GUI work.

Watchlist mutation and the coordinator’s explicit verification flow remain available as controlled operations during protected reconciliation.

---

# Persisted export gate

Export suspension is persisted under the scanner-control root.

If the command loop exits while exports are suspended, a restarted command loop restores the suspended condition.

This is fail-safe behavior.

Exports remain suspended until MasterBot explicitly sends:

```cmd
mb-scan-command resume_exports --wait 10
```

The heartbeat includes:

```text
exports_suspended
exports_suspended_since_utc
suspension_age_seconds
suspension_command_id
state_health
```

Long-lived suspension is reported separately from ordinary command-loop liveness.

Current state-health concepts include:

```text
NORMAL
WARNING
DEGRADED
```

This helps identify cases where the scanner-control process is alive but exports have remained suspended unexpectedly.

---

# Protected Watchlist observation

The MasterBot coordinator uses explicit Watchlist observation to determine what ThinkOrSwim actually contains.

A typical protected observation is:

```text
MasterBot
   |
   v
suspend scheduled exports
   |
   v
explicit export_wl
   |
   v
El-Cheapo local CSV
   |
   v
resume scheduled exports
   |
   v
evidence transport
   |
   v
MasterBot parses observed symbols
```

This is deliberately stronger than assuming that a prior ADD or REPLACE command worked.

---

# Full-target verification

The coordinator does not consider a mutation successful simply because:

```text
mb-scan-command
```

reports the command as processed.

Instead, ThinkOrSwim is explicitly exported after the mutation and the complete observed symbol set is compared with the complete target.

Verification checks:

```text
missing symbols
unexpected symbols
```

Example:

```text
Desired    : 759
Observed   : 760
Missing    : 0
Unexpected : 1
```

is considered a verification mismatch even though all requested symbols were present.

This strict behavior is intentional.

---

# Command acceptance versus GUI completion

`mb-scan-command --wait` waits for command-file processing.

A result such as:

```text
Result : processed
```

means the command was accepted by the El-Cheapo command machinery.

It should not by itself be interpreted as proof that every downstream GUI effect has been successfully verified.

For coordinator workflows, verification evidence is authoritative.

---

# Watchlist verification evidence

Coordinator-targeted Watchlist exports use a dedicated evidence path.

The intended separation is:

```text
ThinkOrSwim GUI work
        |
        v
local El-Cheapo export
        |
        v
local verification staging
        |
        v
release ThinkOrSwim
        |
        v
LAN transport to MasterBot
```

This keeps LAN transport out of the GUI-critical section.

Typical paths:

## Local ThinkOrSwim export

```text
C:\Users\DanLa\Documents\github\stockScans\<file>.csv
```

## Local scanner-control verification outbox

```text
C:\Users\DanLa\Documents\github\stockScans_control\
    outgoing\watchlist_verify\<file>.csv
```

## MasterBot view

```text
\\El-Cheapo\SCANCTRL\outgoing\watchlist_verify\<file>.csv
```

## Final MasterBot evidence

```text
%MB_SCANS%\watchlist_verify\<file>.csv
```

Transport and backlog recovery are currently implemented on the MasterBot side by `schwab_watchlists`.

---

# Evidence recovery

`ToS_scanner` stages verification evidence locally.

The companion MasterBot application can run:

```cmd
mb-wl-recovery
```

to recover staged evidence when LAN transport is temporarily unavailable.

This architecture intentionally allows ThinkOrSwim GUI work to finish and scheduled exports to resume even if evidence transport is temporarily degraded.

See the `schwab_watchlists` project for:

```text
tos_watchlist_transport.py
tos_outbox_recovery.py
mb-wl-recovery
```

---

# Command-loop heartbeat

`scan_command_loop.py` publishes:

```text
<command-root>\status\scanner_heartbeat.json
```

Default heartbeat interval is approximately five seconds, with important state transitions published immediately.

Typical fields include:

```text
application
host
pid
started_at_utc
heartbeat_at_utc
heartbeat_sequence
heartbeat_interval_s
loop_state
running
paused
exports_suspended
shutdown_requested
current_job
last_job
state_health
```

Common loop states include:

| State                  | Meaning                                                     |
| ---------------------- | ----------------------------------------------------------- |
| `waiting_for_operator` | Command loop started but operator has not enabled polling   |
| `idle`                 | Polling and not executing a command                         |
| `busy`                 | Executing a command                                         |
| `paused`               | Logical command-loop scanner state is paused                |
| `exports_suspended`    | Command loop alive; scheduled exports intentionally blocked |
| `stopped`              | Shutdown requested or loop exited                           |

---

# Checking command-loop status

On MasterBot:

```cmd
mb-scan-status
```

Example:

```text
Scanner status : HEALTHY
Detail         : Scanner heartbeat is current.
Host           : El-Cheapo
Loop state     : idle
Running        : yes
Paused         : no
Exports suspended: no
State health   : NORMAL
```

Raw heartbeat JSON:

```cmd
mb-scan-status --json
```

Again: this reports the `scan_command_loop.py` heartbeat.

It is not currently an independent health check of `scan_main_v2p0dev0.py`.

---

# Current Watchlist use in the POC

The active MasterBot POC uses the ThinkOrSwim personal Watchlist as a downstream materialization target.

The current producer architecture is:

```text
Overnight Volume
     |
     +--> BASE_SET
               |
               v
       Canonical Watchlist
               ^
               |
     +--> ENSURE_PRESENT
     |
Nasdaq LUDP/M
               |
               v
         ThinkOrSwim
```

`ToS_scanner` does not own this policy.

It only performs downstream GUI actions requested through the adapter/application layer.

---

# OV_DECISION

For the current proof of concept, the ThinkOrSwim Watchlist can contain the custom column:

```text
OV_DECISION
```

A large candidate Watchlist is exported with current `OV_DECISION` values.

MasterBot then:

* reads those values;
* combines them with live Schwab quote data;
* ranks eligible candidates;
* selects a smaller opening `BASE_SET`;
* reconciles ThinkOrSwim to that target.

Eventually, the intention is to move the richer historical Overnight Volume analytics to MasterBot rather than depend on a ToS custom expression.

That future analytics work belongs primarily in `mb_market_data` and the producer/application layers, not in `ToS_scanner`.

---

# Tests

The active v2 tests should be run while excluding archived historical material:

```cmd
pytest -q --ignore=docs\archive
```

The archived directory contains historical/debug test files that are not part of the current active suite.

Current POC development has used this command as the authoritative active test run.

Useful targeted checks include:

```cmd
python -m py_compile ^
    scan_command_loop.py ^
    scanner_heartbeat.py ^
    file_command_ingress.py ^
    scan_dispatcher.py ^
    tos_scan_action_executor.py ^
    tos_pwidget_actions.py
```

Check whitespace:

```cmd
git diff --check
```

---

# Useful command-loop smoke test

1. Start ThinkOrSwim.
2. Start `scan_main_v2p0dev0.py`.
3. Start `scan_command_loop.py`.
4. Confirm the command-loop heartbeat is current.
5. Send:

```cmd
mb-scan-command start --wait 10
```

6. Check:

```cmd
mb-scan-status
```

7. Test explicit Watchlist observation/export.
8. Test a controlled ADD.
9. Test a controlled REPLACE.
10. Confirm scheduled exports resume.
11. Confirm the command loop returns to `idle`.

---

# Export-gate restart smoke test

1. Start `scan_command_loop.py`.
2. Send:

```cmd
mb-scan-command suspend_exports --wait 10
```

3. Confirm:

```text
Exports suspended: yes
```

4. Stop the command loop without resuming exports.
5. Restart `scan_command_loop.py`.
6. Confirm suspension is restored.
7. Send:

```cmd
mb-scan-command resume_exports --wait 10
```

8. Confirm:

```text
Exports suspended: no
```

This verifies that export suspension survives command-loop restart.

---

# Legacy v1 application

The older entry point remains in the repository:

```cmd
python scan_main_v1p2.py
```

It represents the earlier scanner/control-panel architecture.

Current POC development should use:

```cmd
python scan_main_v2p0dev0.py
```

together with:

```cmd
python scan_command_loop.py
```

unless working specifically on historical v1 behavior.

Archived historical notes and debug material live under:

```text
docs\archive
```

---

# Repository structure

Important current files include:

```text
ToS_scanner/
├── scan_main_v2p0dev0.py
├── scan_command_loop.py
├── config.py
├── control_manager.py
├── control_panel.py
├── export_gate.py
├── file_command_ingress.py
├── scan_dispatcher.py
├── scan_job_queue.py
├── scan_jobs.py
├── scan_output.py
├── scanner_heartbeat.py
├── scanner_logging.py
├── tos_pwidget_actions.py
├── tos_scan_action_executor.py
├── tests/
├── docs/
│   └── archive/
└── README.md
```

Exact supporting-module names may continue to evolve during v2 development.

---

# Current POC limitations

The following are known and accepted for the current proof of concept:

* ThinkOrSwim is controlled through GUI automation;
* GUI geometry must match the pseudo-widget layout;
* `scan_main_v2p0dev0.py` and `scan_command_loop.py` are separate processes;
* `mb-scan-status` reports the command-loop heartbeat rather than both processes;
* Watchlist import can fail if another window obscures the ThinkOrSwim dialog;
* full-target verification is required because GUI command acceptance alone is insufficient;
* normal scheduled export transport and coordinator verification transport serve different purposes;
* some lifecycle and restart handling remain operator-driven;
* the architecture is still changing.

---

# Post-POC upgrade backlog

If the overall Watchlist coordinator concept is successful, important `ToS_scanner` work includes:

## Merge scanner and command-loop processes

Replace:

```text
scan_main_v2p0dev0.py
        +
scan_command_loop.py
```

with one production scanner service.

## One authoritative status model

Status should describe the actual scanner service rather than a command-loop proxy.

## One GUI-action arbiter

Scheduled exports and MasterBot-directed mutations should be serialized through one owner.

## Better GUI collision protection

Explicitly ensure ThinkOrSwim dialogs are visible and foregrounded before critical clicks.

## Scan export consistency

Watchlist exports explicitly set both filename and target directory.

Scanner exports should eventually use similarly explicit destination handling where appropriate.

## Production startup/shutdown

Move from operator-started POC processes toward a deliberate trading-day lifecycle.

These are production-hardening tasks and should not distract from the current OV + LUDP/M proof of concept.

---

# Generated/private files

Do not commit:

```text
.env
secure/
runtime/
logs/
__pycache__/
client.log
*.pyc
```

Review generated scan/export data before adding anything to Git.

---

# Security

This repository should not contain:

* brokerage credentials;
* API secrets;
* passwords;
* token files;
* private account information.

ThinkOrSwim automation should remain isolated from credential-management logic.

---

# Disclaimer

This is an independent personal software project.

It is not affiliated with or endorsed by Charles Schwab, ThinkOrSwim, Nasdaq, or any other market-data/trading provider.

The software is intended for development and experimentation.

It does not provide financial advice and does not place trades.

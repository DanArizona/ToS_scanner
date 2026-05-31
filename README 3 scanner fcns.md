(sea-green) C:\Users\DanLa\Documents\github\thousand_miles\ToS_scanner>python test_widget_tree_v3.py layout_4real8.yaml 
YAML source: C:\Users\DanLa\Documents\github\thousand_miles\ToS_scanner\layout_4real8.yaml
title_map entries: 5
  win_logon -> Logon to thinkorswim
  win_main -> Main@thinkorswim
  win_saver -> Watchlist Scanner
  win_tos -> thinkorswim
  win_update -> thinkorswim updater

Top-level root tree count: 4

Top-level root tree 1: win_updater
----------------------------------
win_updater  (w=377, h=682)

Top-level root tree 2: win_logon
--------------------------------
win_logon  (w=377, h=682)
    ├── ledit_userid  (w=333, h=43)
    └── ledit_pword  (w=334, h=45)

Top-level root tree 3: win_main
-------------------------------
win_main  (w=1190, h=1080)
    ├── btn_main_exit  (w=32, h=16)  ptxt="X"
    └── tab_scan  (w=43, h=20)
        ├── btn_scan  (w=47, h=23)  ptxt="Scan"
        ├── btn_query_menu  (w=34, h=18)
        │   └── pick_load_query  (w=184, h=23)  ptxt="Load scan query"
        │       ├── pick_personal_query  (w=240, h=23)  ptxt="Personal"
        │       │   └── pick_scan50_data  (w=156, h=23)  ptxt="scan050_data"
        │       └── pick_public_query  (w=240, h=23)  ptxt="Public"
        │           └── pick_pct_gainers  (w=237, h=23)  ptxt="% Change Gainers"
        ├── btn_action_menu  (w=34, h=20)
        │   └── pick_export  (w=164, h=23)  ptxt="Export"
        │       └── pick_to_file  (w=152, h=23)  ptxt="To file..."
        └── ocr_MyR_5  (w=56, h=141)

Top-level root tree 4: win_saver
--------------------------------
win_saver  (w=960, h=540)
    ├── ledit_fname  (w=795, h=16)
    ├── btn_save_file  (w=85, h=23)  ptxt="Save"
    └── btn_save_cancel  (w=85, h=23)  ptxt="Cancel"
--------------------------------------------------------------------------------------------------------
The following lists are outlines of various functions that we will need to interact with ThinkOrSwim (ToS).
The interface will be controlled by mouseclicks (in most cases) or text entry into a data field.
For debug purposes, we want to define keystroke combinations that will invoke each function. Shown after # for each function.
Each function requires a series of steps in sequence. 
We will specify a wait time between steps. 
In addition to the specified time, add a small random time (say 5 milli-seconds).
We will specify moves (e.g. moveto center of pseudo-widget btn_query_menu)
In addition to the specified destination, add a small amount of random placement (say up to +/- 3 pixels vertical, +/- 5 pixels horizontal)
At this stage, we won't be checking that the pseudo-widget region contains the expected text (ptxt), but we should anticipate such a requirement in the future.



1. def open_scan_tab:  # <ctrl>+<alt>+1
   bring win_main to the front
   moveto center of tab_scan
   mouseclick
   

2. def load_scan50_query:  # <ctrl>+<alt>+2
   call open_scan_tab
   moveto center of btn_query_menu
   mouseclick
   movetoVH center of pick_load_query
   mouseclick
   movetoHV center of pick_personal_query
   mouseclick
   movetoHV center of pick_scan50_data
   mouseclick


3. def load_pct_gainers_query:  # <ctrl>+<alt>+3
   call open_scan_tab
   moveto center of btn_query_menu
   mouseclick
   movetoVH center of pick_load_query
   mouseclick
   movetoHV center of pick_public_query
   mouseclick
   movetoHV center of pick_pct_gainers


4. def trigger_scan:  # <ctrl>+<alt>+4
   call open_scan_tab
   moveto center of btn_scan
   mouseclick


5. def export_csv_file:  # <ctrl>+<alt>+5
   call open_scan_tab
   moveto center of btn_action_menu
   mouseclick
   movetoVH center of pick_export
   mouseclick
   movetoHV center of pick_to_file
   mouseclick
   check that window win_saver opens


6. def enter_filename:  # <ctrl>+<alt>+6
   bring win_saver to the front
   moveto center of ledit_fname
   mouseclick
   select all
   delete
   enter the name of the csv file
   movetoVH to center of btn_save_file
   mouseclick


7. def confirm_save:  # <ctrl>+<alt>+7
   bring win_saver to the front
   movetoVH to center of btn_save_file
   mouseclick

   
8. def cancel_export:  # <ctrl>+<alt>+8
   bring win_saver to the front
   movetoVH to center of btn_save_cancel
   mouseclick


9. def verify_save:  # <ctrl>+<alt>+9
   check target directory for file
   verify the CSV creation is complete
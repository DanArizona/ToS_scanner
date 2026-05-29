



python test_widget_tree_v3.py layout_4real9.yaml



(sea-green) C:\Users\DanLa\Documents\github\thousand_miles\ToS_scanner>python test_widget_tree_v3.py layout_4real9.yaml<br><br>
YAML source: <br>
&emsp;C:\Users\DanLa\Documents\github\thousand_miles\ToS_scanner\layout_4real9.yaml<br>
```cmd
title_map entries: 5
  win_logon -> Logon to thinkorswim
  win_main -> Main@thinkorswim
  win_saver -> Watchlist Scanner
  win_tos -> thinkorswim
  win_update -> thinkorswim updater
```

Top-level root tree count: 4

Top-level root tree 1: win_updater
----------------------------------
```cmd
win_updater  (w=377, h=682)
```

Top-level root tree 2: win_logon
--------------------------------
```cmd
win_logon  (w=377, h=682)<br>
    ├── ledit_userid  (w=333, h=43)<br>
    └── ledit_pword  (w=334, h=45)
```

Top-level root tree 3: win_main
-------------------------------
```cmd
win_main  (w=1190, h=1080)
    ├── btn_main_exit  (w=32, h=16)  ptxt="X"
    └── tab_scan  (w=43, h=20)  ptxt="Scan"
        ├── btn_stock_hacker  (w=47, h=23)  ptxt="Stock Hacker"
        │   ├── btn_scan  (w=47, h=23)  ptxt="Scan"
        │   └── ocr_MyR_5  (w=56, h=141)
        ├── btn_query_menu  (w=34, h=18)
        │   └── pick_load_query  (w=184, h=23)  ptxt="Load scan query"
        │       ├── pick_personal_query  (w=240, h=23)  ptxt="Personal"
        │       │   └── pick_scan50_data  (w=156, h=23)  ptxt="scan050_data"
        │       └── pick_public_query  (w=240, h=23)  ptxt="Public"
        │           └── pick_pct_gainers  (w=237, h=23)  ptxt="% Change Gainers"
        └── btn_action_menu  (w=34, h=20)
            └── pick_export  (w=164, h=23)  ptxt="Export"
                └── pick_to_file  (w=152, h=23)  ptxt="To file..."
```

Top-level root tree 4: win_saver
--------------------------------
```cmd
win_saver  (w=960, h=540)
    ├── ledit_fname  (w=795, h=16)
    ├── btn_save_file  (w=85, h=23)  ptxt="Save"
    └── btn_save_cancel  (w=85, h=23)  ptxt="Cancel"
```
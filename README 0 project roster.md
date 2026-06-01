The canonical pseudo-widget layout for the scanner is:

    layout_scanner3_v1p0.yaml

Both ToS_scanner and ToS_gui_survey should access this file through
MB_PWIDGET_YAML using the mb_tools configuration precedence model:

    local .env > Windows environment > mb_tools defaults.env


## Latest YAML file: &emsp; layout_4real9.yaml &emsp; 2026-03-20
<br>
<br>

| Script                   | Folder         | Description                                                         |
|:-------------------------|:-------------- |:--------------------------------------------------------------------|
| set_env.cmd              | ToS_scanner    | Sets python environment variables for Pushover notifications        | 
| test_open_windows.py     | ToS_scanner    | Show system info for open windows                                   |
| test_widget_tree_v3.py   | ToS_scanner    | Display yaml pseudo-widget hierarchy tree                           |
|                          |                | ``` python test_widget_tree_v3.py layout_4real9.yaml ```            |
| widget_check.py          | toolkit/ToS_gui_survey | Draws hierarchy bboxes for a specified widget               |
|                          |                | ```python widget_check.py <some_pseudo-widget>```                   |
|                          |                | ```python widget_check.py ``` &emsp; (pops up selection dialog)     |
| test_tos_debug_panel.py  | ToS_scanner    | Presents GUI window facilitating a variety of pseudo-widget actions |
| scan_main_v1p2.py        | ToS_scanner    | The main show: Controls ToS and exports 4 scans per minute          |


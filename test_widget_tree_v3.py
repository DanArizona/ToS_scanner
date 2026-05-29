from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from config import WindowConfig
from layout import load_widget_layout


CHILD_CONTAINER_NAMES = (
    "children",
    "widgets",
    "items",
    "members",
    "stacks",
    "nodes",
)

DIMENSION_CONTAINER_NAMES = (
    "region",
    "bbox",
    "rect",
    "bounds",
    "geometry",
)

TEXT_FIELD_NAMES = (
    "ptxt",
    "text",
    "label_text",
)


def setup_logger(logger_name: str, log_stem: str, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{log_stem}-{datetime.now():%Y-%m-%d}.log"

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        logger.handlers.clear()

    file_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    console_formatter = logging.Formatter("%(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def get_field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def has_field(obj: Any, name: str) -> bool:
    if isinstance(obj, dict):
        return name in obj
    return hasattr(obj, name)


def first_present(obj: Any, names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        if has_field(obj, name):
            value = get_field(obj, name)
            if value is not None:
                return value
    return default


def extract_name(node: Any, fallback: str = "<unnamed>") -> str:
    value = first_present(node, ("name", "title", "id", "key", "label"), default=None)
    if value is None:
        return fallback
    return str(value)


def extract_ptxt(node: Any) -> str:
    value = first_present(node, TEXT_FIELD_NAMES, default="")
    if value is None:
        return ""
    return str(value)


def extract_dimensions(node: Any) -> tuple[str, str]:
    width = first_present(node, ("width", "w"), default=None)
    height = first_present(node, ("height", "h"), default=None)

    if width is not None or height is not None:
        return (
            "?" if width is None else str(width),
            "?" if height is None else str(height),
        )

    for container_name in DIMENSION_CONTAINER_NAMES:
        container = get_field(node, container_name, None)
        if container is None:
            continue

        width = first_present(container, ("width", "w"), default=None)
        height = first_present(container, ("height", "h"), default=None)

        if width is not None or height is not None:
            return (
                "?" if width is None else str(width),
                "?" if height is None else str(height),
            )

    return ("?", "?")


def iter_raw_children(node: Any) -> list[Any]:
    """
    Return child nodes only, without trying to name them yet.
    """
    for field_name in CHILD_CONTAINER_NAMES:
        children = get_field(node, field_name, None)
        if children is None:
            continue

        if isinstance(children, dict):
            return list(children.values())

        if isinstance(children, (list, tuple)):
            return list(children)

    return []


def build_name_registry(layout_obj: Any) -> dict[int, str]:
    """
    Build a best-effort mapping from object identity to widget name.

    This is especially useful when a child object does not expose its own name
    directly during traversal, but the top-level registry returned by
    load_widget_layout(...) does.
    """
    registry: dict[int, str] = {}

    if isinstance(layout_obj, dict):
        for key, value in layout_obj.items():
            registry[id(value)] = extract_name(value, fallback=str(key))

    elif isinstance(layout_obj, (list, tuple)):
        for idx, value in enumerate(layout_obj):
            registry[id(value)] = extract_name(value, fallback=f"[{idx}]")

    else:
        registry[id(layout_obj)] = extract_name(layout_obj, fallback="<root>")

    return registry


def iter_children(node: Any, name_registry: dict[int, str]) -> list[tuple[str, Any]]:
    """
    Return child pseudo-widgets with resolved names.
    """
    results: list[tuple[str, Any]] = []

    for field_name in CHILD_CONTAINER_NAMES:
        children = get_field(node, field_name, None)
        if children is None:
            continue

        if isinstance(children, dict):
            for key, child in children.items():
                child_name = name_registry.get(id(child), extract_name(child, fallback=str(key)))
                results.append((child_name, child))
            return results

        if isinstance(children, (list, tuple)):
            for idx, child in enumerate(children):
                child_name = name_registry.get(
                    id(child),
                    extract_name(child, fallback=f"{field_name}[{idx}]")
                )
                results.append((child_name, child))
            return results

    return results


def normalize_top_level_roots(layout_obj: Any, source_name: str) -> list[tuple[str, Any]]:
    """
    Determine the true top-level root trees.

    If layout_obj is a dict, it is treated as a registry/catalog of all nodes.
    True roots are those nodes that are NOT referenced as children by any other node.
    """
    roots: list[tuple[str, Any]] = []

    if layout_obj is None:
        return roots

    if isinstance(layout_obj, dict):
        all_items = list(layout_obj.items())
        child_ids: set[int] = set()

        for _, node in all_items:
            for child in iter_raw_children(node):
                child_ids.add(id(child))

        for key, node in all_items:
            if id(node) not in child_ids:
                root_name = extract_name(node, fallback=str(key))
                roots.append((root_name, node))

        if not roots:
            for key, node in all_items:
                root_name = extract_name(node, fallback=str(key))
                roots.append((root_name, node))

        return roots

    if isinstance(layout_obj, (list, tuple)):
        for idx, node in enumerate(layout_obj):
            root_name = extract_name(node, fallback=f"{source_name}[{idx}]")
            roots.append((root_name, node))
        return roots

    roots.append((extract_name(layout_obj, fallback=source_name), layout_obj))
    return roots


def format_node_line(node_name: str, node: Any) -> str:
    width, height = extract_dimensions(node)
    ptxt = extract_ptxt(node)
    ptxt_str = f'  ptxt="{ptxt}"' if ptxt else ""
    return f"{node_name}  (w={width}, h={height}){ptxt_str}"


def build_tree_lines(root_name: str, root_node: Any, name_registry: dict[int, str]) -> list[str]:
    lines: list[str] = []
    seen: set[int] = set()

    def walk(name: str, node: Any, prefix: str = "", is_last: bool = True) -> None:
        node_id = id(node)
        connector = "└── " if is_last else "├── "

        if prefix == "":
            lines.append(format_node_line(name, node))
        else:
            lines.append(prefix + connector + format_node_line(name, node))

        if node_id in seen:
            cycle_prefix = prefix + ("    " if is_last else "│   ")
            lines.append(cycle_prefix + "└── <cycle detected>")
            return

        seen.add(node_id)

        children = iter_children(node, name_registry)
        child_prefix = prefix + ("    " if is_last else "│   ")

        for idx, (child_name, child_node) in enumerate(children):
            child_is_last = idx == len(children) - 1
            walk(child_name, child_node, child_prefix, child_is_last)

    walk(root_name, root_node, "", True)
    return lines


def build_title_map() -> dict[str, str]:
    """
    Build pseudo-window-name -> real window title map from WindowConfig.

    Prefer cfg.TITLE_MAP if available, and fall back to a best-effort build.
    """
    cfg = WindowConfig()

    title_map = getattr(cfg, "TITLE_MAP", None)
    if isinstance(title_map, dict) and title_map:
        return dict(title_map)

    fallback: dict[str, str] = {}

    explicit_candidates = {
        "win_main": ("WINDOW_TOS_MAIN", "WINDOW_MAIN"),
        "win_logon": ("WINDOW_TOS_LOGON", "WINDOW_LOGON"),
        "win_updater": ("WINDOW_TOS_UPDATER", "WINDOW_UPDATER", "WINDOW_TOS_UPDATE", "WINDOW_UPDATE"),
        "win_update": ("WINDOW_TOS_UPDATE", "WINDOW_UPDATE", "WINDOW_TOS_UPDATER", "WINDOW_UPDATER"),
        "win_saver": ("WINDOW_TOS_SAVER", "WINDOW_SAVER"),
        "win_tos": ("WINDOW_TOS",),
    }

    for pseudo_name, attr_names in explicit_candidates.items():
        for attr_name in attr_names:
            value = getattr(cfg, attr_name, None)
            if isinstance(value, str) and value.strip():
                fallback[pseudo_name] = value
                break

    for attr_name in dir(cfg):
        if not attr_name.startswith("WINDOW_"):
            continue

        value = getattr(cfg, attr_name, None)
        if not isinstance(value, str) or not value.strip():
            continue

        suffix = attr_name[len("WINDOW_"):]
        if suffix.startswith("TOS_"):
            suffix = suffix[len("TOS_"):]

        pseudo_name = f"win_{suffix.lower()}"
        fallback.setdefault(pseudo_name, value)

    return fallback


def load_layout_via_project_module(yaml_path: Path, title_map: dict[str, str]) -> Any:
    try:
        return load_widget_layout(yaml_path, title_map)
    except TypeError:
        return load_widget_layout(str(yaml_path), title_map)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Load pseudo-widget YAML with project modules and print the hierarchy "
            "for each true top-level root tree."
        )
    )
    parser.add_argument("yaml_file", help="Path to the pseudo-widget YAML file")
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Directory for the log file (default: ./logs)",
    )
    args = parser.parse_args()

    yaml_path = Path(args.yaml_file).expanduser().resolve()
    log_dir = Path(args.log_dir).expanduser().resolve()

    logger = setup_logger("widget_tree_test_v3", "widget-tree-test-v3", log_dir)

    if not yaml_path.exists():
        logger.error("YAML file does not exist: %s", yaml_path)
        return 1

    try:
        title_map = build_title_map()
        logger.info("YAML source: %s", yaml_path)
        logger.info("title_map entries: %d", len(title_map))
        for key, value in sorted(title_map.items()):
            logger.info("  %s -> %s", key, value)
        logger.info("")

        layout_obj = load_layout_via_project_module(yaml_path, title_map)
        name_registry = build_name_registry(layout_obj)
        roots = normalize_top_level_roots(layout_obj, source_name=yaml_path.stem)

        if not roots:
            logger.warning("No top-level root trees were found in: %s", yaml_path)
            return 0

        logger.info("Top-level root tree count: %d", len(roots))
        logger.info("")

        for idx, (root_name, root_node) in enumerate(roots, start=1):
            header = f"Top-level root tree {idx}: {root_name}"
            divider = "-" * len(header)

            tree_lines = build_tree_lines(root_name, root_node, name_registry)
            tree_text = "\n".join(tree_lines)

            logger.info(header)
            logger.info(divider)
            logger.info(tree_text)
            logger.info("")

    except Exception:
        logger.exception("Failed while loading or rendering widget trees.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


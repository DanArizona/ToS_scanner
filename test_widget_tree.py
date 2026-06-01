from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

CHILD_CONTAINER_KEYS = ("children", "widgets", "regions", "items", "members", "stacks")


def setup_logger(logger_name: str, log_stem: str, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{log_stem}-{datetime.now():%Y-%m-%d}.log"

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def node_label(name: str, node: Any) -> str:
    parts = [str(name)]

    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type:
            parts.append(f"[{node_type}]")

        if "title" in node and node["title"] not in (None, "", name):
            parts.append(f'title="{node["title"]}"')

        geom_keys = ("x", "y", "width", "height")
        if all(k in node for k in geom_keys):
            parts.append(
                f'({node["x"]}, {node["y"]}, {node["width"]}x{node["height"]})'
            )

    return " ".join(parts)


def iter_children(node: Any) -> list[tuple[str, Any]]:
    children: list[tuple[str, Any]] = []

    if isinstance(node, dict):
        # Prefer common container keys if present.
        for key in CHILD_CONTAINER_KEYS:
            value = node.get(key)
            if isinstance(value, dict):
                for child_name, child_node in value.items():
                    children.append((str(child_name), child_node))
                return children
            if isinstance(value, list):
                for idx, child_node in enumerate(value):
                    if isinstance(child_node, dict):
                        label = str(child_node.get("name", f"{key}[{idx}]"))
                    else:
                        label = f"{key}[{idx}]"
                    children.append((label, child_node))
                return children

        # Fallback: recurse into every nested dict/list entry.
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                children.append((str(key), value))

    elif isinstance(node, list):
        for idx, item in enumerate(node):
            if isinstance(item, dict):
                label = str(item.get("name", f"[{idx}]"))
            else:
                label = f"[{idx}]"
            children.append((label, item))

    return children


def build_tree_lines(root_name: str, root_node: Any) -> list[str]:
    lines = [root_name]

    def walk(node: Any, prefix: str = "") -> None:
        children = iter_children(node)
        for idx, (name, child) in enumerate(children):
            is_last = idx == len(children) - 1
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + node_label(name, child))
            extension = "    " if is_last else "│   "
            walk(child, prefix + extension)

    walk(root_node)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read a pseudo-widget YAML file and print/log its hierarchy tree."
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

    logger = setup_logger("widget_tree_test", "widget-tree-test", log_dir)

    if not yaml_path.exists():
        logger.error("YAML file does not exist: %s", yaml_path)
        return 1

    try:
        data = load_yaml(yaml_path)
        tree_lines = build_tree_lines(yaml_path.name, data)
        tree_text = "\n".join(tree_lines)

        logger.info("YAML source: %s", yaml_path)
        logger.info("Node count (including root): %d", len(tree_lines))
        logger.info("Hierarchy tree:\n%s", tree_text)

    except Exception:
        logger.exception("Failed while reading or rendering the YAML tree.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


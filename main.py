# -*- coding: utf-8 -*-
"""Print Round Robin desktop application entry point."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from print_round_robin import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="Print Round Robin — Windows print queue balancer")
    parser.add_argument("--minimized", action="store_true", help="start minimized")
    parser.add_argument("--admin-action-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"Print Round Robin {__version__}")
    args = parser.parse_args()

    if args.admin_action_file:
        from print_round_robin.windows import execute_admin_action_file
        return execute_admin_action_file(args.admin_action_file)

    if os.name != "nt":
        print("Print Round Robin предназначен для Windows 10/11.", file=sys.stderr)
        return 2

    try:
        import tkinter as tk
        from print_round_robin.gui import PrintRoundRobinApp
    except Exception as exc:
        print(f"Не удалось загрузить Tkinter: {exc}", file=sys.stderr)
        return 3

    root = tk.Tk()
    PrintRoundRobinApp(root, minimized=args.minimized)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

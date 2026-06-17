"""Launcher for poetry run dashboard."""

import sys
from streamlit.web import cli as stcli
from src.cli.constants import PROJECT_ROOT


def main():
    """Launcher for poetry run dashboard"""
    app_path = str(PROJECT_ROOT / "dashboard" / "app.py")
    sys.argv = ["streamlit", "run", app_path]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()

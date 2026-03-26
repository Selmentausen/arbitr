import sys
from streamlit.web import cli as stcli

def main():
    """Launcher for poetry run dashboard"""
    sys.argv = ["streamlit", "run", "dashboard/app.py"]
    sys.exit(stcli.main())

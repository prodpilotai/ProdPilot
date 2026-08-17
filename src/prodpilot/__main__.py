"""Allow the server to be started with `python -m prodpilot`.

An MCP client that has the project on its path can launch the server this way
without depending on the console script being installed.
"""

from prodpilot.cli import app

if __name__ == "__main__":
    app()

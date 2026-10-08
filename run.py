"""Development entrypoint: python run.py

Production runs gunicorn against ``flatout:create_app()``; see the Dockerfile.
The debugger listens on localhost alone; set DEV_BIND=0.0.0.0 to reach the dev
server from the local network (the debugger is PIN-gated, but that PIN is the
only wall between the LAN and the process).
"""
import os

from flatout import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("DEV_BIND", "127.0.0.1"), port=8000, debug=True)

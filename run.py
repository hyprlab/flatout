"""Development entrypoint: python run.py

Production runs gunicorn against ``flatout:create_app()``; see the Dockerfile.
"""
from flatout import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    FLASK_APP=flatout

# flatpak and ostree import, sign and serve the repository; gpg holds the
# signing key. rpm (rpmsign) and createrepo-c make the dnf repository;
# dpkg-deb, already in the base image, reads Debian packages for the apt one.
# No recommended packages: the repository tools need none of the desktop
# integration that would come with them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends flatpak ostree gnupg rpm createrepo-c \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY flatout ./flatout
COPY run.py LICENSE CHANGELOG.md ./

RUN useradd --create-home --uid 1000 flatout \
    && mkdir -p /data \
    && chown -R flatout:flatout /data /app
USER flatout

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# One worker plus threads, on purpose: the job thread that writes the
# repository must exist exactly once, and SQLite is happiest with a single
# writing process. Uploads of large bundles need the long timeout.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "900", \
     "--access-logfile", "-", "--bind", "0.0.0.0:8000", "flatout:create_app()"]

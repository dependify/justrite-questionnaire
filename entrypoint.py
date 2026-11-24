#!/usr/bin/env python3
"""Container entrypoint: make the data volume writable, drop privileges, start the server."""
import os
import pathlib
import sys

APP_UID = 10001
APP_GID = 10001
BASE_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR", "/data")).resolve()


def main() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"FATAL: cannot create data directory {DATA_DIR}: {exc}", file=sys.stderr)
        raise

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            os.chown(DATA_DIR, APP_UID, APP_GID)
            for root, dirs, files in os.walk(DATA_DIR):
                for name in dirs:
                    os.chown(os.path.join(root, name), APP_UID, APP_GID)
                for name in files:
                    os.chown(os.path.join(root, name), APP_UID, APP_GID)
        except Exception as exc:
            print(f"WARNING: could not adjust ownership of {DATA_DIR}: {exc}", file=sys.stderr)
        try:
            os.setgroups([])
            os.setgid(APP_GID)
            os.setuid(APP_UID)
        except Exception as exc:
            print(f"FATAL: could not drop privileges: {exc}", file=sys.stderr)
            raise

    print(f"Starting server as uid={os.geteuid() if hasattr(os, 'geteuid') else 'n/a'} data={DATA_DIR}", flush=True)
    os.execv(sys.executable, [sys.executable, str(BASE_DIR / "server.py")])


if __name__ == "__main__":
    main()

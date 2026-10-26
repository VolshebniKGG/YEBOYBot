"""CI-only adapters to native clients in the disposable PostgreSQL service."""
from pathlib import Path
import sys

WRAPPER = '''#!/usr/bin/env python3
import os
from pathlib import Path
import sys

container = os.environ.get("YEBOY_TEST_PG_CONTAINER")
if not container:
    raise SystemExit("Disposable CI PostgreSQL container is not configured")
env = dict(os.environ)
for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
    env.pop(name, None)
# Both client and server are inside this same test service container.
env["PGHOST"], env["PGPORT"] = "127.0.0.1", "5432"
args = ["docker", "--host=unix:///var/run/docker.sock", "exec", "--interactive"]
for name in ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD", "PGSSLMODE", "PGCONNECT_TIMEOUT"):
    if name in env:
        args += ["--env", name]
args += [container, Path(sys.argv[0]).name, *sys.argv[1:]]
os.execvpe("docker", args, env)
'''


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("pg_dump", "pg_restore"):
        path = directory / name
        path.write_text(WRAPPER, encoding="utf-8")
        path.chmod(0o700)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

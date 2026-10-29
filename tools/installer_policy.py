"""Shared stdlib-only policy for package installation in owned project venvs."""
import os

PIP_VERSION = "26.2"
PIP_VERSION_CHECK = 'import importlib.metadata as metadata; print(metadata.version("pip"))'


def pip_is_current(version):
    # PyPI 26.2 and its PEP 440 spelling 26.2.0 designate the same release.
    return isinstance(version, str) and version.strip() in {PIP_VERSION, PIP_VERSION + ".0"}


def clean_environment(source=None):
    env = dict(os.environ if source is None else source)
    for key in list(env):
        if key.upper().startswith(("PYTHON", "PIP_")) or key.upper() in {"VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT"}:
            del env[key]
    env.update(PYTHONUTF8="1", PIP_CONFIG_FILE=os.devnull)
    return env


def pip_command(interpreter, *arguments, source=None):
    original = os.environ if source is None else source
    args = [str(interpreter), "-I", "-m", "pip", "--isolated", "--require-virtualenv",
            "--disable-pip-version-check", "--no-cache-dir"]
    # --isolated ignores PIP_CERT, so preserve existing verified CA trust explicitly.
    cert = original.get("PIP_CERT") or original.get("REQUESTS_CA_BUNDLE")
    if cert:
        args += ["--cert", cert]
    return args + list(arguments)

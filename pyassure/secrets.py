"""Password encryption for config files:  python -m pyassure.secrets <command>

Store secrets as  ENC(<token>)  in YAML; PyAssure decrypts them IN MEMORY, only when a connection
or session is built. Plaintext is never written to disk, reports or logs.

  keygen                    create the master key   (app/config/.pyassure.key, mode 600)
  encrypt [VALUE]           encrypt one value       (hidden prompt when VALUE is omitted)
  decrypt TOKEN             print one secret        (use sparingly)
  encrypt-config FILE       encrypt every plaintext password/token/secret in a YAML file
  rotate FILE --old-key-file A --new-key-file B     re-encrypt a file under a new key

Master key lookup: env PYASSURE_KEY, then the key file (env PYASSURE_KEY_FILE overrides the path).
Algorithm: Fernet (AES-128-CBC + HMAC-SHA256, random IV per encryption, tamper-evident).

Threat model: this keeps secrets out of git/tickets/screenshots. Anyone holding BOTH the config and
the key can decrypt, so keep the key in a CI secret store and never commit it (*.key is git-ignored).
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from pyassure.config import APP_DIR

ENV_KEY, ENV_KEY_FILE = "PYASSURE_KEY", "PYASSURE_KEY_FILE"
DEFAULT_KEY_FILE = APP_DIR / "config" / ".pyassure.key"
_ENC = re.compile(r"^ENC\((?P<t>.+)\)$", re.DOTALL)
_ENC_ANY = re.compile(r"ENC\((?P<t>[A-Za-z0-9_\-=]+)\)")
SENSITIVE_KEYS = ("password", "passwd", "secret", "access_token", "token", "api_key",
                  "private_key_passphrase", "client_secret")


class SecurityError(Exception):
    """Missing/invalid key or undecryptable token."""


# --------------------------------------------------------------------------- key handling
def key_file() -> Path:
    return Path(os.environ.get(ENV_KEY_FILE, DEFAULT_KEY_FILE)).expanduser()


def load_key(path: Optional[Path] = None) -> bytes:
    """Explicit path > env var > default key file."""
    if path is None and os.environ.get(ENV_KEY):
        return os.environ[ENV_KEY].strip().encode()
    kf = Path(path) if path else key_file()
    if kf.exists():
        return kf.read_text(encoding="utf-8").strip().encode()
    raise SecurityError(f"No master key. Set {ENV_KEY} or create {kf} with: python -m pyassure.secrets keygen")


def _fernet(key: Optional[bytes] = None) -> Fernet:
    try:
        return Fernet(key or load_key())
    except ValueError as exc:
        raise SecurityError(f"Master key is not a valid Fernet key: {exc}") from exc


# ------------------------------------------------------------------------ values and trees
def is_encrypted(v: Any) -> bool:
    return isinstance(v, str) and _ENC.match(v.strip()) is not None


def encrypt_value(plain: str, key: Optional[bytes] = None) -> str:
    return f"ENC({_fernet(key).encrypt(plain.encode()).decode()})"


def decrypt_value(v: str, key: Optional[bytes] = None) -> str:
    """Decrypt ENC(...); anything else is returned unchanged."""
    m = _ENC.match(v.strip())
    if not m:
        return v
    try:
        return _fernet(key).decrypt(m.group("t").encode()).decode()
    except InvalidToken as exc:
        raise SecurityError("Could not decrypt value: wrong master key or corrupted token") from exc


def decrypt_tree(obj: Any) -> Any:
    """Copy of a dict/list with every ENC(...) decrypted. The key is loaded lazily, so configs
    without encrypted values need no key at all."""
    if isinstance(obj, dict):
        return {k: decrypt_tree(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decrypt_tree(v) for v in obj]
    return decrypt_value(obj) if is_encrypted(obj) else obj


# ------------------------------------------------------------------------- config files
# Line based (not a YAML round-trip) so comments and formatting survive.
_LINE = re.compile(r"^(?P<ind>\s*(?:-\s+)?)(?P<key>" + "|".join(SENSITIVE_KEYS) + r")\s*:\s*"
                   r"(?P<val>\"[^\"]*\"|'[^']*'|[^#\n]*?)\s*(?P<cmt>#.*)?$", re.IGNORECASE)


def encrypt_config_file(path: Path, dry_run: bool = False) -> list[str]:
    """Encrypt plaintext sensitive values in place (keeps a .bak). Skips empty values, ${ENV}
    placeholders and values that are already ENC(...). Returns what changed."""
    path, out, changed = Path(path), [], []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = _LINE.match(line)
        val = m.group("val").strip().strip("'\"") if m else ""
        if m and val and not val.startswith("${") and not is_encrypted(val):
            cmt = f"  {m.group('cmt')}" if m.group("cmt") else ""
            line = f"{m.group('ind')}{m.group('key')}: {encrypt_value(val)}{cmt}"
            changed.append(f"line {n}: {m.group('key')}")
        out.append(line)
    if changed and not dry_run:
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def rotate_config_file(path: Path, old: bytes, new: bytes) -> int:
    """Re-encrypt every ENC(...) in a file from key `old` to key `new`. Returns the count."""
    path, n = Path(path), 0
    text = path.read_text(encoding="utf-8")

    def swap(m: re.Match) -> str:
        nonlocal n
        n += 1
        return encrypt_value(decrypt_value(m.group(0), old), new)

    new_text = _ENC_ANY.sub(swap, text)
    if n:
        path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8")
        path.write_text(new_text, encoding="utf-8")
    return n


# ------------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m pyassure.secrets", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen"); k.add_argument("--out"); k.add_argument("--force", action="store_true")
    k.add_argument("--print", dest="show", action="store_true", help="print the key (e.g. for a CI secret)")
    sub.add_parser("encrypt").add_argument("value", nargs="?")
    sub.add_parser("decrypt").add_argument("token")
    ec = sub.add_parser("encrypt-config"); ec.add_argument("file"); ec.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("rotate"); r.add_argument("file")
    r.add_argument("--old-key-file", required=True); r.add_argument("--new-key-file", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "keygen":
            key = Fernet.generate_key().decode()
            if a.show:
                print(key)
                return 0
            path = Path(a.out) if a.out else key_file()
            if path.exists() and not a.force:
                raise SecurityError(f"{path} exists (use --force; existing ENC() values would break)")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(key + "\n", encoding="utf-8")
            try:
                path.chmod(stat.S_IRUSR | stat.S_IWUSR)         # owner read/write only (POSIX)
            except OSError:
                pass
            print(f"Master key written to {path} - keep it out of git (or export {ENV_KEY} in CI).")
        elif a.cmd == "encrypt":
            print(encrypt_value(a.value if a.value is not None else getpass.getpass("Value to encrypt: ")))
        elif a.cmd == "decrypt":
            print(decrypt_value(a.token))
        elif a.cmd == "encrypt-config":
            ch = encrypt_config_file(Path(a.file), a.dry_run)
            print(f"{'Would encrypt' if a.dry_run else 'Encrypted'} {len(ch)} value(s) in {a.file}" + "".join(f"\n  {c}" for c in ch))
        elif a.cmd == "rotate":
            old, new = (Path(x).read_text().strip().encode() for x in (a.old_key_file, a.new_key_file))
            print(f"Re-encrypted {rotate_config_file(Path(a.file), old, new)} value(s) in {a.file}")
    except SecurityError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

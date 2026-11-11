# 9. Security: encrypting passwords in config files

Store passwords/tokens as `ENC(<token>)`. PyAssure decrypts them **in memory only when a connection/endpoint is built**; plaintext is never written to disk, reports or logs.

```yaml
snowflake_dev:
  type: snowflake
  user: QA_USER
  password: ENC(gAAAAABq8W6O...)
```

## Setup (once)
```bash
python -m pyassure.secrets keygen           # app/config/.pyassure.key (mode 600, git-ignored)
python -m pyassure.secrets keygen --print   # print the key instead (store it as a CI secret)
```
Master key lookup: `PYASSURE_KEY` env var, then the key file (`PYASSURE_KEY_FILE` overrides the path).

## Commands (`pyassure-secrets` is the same CLI as an installed command)
| Command | Does |
|---|---|
| `encrypt [VALUE]` | prompts (hidden) and prints an `ENC(...)` token |
| `encrypt-config FILE [--dry-run]` | encrypts every plaintext `password, passwd, secret, access_token, token, api_key, private_key_passphrase, client_secret` in place; keeps comments; writes `.bak`; skips `${ENV}` and already-encrypted values |
| `decrypt "ENC(...)"` | prints the secret (use sparingly) |
| `rotate FILE --old-key-file A --new-key-file B` | re-encrypts all `ENC()` values under a new key |

## Where decryption applies
Connection settings (`connections.yml`), inline endpoint settings in dataset YAML (including `web_table.actions[].value` and `api.headers`), values arriving through
`${ENV}`, and credentials read in `app/auth.py` via `decrypt_value()`. The key is loaded lazily: configs without `ENC()` need no key.

## Mechanics and threat model
Fernet (AES-128-CBC + HMAC-SHA256), random IV per encryption (same input -> different tokens), tamper/wrong-key detection
(`SecurityError: Could not decrypt value: wrong master key or corrupted token`). This keeps secrets out of git, tickets and screenshots; anyone holding **both**
the config and the key can decrypt. So: never commit the key (`*.key` is git-ignored); inject `PYASSURE_KEY` from your CI secret store; use a key per
environment and `rotate` when people leave; prefer SSO/key-pair/short-lived credentials and **read-only test accounts**.

Connection settings are never put into report facts, checkpoints or JSON; driver error messages normally do not echo passwords - review them for unusual drivers.
Session files (`.pyassure/sessions/*.json`) contain live cookies/tokens: they are git-ignored; treat them as secrets and do not publish them as CI artifacts.

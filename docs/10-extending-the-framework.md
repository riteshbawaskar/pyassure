# 10. Extending PyAssure

Day-to-day work happens in `app/`. Touch `pyassure/` only to add **generic** capability.

## In `app/` (typical)
| Task | How |
|---|---|
| New page | `app/pages/x_page.py` (`class XPage(BasePage)`), then steps in `app_steps.py` |
| New API area | `app/api/x_api.py` (`class XApi(ApiClient)`) |
| New login type | `@session_provider("name", kind="api"\|"browser")` in `app/auth.py`; add to `sessions.warmup` if used a lot |
| New comparison | `app/datasets/compare/x.yml` + 4-line scenario |
| App-specific hook | `app/features/environment.py`: define your hook, call `pyassure.hooks.<hook>(context)` first |
| Different environment | `PYASSURE_CONFIG=app/config/pyassure.qa.yml`, `PYASSURE_CONNECTIONS=app/config/connections.qa.yml` |

## In `pyassure/` (generic additions)
**A database** - add a class in `pyassure/data/connectors.py` and register it:
```python
class MyDbConnector(_DbApiConnector):            # any DB-API driver
    def connect(self):
        import mydb
        self._conn = mydb.connect(host=self.config["host"], password=self.config["password"])
REGISTRY["mydb"] = MyDbConnector
```
**A validation check** - in `pyassure/data/validate.py`:
```python
@register("positive_balance")
def _positive(v, df, spec):
    bad = pd.to_numeric(df["BALANCE"], errors="coerce") < 0         # True = invalid row
    return [v.bad_rows(spec, "positive_balance", spec.get("name", "BALANCE"), df, bad, "BALANCE >= 0")]
```
**A comparison option** - add a field to `CompareOptions` (`data/compare.py`) and use it in `_equal_non_null`; typos in option names are rejected automatically.
**A step library** - new `pyassure/steps/x_steps.py`, import it in `app/features/steps/framework_steps.py`; record results with `checkpoint()`.
**A config switch** - add the key + default to `DEFAULTS` in `pyassure/config.py`; read it with `section("name")`.
**Branding** - `pyassure/branding.py`.

## Tests for the framework
`pytest -q` (`tests/test_pyassure.py`) needs no cloud access or browser (the session/API tests start the demo app). Add a test per new check/option.

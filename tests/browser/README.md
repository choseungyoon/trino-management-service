# Browser tests

These tests cover behavior that TypeScript and FastAPI unit tests cannot prove once React is running
in a browser:

- login and expired-session redirect over HTTPS;
- blank-reason and double-click protection for query kill;
- native dialog behavior;
- polling without blanking the current table;
- theme persistence;
- every registered console route rendering without a browser error.

They use an in-memory FastAPI harness and a stub Trino client. PostgreSQL and a real Trino cluster
are not required. HTTPS is intentional because the session cookie is `Secure`.

```bash
venv/bin/pip install -e ".[browser]"
venv/bin/python -m playwright install chromium
venv/bin/python -m unittest tests.browser.ui_behaviour -v
```

The module is outside the default suite because Chromium must be downloaded and the corporate
network may block it. Real PostgreSQL and Trino checks remain under `tests/integration/` and the
onsite checklist in `docs/TODO.md`.

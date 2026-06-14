import os

# config.py requires ADMIN_TOKEN at import; ensure it's set before tests import it.
os.environ.setdefault("ADMIN_TOKEN", "test-token")

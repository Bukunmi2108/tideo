"""Fire a Celery task by dotted path and print its JSON result. For manual phase drills.

Run inside a worker container (where the task code and fixtures are available):
    python scripts/fire_task.py <module.func> [arg ...]

Examples:
    python scripts/fire_task.py app.workers.tasks.inspect.probe fixtures/short.mp4
    python scripts/fire_task.py app.workers.tasks.transcode.transcode j_ok fixtures/short.mp4
"""

import importlib
import json
import sys
from pathlib import Path

# Run as a script, /app (the repo root) isn't on sys.path by default; add it so `app` imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python scripts/fire_task.py <module.func> [arg ...]")
    module_path, func = sys.argv[1].rsplit(".", 1)
    task = getattr(importlib.import_module(module_path), func)
    result = task.delay(*sys.argv[2:]).get(timeout=120)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

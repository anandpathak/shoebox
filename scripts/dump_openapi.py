"""Write web/src/api/openapi.json from the FastAPI app (the server doesn't publish it at runtime).
Then `npm run gen:api` in web/ turns it into TypeScript types."""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("SHOEBOX_HOME", tempfile.mkdtemp(prefix="shoebox-openapi-"))
os.environ.setdefault("SHOEBOX_TOKEN_STORE", "file")

from fastapi.openapi.utils import get_openapi  # noqa: E402

from shoebox.server.app import create_app  # noqa: E402

app = create_app()
spec = get_openapi(title=app.title, version=app.version, routes=app.routes)
out = ROOT / "web" / "src" / "api" / "openapi.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(spec, indent=2) + "\n")
print(f"wrote {out.relative_to(ROOT)} ({len(spec['paths'])} paths)")

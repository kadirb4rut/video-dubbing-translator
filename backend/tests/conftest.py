from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="lingowave-test-suite-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_ROOT / 'test.db'}")
os.environ.setdefault("LOCAL_STORAGE_DIR", str(TEST_ROOT / "objects"))

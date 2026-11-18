"""Хвост журнала приложения в читаемом виде: .venv\\Scripts\\python.exe tests\\log_tail.py [строк]"""

import sys
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "dictation.log"
count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
lines = LOG.read_text(encoding="cp1251", errors="replace").splitlines()
print("\n".join(lines[-count:]))

"""Tick a task in docs/TASKS.md: `python scripts/task_done.py T2.1 [YYYY-MM-DD] [--note "text"]`."""

import re
import sys
from datetime import date
from pathlib import Path

TASKS = Path(__file__).resolve().parent.parent / "docs" / "TASKS.md"


def main(argv: list[str]) -> None:
    note = None
    if "--note" in argv:
        i = argv.index("--note")
        note, argv = argv[i + 1], argv[:i] + argv[i + 2:]
    task, when = argv[0], (argv[1] if len(argv) > 1 else date.today().isoformat())
    text = TASKS.read_text()
    m = re.search(r"- \[[ ~]\] \*\*" + re.escape(task) + r"\*\*", text)
    if not m:
        sys.exit(f"{task}: not found or already done")
    end = text.index("\n", m.start())
    line = text[m.start():end].replace("- [ ]", "- [x]", 1).replace("- [~]", "- [x]", 1) + f" ✅ {when}"
    text = text[:m.start()] + line + text[end:]
    if note:
        nxt = text.index("\n", m.start() + len(line))
        # add the note under the task's "Done when" line (or right after the task line)
        dw = re.match(r"\n  \*Done when:\*[^\n]*", text[nxt:])
        at = nxt + (dw.end() if dw else 0)
        text = text[:at] + f"\n  *Note:* {note}" + text[at:]
    TASKS.write_text(text)
    print(f"{task} ticked")


if __name__ == "__main__":
    main(sys.argv[1:])

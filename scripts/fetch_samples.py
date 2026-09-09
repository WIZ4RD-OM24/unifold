"""Download real Uniface exports to develop against.

These are workshop materials published by Rocket in the `uniface/learn-palettes`
repository. We do not vendor them -- they are someone else's files and carry no
stated licence -- so this script fetches them on demand into samples/, which is
gitignored.

They are the project's ground truth: one Uniface 9.7 export and five from 10.2,
covering an include proc, an application model, three components and a project.

    py -3 scripts/fetch_samples.py
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/uniface/learn-palettes/master"
DEST = Path(__file__).resolve().parent.parent / "samples" / "learn-palettes"

# (remote path, local name, what it is)
FILES = [
    ("Code Samples/HILIGHTROW Include Proc.xml",
     "HILIGHTROW_Include_Proc.xml", "9.7 include proc (global ProcScript)"),
    ("Employee Form Export/cpt_showemployees.xml",
     "cpt_showemployees.xml", "10.2 component"),
    ("Employee Form Export/cpt_showemployeeswithhighlight.xml",
     "cpt_showemployeeswithhighlight.xml", "10.2 component, near-identical variant"),
    ("Bootstrap DSP/cpt_bootstrapdsp.xml",
     "cpt_bootstrapdsp.xml", "10.2 dynamic server page component"),
    ("Employee Model and Files/bootstrap_model.xml",
     "bootstrap_model.xml", "10.2 application model"),
    ("Solutions/prj_full_demoproject.xml",
     "prj_full_demoproject.xml", "10.2 project (largest sample)"),
]


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    failures = 0
    for remote, local, description in FILES:
        url = "%s/%s" % (BASE, urllib.parse.quote(remote))
        target = DEST / local
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
        except (urllib.error.URLError, OSError) as exc:
            print("FAILED  %-40s %s" % (local, exc), file=sys.stderr)
            failures += 1
            continue
        target.write_bytes(data)
        print("%7d bytes  %-40s %s" % (len(data), local, description))
    if failures:
        print("\n%d file(s) could not be fetched." % failures, file=sys.stderr)
        return 1
    print("\nSamples in %s" % DEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

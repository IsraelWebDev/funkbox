"""Every relative link and image in the tracked Markdown points at a file that git actually tracks
(a link to an ignored file is a 404 on GitHub even though it works locally)."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def tracked() -> set[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except Exception:
        pytest.skip("not a git checkout")
    return set(out.split())


def test_markdown_links_resolve_to_tracked_files():
    files = tracked()
    dirs = {str(Path(f).parent) for f in files} | {str(p) for f in files for p in Path(f).parents}
    bad = []
    for md in sorted(f for f in files if f.endswith(".md")):
        text = (ROOT / md).read_text(encoding="utf-8", errors="replace")
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if re.match(r"^(https?:|mailto:|#)", target):
                continue
            path = target.split("#")[0]
            if not path:
                continue
            rel = (Path(md).parent / path)
            norm = str(Path(*[p for p in rel.parts if p != "."]))
            parts = []
            for part in Path(norm).parts:
                if part == "..":
                    parts.pop() if parts else None
                else:
                    parts.append(part)
            norm = "/".join(parts)
            if norm not in files and norm not in dirs:
                bad.append(f"{md}: {target}")
    assert not bad, "links to files git does not track:\n" + "\n".join(bad)

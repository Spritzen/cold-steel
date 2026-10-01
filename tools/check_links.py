"""Check that every relative link in the Markdown docs points at a real file,
and that every `#anchor` matches a heading. External links are not fetched.

Exit code 1 if anything is broken. Run by `make check`.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def anchors(path: Path) -> set[str]:
    """GitHub-style heading slugs."""
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    slugs = set()
    for heading in HEADING.findall(text):
        slug = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
        slugs.add(slug)
    return slugs


def check(md: Path) -> list[str]:
    errors = []
    text = FENCE.sub("", md.read_text(encoding="utf-8"))
    for target in LINK.findall(text):
        if re.match(r"^[a-z]+:", target):  # http:, https:, mailto:
            continue
        file_part, _, anchor = target.partition("#")
        dest = (md.parent / file_part).resolve() if file_part else md
        rel = md.relative_to(ROOT)
        if not dest.exists():
            errors.append(f"{rel}: missing file {target}")
        elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
            errors.append(f"{rel}: missing anchor {target}")
    return errors


def main() -> int:
    files = [
        p
        for p in ROOT.rglob("*.md")
        if not any(part.startswith(".") for part in p.relative_to(ROOT).parts)
    ]
    errors = [e for md in sorted(files) for e in check(md)]
    for error in errors:
        print(error)
    print(f"checked {len(files)} files, {len(errors)} broken links")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

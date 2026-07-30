"""Generates a daily digest note in Reviews/ covering notes created or modified
in the last 24 hours. Meant to run once a day via a scheduler (launchd, cron, ...)."""
import datetime as dt

import common

log = common.setup_logging("digest")

LOOKBACK_HOURS = 24


def collect_recent_notes() -> list[tuple[str, str]]:
    cutoff = dt.datetime.now().timestamp() - LOOKBACK_HOURS * 3600
    recent = []
    for md_file in common.VAULT_DIR.rglob("*.md"):
        if common.is_ignored_path(md_file):
            continue
        if md_file.stat().st_mtime >= cutoff:
            try:
                text = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            recent.append((common.relpath(md_file), text))
    return recent


def build_digest(recent: list[tuple[str, str]]) -> str:
    if not recent:
        return "No notes created or modified in the last 24 hours."

    body = "\n\n".join(f"### {rel}\n{text[:1200]}" for rel, text in recent)
    prompt = f"""You write a daily digest for a personal knowledge base ("second brain").
Below are the notes created or modified in the last 24 hours. Write a concise
bullet-point summary of the main themes and connections between notes, without
inventing content that isn't in the text.

{body}
"""
    summary = common.ollama_generate(common.DIGEST_MODEL, prompt)
    if not summary:
        summary = "Digest unavailable: local generation error (is Ollama reachable?)."
    return summary


def main() -> None:
    common.VAULT_DIR.mkdir(parents=True, exist_ok=True)
    recent = collect_recent_notes()
    log.info("Digest: %d notes modified in the last %dh", len(recent), LOOKBACK_HOURS)

    summary = build_digest(recent)
    today = dt.date.today().isoformat()
    reviews_dir = common.VAULT_DIR / "Reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    out_path = reviews_dir / f"{today}.md"

    note_list = "\n".join(f"- [[{rel.rsplit('.', 1)[0]}]]" for rel, _ in recent) or "- (none)"
    out_path.write_text(
        f"""---
tags: [digest, second-brain]
date: {today}
---

# Digest for {today}

{summary}

## Notes touched
{note_list}
""",
        encoding="utf-8",
    )
    log.info("Digest written to %s", out_path)


if __name__ == "__main__":
    main()

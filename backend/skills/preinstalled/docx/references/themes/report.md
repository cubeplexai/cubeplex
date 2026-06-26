# Style set: report — internal & operational

All-sans and compact — built to be skimmed. Inter headings over a Liberation
Sans body (an Arial-metric face) strip out every serif, so the document reads
fast and modern: status updates, memos, and internal docs where the reader wants
the structure at a glance, not a leisurely read. Tighter margins and spacing pack
more per page than `corporate` without feeling cramped, and a clear blue accent
keeps headings scannable.

## What it uses (docbuilder.THEMES["report"])
- Heading font **Inter**, body font **Liberation Sans**; CJK both **Noto Sans
  CJK SC**.
- Scale: title 22pt → H1 15 → H2 12.5 → H3 11 → body 10.5 → caption 9.
- Color: headings `#1F4E79`, body `#2A2A2A`, accent `#2E75B6`.
- Spacing: 1.15 line, **6pt after** (compact), **0.9in margins** (tighter),
  headings not numbered.

## Use it when
- **Status reports, weekly updates, internal memos, runbooks.**
- Documents that get **skimmed at a desk**, where clarity beats personality.
- **Cross-functional updates** with lots of short sections and bullet points.

## Avoid it when
- It's a **polished external** report or proposal → `corporate` (serif body reads
  more considered).
- It's a **paper** → `academic`.
- It's a **Chinese document** → `chinese` / `official`.

## Recommended structure
`heading("Subject") → body (TL;DR) → heading per area + bullets → table for any
metrics → heading("Next steps") + numbered`. Short, scannable sections. Often no
`toc` and no cover — open with the subject line. Reach for `bullets()` heavily;
the all-sans face makes lists read crisply.

## Copy tone
- Terse and direct: what happened, what it means, what's next.
- Front-load the takeaway; details follow.
- Bullet parallel items; reserve prose for reasoning that needs it.

## Example
```python
import sys; sys.path.insert(0, "<skill_path>/scripts")
from docbuilder import Doc
d = Doc(theme="report")
d.heading("Weekly status — Platform team, week of June 22")
d.body("On track for the July release. One risk: the migration window overlaps "
       "with the finance close.")
d.heading("Shipped")
d.bullets(["SSE reconnect fix deployed to prod", "New rate-limit dashboard live"])
d.heading("In progress")
d.bullets(["Schema migration (80%)", "Load test for the new tier"])
d.heading("Metrics")
d.table(["Metric", "Last week", "This week"],
        [["p99 latency", "420ms", "310ms"], ["Error rate", "0.4%", "0.2%"]])
d.heading("Next steps")
d.numbered(["Finalize migration window with Finance", "Ship load-test results"])
d.save("/workspace/doc/out.docx")
```
Then: `python3 <skill_path>/scripts/check_doc.py /workspace/doc/out.docx` and fix
any errors.

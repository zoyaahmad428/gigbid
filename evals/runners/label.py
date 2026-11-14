"""Label the 30 judge-calibration proposals yourself (the brief: "label 30 items yourself").

    python -m evals.runners.label

Shows each proposal with its job, blind (you don't see which system wrote it), and asks for:
  score 1-5  (rubric below, the same one the LLM judge gets: agent/prompts/judge.md)
  send y/n   could the freelancer send it as it is?
Answers are saved after every item to evals/judge/human_labels.json; quit any time with q and
run again to continue. Type b to go back one item.
"""
from __future__ import annotations

import json
import textwrap
import time

from evals.runners.judge import HUMAN, ITEMS

RUBRIC = """1 not past the first line (generic, off-topic, or claims that look invented)
2 weak (mostly generic, little specific to this job)
3 acceptable (on topic, some specific proof, but generic in places or no clear next step)
4 good (specific to the main requirements, concrete proof, clear first step or question)
5 excellent (4, and concise and persuasive: the client would very likely reply)"""


def main() -> None:
    items = json.loads(ITEMS.read_text())["items"]
    data = json.loads(HUMAN.read_text()) if HUMAN.exists() else {"labeller": "", "labels": {}}
    if not data["labeller"]:
        data["labeller"] = input("Your name (stored with the labels): ").strip() or "me"
    labelled = data["labels"]
    i = next((k for k, it in enumerate(items) if it["item"] not in labelled), len(items))
    while i < len(items):
        it = items[i]
        print("\n" + "=" * 100)
        print(f"Item {i + 1}/{len(items)}   [{it['item']}]   labelled so far: {len(labelled)}")
        print(f"JOB: {it['title']}")
        print(textwrap.fill(it["description"][:700], 100))
        print("-" * 100 + "\nCOVER LETTER:\n")
        for para in it["letter"].split("\n"):
            print(textwrap.fill(para, 100))
        print("-" * 100 + "\n" + RUBRIC)
        prev = labelled.get(it["item"])
        was = f" (was {prev['score']})" if prev else ""
        s = input(f"Score 1-5{was}, b=back, q=quit: ").strip().lower()
        if s == "q":
            break
        if s == "b":
            i = max(0, i - 1)
            continue
        if s not in {"1", "2", "3", "4", "5"}:
            print("Please type 1-5.")
            continue
        send = ""
        while send not in {"y", "n"}:
            send = input("Send as is? y/n: ").strip().lower()
        note = input("Optional note (Enter to skip): ").strip()
        labelled[it["item"]] = {"score": int(s), "send": send == "y", "note": note, "ts": time.time()}
        HUMAN.write_text(json.dumps(data, indent=1) + "\n")
        i += 1
    print(f"\nSaved {len(data['labels'])}/{len(items)} labels to {HUMAN}")


if __name__ == "__main__":
    main()

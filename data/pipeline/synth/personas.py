"""Generate the three persona corpora (everything here is synthetic).

For each persona:
  facts.jsonl            ground-truth project facts (what is actually true)
  profile.json           rate, JSS, availability; priors from real datasets
  cv.md (+ cv.pdf)       CV; each project appears as a one-line bullet
  case_studies/*.md      15-25 case studies in three writing styles, some with code;
                         a few projects get a second, differently worded write-up
  repos/<repo>/          README.md + one code file; decoy repos undersell themselves
  reviews.jsonl          client reviews for completed projects
  evidence_index.jsonl   one EvidenceItem per document (proposals added later)

Every number in a text is rendered from the project's facts, so a claim like
"cut handling time by 34%" can be checked against facts.jsonl.
"""
from __future__ import annotations

import datetime as dt
import random
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stable_id, write_json, write_jsonl, write_text  # noqa: E402
from schema import EvidenceItem, Provenance  # noqa: E402
from synth.persona_content import ARCHETYPES, CODE_SNIPPETS, INDUSTRIES, PERSONAS  # noqa: E402

GEN = "synth.personas@1.0.0"
PROV = Provenance(source="synthetic", source_dataset="generator", generator=GEN)
EXT = {"python": "py", "typescript": "ts", "sql": "sql"}


def _metric(rng: random.Random, tpl: str, lo: int, hi: int) -> tuple[str, dict]:
    if "{a}" in tpl:
        a = lo + rng.randint(-2, 2)
        b = max(1, hi + rng.randint(-1, 1))
        if lo < hi:                       # "from a to b" where b should be the larger
            a, b = min(a, b - 1), b
        return tpl.format(a=a, b=b), {"a": a, "b": b}
    v = rng.randint(min(lo, hi), max(lo, hi))
    return tpl.format(v=v), {"v": v}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48]


def make_projects(persona: str, rng: random.Random) -> list[dict]:
    archs = ARCHETYPES[persona]
    n = rng.randint(15, 25)
    order = archs[:] + rng.sample(archs, len(archs)) + rng.sample(archs, len(archs))  # each once, then repeats
    projects = []
    used_repo_names: set[str] = set()
    for i in range(n):
        a = order[i]
        industry = rng.choice(INDUSTRIES)
        year = rng.choice([2021, 2022, 2023, 2023, 2024, 2024, 2025, 2025, 2026])
        month = rng.randint(1, 12) if year < 2026 else rng.randint(1, 9)
        start = dt.date(year, month, 1)
        weeks = rng.randint(3, 16)
        end = min(start + dt.timedelta(weeks=weeks), dt.date(2026, 10, 31))
        weeks = max(1, (end - start).days // 7)
        metrics = [_metric(rng, t, lo, hi) for t, lo, hi in a["metrics"]]
        repo_name, lang, stack = a["repo"]
        if repo_name in used_repo_names:
            repo_name = f"{repo_name}-{_slug(industry)}"
        used_repo_names.add(repo_name)
        docs = rng.choice(a["docs"]) if "docs" in a else None
        pid = stable_id("prj", persona, i, a["key"], industry)
        projects.append({
            "project_id": pid, "persona": persona, "archetype": a["key"],
            "title": a["title"].format(industry=industry),
            "client_industry": industry,
            "problem": a["problem"].format(docs=docs or "", industry=industry),
            "approach": [s.format(docs=docs or "") for s in a["approach"]],
            "skills": a["skills"], "implied_skills": a["implied"],
            "metrics": [m[0] for m in metrics], "metric_values": [m[1] for m in metrics],
            "start": start.isoformat(), "end": end.isoformat(), "weeks": weeks,
            "repo": {"name": repo_name, "language": lang, "stack": stack,
                     "decoy": bool(a.get("decoy"))},
            "engagement": rng.choice(["fixed-price", "hourly", "hourly"]),
            "has_review": rng.random() < 0.75,
            "second_writeup": False,
            "provenance": PROV.model_dump(),
        })
    for p in rng.sample(projects, 3):
        p["second_writeup"] = True
    return projects


# ------------------------------------------------------------- case studies
def _cs_narrative(p: dict, rng: random.Random) -> str:
    a = p["approach"]
    return (f"# {p['title']}\n\n"
            f"*{p['client_industry'].capitalize()} client · {p['start'][:7]} · {p['weeks']} weeks · {p['engagement']}*\n\n"
            f"{p['problem']} I was brought in to fix that.\n\n"
            f"I started by working out what good looked like with the client, then {a[0]}. "
            f"Next I {a[1]}. Finally, I {a[2]}.\n\n"
            f"The result: I {p['metrics'][0]}, and {p['metrics'][1]}.\n\n"
            f"Tools: {', '.join(p['skills'])}.\n")


def _cs_star(p: dict, rng: random.Random) -> str:
    steps = "\n".join(f"- {s[0].upper() + s[1:]}." for s in p["approach"])
    return (f"# {p['title']}\n\n"
            f"**Client:** {p['client_industry']} company  \n**When:** {p['start'][:7]} to {p['end'][:7]}  \n"
            f"**Engagement:** {p['engagement']}\n\n"
            f"## Situation\n{p['problem']}\n\n## What I did\n{steps}\n\n"
            f"## Outcome\n- {p['metrics'][0][0].upper() + p['metrics'][0][1:]}.\n"
            f"- {p['metrics'][1][0].upper() + p['metrics'][1][1:]}.\n\n"
            f"## Stack\n{' · '.join(p['skills'])}\n")


def _cs_terse(p: dict, rng: random.Random) -> str:
    # Mixed code and prose; skills are mostly implied by the snippet, not named.
    lang = p["repo"]["language"]
    snippet = _code_body(p, max_snippets=1).strip() or "# (internal code, not shareable)"
    return (f"# {p['title']}\n\n"
            f"Short version: {p['problem'].lower()} {p['metrics'][0][0].upper() + p['metrics'][0][1:]}.\n\n"
            f"The core of it was simple: I {p['approach'][1]}. The piece that mattered most:\n\n"
            f"```{lang}\n{snippet}\n```\n\n"
            f"Also {p['metrics'][1]}. Ran {p['weeks']} weeks for a {p['client_industry']} client in {p['start'][:4]}.\n")


def _cs_second(p: dict) -> str:
    """Same project, third description: a reflective blog-style note that never
    repeats the title, so it only matches the project by content."""
    return (f"# What I learned from a {p['client_industry']} project\n\n"
            f"Back in {p['start'][:4]} a {p['client_industry']} client came to me because "
            f"{p['problem'][0].lower() + p['problem'][1:]} The interesting part was not the model or the code. "
            f"It was agreeing what to measure. Once we had that, I {p['approach'][0]} and the rest followed.\n\n"
            f"Numbers, for the record: {p['metrics'][0]}; {p['metrics'][1]}.\n")


CS_STYLES = [_cs_narrative, _cs_star, _cs_terse]


# ------------------------------------------------------------- repos
def _code_body(p: dict, max_snippets: int = 4) -> str:
    lang = p["repo"]["language"]
    lib = CODE_SNIPPETS[lang]
    parts = [lib[s].format(repo=p["repo"]["name"]) if "{repo}" in lib.get(s, "") else lib.get(s, "")
             for s in p["implied_skills"]]
    parts = [x for x in parts if x][:max_snippets]
    return "\n\n".join(parts)


def _readme(p: dict) -> str:
    r = p["repo"]
    if r["decoy"]:
        # Undersells itself: no skills, no metrics. Only the code shows what it does.
        return (f"# {r['name']}\n\n{r['stack'].capitalize()}. Mostly for my own reuse.\n\n"
                f"Not documented yet. See the source.\n")
    return (f"# {r['name']}\n\n{p['title']}.\n\n"
            f"## What it does\n{p['problem']} This is the code from when I {p['approach'][1]}.\n\n"
            f"## Stack\n{r['stack']}; {', '.join(p['skills'])}.\n\n"
            f"## Run\n```bash\nmake install\nmake run\n```\n\n"
            f"Client data is not included. Results in production: {p['metrics'][0]}.\n")


def _code_file(p: dict) -> str:
    lang = p["repo"]["language"]
    head = {"python": '"""{t}."""\nimport json\nimport os\n\n',
            "typescript": "// {t}\n\n",
            "sql": "-- {t}\n\n"}[lang].format(t=p["title"])
    if lang == "sql" and not _code_body(p):
        return head + "SELECT 1;\n"
    return head + (_code_body(p) or "pass\n")


# ------------------------------------------------------------- CV
def _cv(persona: str, cfg_p: dict, projects: list[dict], profile: dict) -> str:
    jobs = "\n".join(f"- **{t}**, {org} ({s} to {e or 'present'})" for org, t, s, e in cfg_p["employers"])
    recent = sorted(projects, key=lambda p: p["start"], reverse=True)
    bullets = "\n".join(
        f"- {p['title']} ({p['start'][:4]}): {p['metrics'][0]}." for p in recent[:12])
    certs = "\n".join(f"- {c}" for c in cfg_p["certs"]) or "- None"
    return (f"# {cfg_p['name']}\n\n*Synthetic persona generated for the GigBid assignment. "
            f"Not a real person.*\n\n{cfg_p['headline']}  \n{cfg_p['location']}  \n"
            f"Rate: ${profile['hourly_rate_usd']}/hr · Job success {profile['job_success_score']}% · "
            f"{profile['jobs_completed']} completed jobs\n\n"
            f"## Summary\n{cfg_p['years']} years of experience. Core skills: {', '.join(cfg_p['core_skills'])}.\n\n"
            f"## Experience\n{jobs}\n\n## Selected freelance projects\n{bullets}\n\n"
            f"## Education\n- {cfg_p['education']}\n\n## Certifications\n{certs}\n")


def _cv_pdf(md: str, path: Path) -> bool:
    try:
        from reportlab import rl_config
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        return False
    rl_config.invariant = 1          # fixed creation date and document id -> byte-identical rebuilds
    styles = getSampleStyleSheet()
    story = []
    for line in md.splitlines():
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"\*(.+?)\*", r"<i>\1</i>", line).replace("  ", "")
        if line.startswith("# "):
            story.append(Paragraph(line[2:], styles["Title"]))
        elif line.startswith("## "):
            story.append(Paragraph(line[3:], styles["Heading2"]))
        elif line.startswith("- "):
            story.append(Paragraph("• " + line[2:], styles["BodyText"]))
        elif line.strip():
            story.append(Paragraph(line, styles["BodyText"]))
        else:
            story.append(Spacer(1, 6))
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return True


# ------------------------------------------------------------- reviews
REVIEW_OPENERS = ["Great to work with.", "Would hire again.", "Solid work overall.",
                  "Delivered what we needed.", "Clear communicator."]
REVIEW_MINOR = ["Took a little longer than planned on the final handover.",
                "Documentation could have been more detailed.",
                "Needed a reminder or two on updates mid-project."]


def _review(p: dict, rng: random.Random) -> dict:
    rating = rng.choices([5.0, 4.5, 4.0, 3.0], weights=[70, 15, 11, 4])[0]
    detail = rng.choice([f"They {p['approach'][rng.randrange(3)]}.", f"In the end they {p['metrics'][0]}."])
    text = f"{rng.choice(REVIEW_OPENERS)} {detail}"
    if rating < 5:
        text += " " + rng.choice(REVIEW_MINOR)
    return {"review_id": stable_id("rev", p["project_id"]), "persona": p["persona"],
            "project_id": p["project_id"], "rating": rating, "text": text,
            "reviewer": f"Client, {p['client_industry']} company",
            "date": p["end"], "provenance": PROV.model_dump()}


# ------------------------------------------------------------- profile
def make_profile(persona: str, cfg_p: dict, priors: dict, rng: random.Random) -> dict:
    return {
        "persona": persona, "name": cfg_p["name"], "headline": cfg_p["headline"],
        "location": cfg_p["location"], "years_experience": cfg_p["years"],
        "category": cfg_p["category"], "core_skills": cfg_p["core_skills"],
        "does_not_do": cfg_p["not_skills"],
        "hourly_rate_usd": priors["hourly_rate_usd"],
        "min_fixed_budget_usd": priors["min_fixed_budget_usd"],
        "job_success_score": priors["job_success_score"],
        "jobs_completed": priors["jobs_completed"],
        "availability_hours_per_week": rng.choice([20, 25, 30]),
        "weekly_connects_budget": rng.choice([60, 80, 100]),
        "priors_from": priors["sources"],
        "provenance": PROV.model_dump(),
    }


def generate_persona(persona: str, out: Path, priors: dict, seed_rng: random.Random) -> dict:
    cfg_p = PERSONAS[persona]
    rng = random.Random(seed_rng.random())
    if out.exists():                       # a rebuild must not keep files from an older run
        assert out.parent.name == "personas", out
        shutil.rmtree(out)
    projects = make_projects(persona, rng)
    profile = make_profile(persona, cfg_p, priors, rng)
    evidence: list[dict] = []

    def ev(etype: str, title: str, path: str, p: dict | None, skills: list[str], implied: list[str],
           date: str, outcome: str | None = None, eid: str | None = None) -> None:
        evidence.append(EvidenceItem(
            evidence_id=eid or stable_id("ev", persona, etype, path), persona=persona, type=etype,
            project_id=p["project_id"] if p else None, title=title, path=path, skills=skills,
            skills_implied=implied, date=date, client_industry=p["client_industry"] if p else None,
            outcome=outcome, provenance=PROV).model_dump())

    cv = _cv(persona, cfg_p, projects, profile)
    write_text(out / "cv.md", cv)
    has_pdf = _cv_pdf(cv, out / "cv.pdf")
    ev("cv", f"CV: {cfg_p['name']}", "cv.pdf" if has_pdf else "cv.md", None, cfg_p["core_skills"], [],
       dt.date(2026, 10, 1).isoformat())

    for i, p in enumerate(projects):
        style = CS_STYLES[i % 3]
        text = style(p, rng)
        rel = f"case_studies/cs_{i+1:02d}_{_slug(p['archetype'])}.md"
        write_text(out / rel, text)
        named = [s for s in p["skills"] if s.lower() in text.lower()]
        ev("case_study", p["title"], rel, p, named,
           [s for s in p["skills"] + p["implied_skills"] if s not in named], p["end"])
        if p["second_writeup"]:
            rel2 = f"case_studies/cs_{i+1:02d}b_notes.md"
            write_text(out / rel2, _cs_second(p))
            ev("case_study", f"Notes: {p['title']}", rel2, p, [], p["skills"] + p["implied_skills"], p["end"])

        r = p["repo"]
        readme = _readme(p)
        write_text(out / f"repos/{r['name']}/README.md", readme)
        ev("readme", f"{r['name']} README", f"repos/{r['name']}/README.md", p,
           [] if r["decoy"] else p["skills"], p["implied_skills"] if not r["decoy"] else p["skills"] + p["implied_skills"],
           p["end"])
        code_rel = f"repos/{r['name']}/main.{EXT[r['language']]}"
        write_text(out / code_rel, _code_file(p))
        ev("code", f"{r['name']} source", code_rel, p, p["implied_skills"], p["skills"], p["end"])

    reviews = [_review(p, rng) for p in projects if p["has_review"]]
    for rv, p in zip(reviews, [p for p in projects if p["has_review"]]):
        ev("review", f"Review: {p['title']}", f"reviews.jsonl#{rv['review_id']}", p, [], p["skills"],
           rv["date"], eid=rv["review_id"])

    write_jsonl(out / "facts.jsonl", projects)
    write_jsonl(out / "reviews.jsonl", reviews)
    write_json(out / "profile.json", profile)
    write_jsonl(out / "evidence_index.jsonl", evidence)
    return {"persona": persona, "projects": projects, "profile": profile, "evidence": evidence,
            "reviews": reviews, "cv_pdf": has_pdf}

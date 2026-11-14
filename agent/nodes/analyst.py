"""Job Analyst: job post -> JobSpec (small model). No tools: it only reads the post it is given."""
from __future__ import annotations

from agent.guards import wrap
from agent.handoffs import JobSpec
from agent.llm import structured
from agent.nodes.base import node, to_thread, usage_entry


@node("analyst")
async def analyst(state: dict) -> dict:
    j = state["job"]
    b = j["budget"]
    spec, u = await to_thread(structured, "analyst", JobSpec, {
        "job_id": j["job_id"],
        "budget": f"{b['type']} {b.get('min')}-{b.get('max')} USD",
        "duration": j.get("duration") or "not stated", "experience": j.get("experience_level") or "not stated",
        "skills": j.get("skills", []), "category": j.get("category"),
        "screening": j.get("screening_questions") or "none",
        "job_post": wrap("job_post", f"{j['title']}\n\n{j['description']}"),
    })
    spec.job_id, spec.title = j["job_id"], j["title"]           # never trust the model for identifiers
    spec.screening_questions = list(j.get("screening_questions") or [])
    return {"job_spec": spec.model_dump(), "usage": usage_entry("analyst", u)}

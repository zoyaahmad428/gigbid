"""Model-free unit tests for RAG building blocks (fast; safe to run in CI)."""
from __future__ import annotations

from evals.runners.rag_eval import ablations, ndcg, parse_combo
from rag.index.bm25 import BM25, tokenize
from rag.pipeline import rrf
from rag.query import requirements
from rag.verify import clean_draft, is_claim, split_sentences


def test_tokenize_keeps_tool_names():
    assert {"next.js", "c++", "dbt"} <= set(tokenize("Next.js app with C++ and dbt."))


def test_bm25_prefers_exact_tool_name():
    idx = BM25(["Built pipelines with Celery and Redis.", "Built pipelines with Airflow."])
    assert idx.top("celery workers", 1)[0][0] == 0


def test_rrf_rewards_agreement():
    assert rrf([[1, 2, 3], [2, 1, 3]])[:2] in ([1, 2], [2, 1])
    assert rrf([[1, 2], [3, 2]])[0] == 2


def test_requirements_extracts_needs():
    job = {"title": "RAG chatbot", "skills": ["Python", "LLM"],
           "description": "We need a chatbot over our PDFs. It must cite sources. Must use Python. Nice office."}
    reqs = requirements(job)
    assert any("cite sources" in r for r in reqs) and reqs[-1].startswith("RAG chatbot: experience with")
    assert not any("Nice office" in r for r in reqs)


def test_claim_detection():
    assert is_claim("I cut handling time by 33% for a media client.")
    assert not is_claim("I would start by agreeing on metrics.")
    assert not is_claim("Could you share the data?")
    assert not is_claim("Hi there,")


def test_clean_draft_removes_only_unsupported():
    draft = "Hi there,\n\nI cut costs by 40%. I built a dashboard.\n\nCould we talk?"
    results = [{"sentence": s, "label": lab} for s, lab in zip(
        split_sentences(draft), ["not_a_claim", "unsupported", "supported", "not_a_claim"])]
    text, stats = clean_draft(draft, results)
    assert "40%" not in text and "dashboard" in text and stats["unsupported_rate"] == 0.5


def test_ndcg_perfect_and_empty():
    assert ndcg(["a", "b"], {"a": 2, "b": 1}) == 1.0
    assert ndcg(["x"], {"a": 2}) == 0.0


def test_ablation_removes_one_component_each():
    best = parse_combo("best=decompose,hybrid,rerank")
    names = {c.name for c in ablations(best)}
    assert names == {"best-decompose", "best-bm25", "best-dense", "best-rerank"}


def test_citation_markers_are_not_content_and_disclaimers_are_not_claims():
    from rag.verify import CITE_RE, _numbers, is_claim
    s = "I cut handling time by 33% for an e-commerce client [ev_b83609f70b, rev_1a2b]."
    body = CITE_RE.sub("", s)
    assert _numbers(body) == {"33"}
    assert CITE_RE.findall(s) == ["ev_b83609f70b, rev_1a2b"]
    assert is_claim(body)
    assert not is_claim("I have not worked with Kubernetes in production yet, so I would keep it simple.")
    assert not is_claim("I haven't used Snowflake before; the SQL itself carries over.")

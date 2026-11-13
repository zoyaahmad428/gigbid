"""Skill normalisation and job-to-persona mapping.

Real postings spell the same skill many ways ("ReactJS", "React.js", "react").
`normalise_skill` maps them to one canonical name; `categorise` assigns a job a
category and the personas it fits, from its title, description and skills.
"""
from __future__ import annotations

import re

# canonical skill -> aliases (lower-case, matched as whole words)
SKILL_ALIASES: dict[str, list[str]] = {
    # AI / ML
    "Python": ["python", "python3"],
    "PyTorch": ["pytorch", "torch"],
    "TensorFlow": ["tensorflow", "tf2", "keras"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "LangChain": ["langchain"],
    "LangGraph": ["langgraph"],
    "LLM": ["llm", "llms", "large language model", "large language models", "gpt", "chatgpt",
            "openai api", "openai", "gpt-4", "claude", "llama"],
    "RAG": ["rag", "retrieval augmented generation", "retrieval-augmented generation"],
    "Vector Database": ["pinecone", "weaviate", "chroma", "chromadb", "qdrant", "faiss",
                        "vector database", "pgvector", "milvus"],
    "NLP": ["nlp", "natural language processing", "text classification", "named entity recognition"],
    "Computer Vision": ["computer vision", "opencv", "image classification", "object detection",
                        "yolo", "image segmentation"],
    "Machine Learning": ["machine learning", "ml", "predictive modeling", "predictive modelling"],
    "Deep Learning": ["deep learning", "neural network", "neural networks", "cnn", "transformers",
                      "hugging face", "huggingface"],
    "MLOps": ["mlops", "mlflow", "kubeflow", "model deployment", "sagemaker", "vertex ai"],
    "Fine-tuning": ["fine-tuning", "fine tuning", "lora", "qlora", "peft"],
    # Full stack
    "JavaScript": ["javascript", "js", "es6"],
    "TypeScript": ["typescript", "ts"],
    "React": ["react", "reactjs", "react.js"],
    "Next.js": ["next.js", "nextjs", "next js"],
    "Vue.js": ["vue", "vue.js", "vuejs", "nuxt", "nuxt.js"],
    "Angular": ["angular", "angularjs"],
    "Node.js": ["node.js", "nodejs", "node", "express", "express.js", "expressjs", "nestjs"],
    "Django": ["django", "django rest framework", "drf"],
    "FastAPI": ["fastapi"],
    "Flask": ["flask"],
    "Laravel": ["laravel"],
    "PHP": ["php"],
    "Ruby on Rails": ["ruby on rails", "rails", "ruby"],
    "PostgreSQL": ["postgresql", "postgres"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongodb", "mongo", "mongoose"],
    "Redis": ["redis"],
    "Celery": ["celery"],
    "GraphQL": ["graphql", "apollo"],
    "REST API": ["rest api", "restful api", "api development", "api integration", "rest"],
    "Docker": ["docker", "docker compose", "containerization"],
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda", "aws lambda"],
    "Tailwind CSS": ["tailwind", "tailwind css", "tailwindcss"],
    "HTML/CSS": ["html", "css", "html5", "css3", "sass", "scss"],
    "Stripe": ["stripe", "payment gateway integration"],
    "WordPress": ["wordpress", "woocommerce"],
    "Shopify": ["shopify"],
    # Data analysis
    "SQL": ["sql", "t-sql", "pl/sql"],
    "Excel": ["excel", "microsoft excel", "spreadsheets", "vba", "google sheets"],
    "Power BI": ["power bi", "powerbi", "dax", "power query"],
    "Tableau": ["tableau"],
    "Looker": ["looker", "looker studio", "google data studio", "data studio"],
    "pandas": ["pandas", "numpy"],
    "dbt": ["dbt", "data build tool"],
    "Snowflake": ["snowflake"],
    "BigQuery": ["bigquery", "google bigquery"],
    "Data Visualization": ["data visualization", "data visualisation", "dashboard", "dashboards",
                           "matplotlib", "seaborn", "plotly"],
    "Statistics": ["statistics", "statistical analysis", "a/b testing", "hypothesis testing",
                   "regression analysis", "spss", "stata"],
    "R": ["r", "rstudio", "tidyverse", "ggplot2"],
    "ETL": ["etl", "elt", "data pipeline", "data pipelines", "airflow", "apache airflow", "data engineering"],
    "Data Analysis": ["data analysis", "data analytics", "data analyst", "business intelligence",
                      "data cleaning", "data mining", "analytics"],
    "Web Scraping": ["web scraping", "scraping", "scrapy", "beautifulsoup", "selenium", "data scraping"],
}

_ALIAS_TO_CANON: dict[str, str] = {}
for canon, aliases in SKILL_ALIASES.items():
    _ALIAS_TO_CANON[canon.lower()] = canon
    for a in aliases:
        _ALIAS_TO_CANON[a] = canon

PERSONA_SKILLS: dict[str, set[str]] = {
    "ai_ml": {"PyTorch", "TensorFlow", "scikit-learn", "LangChain", "LangGraph", "LLM", "RAG",
              "Vector Database", "NLP", "Computer Vision", "Machine Learning", "Deep Learning",
              "MLOps", "Fine-tuning"},
    "fullstack": {"JavaScript", "TypeScript", "React", "Next.js", "Vue.js", "Angular", "Node.js",
                  "Django", "FastAPI", "Flask", "Laravel", "PHP", "Ruby on Rails", "MongoDB",
                  "GraphQL", "REST API", "Tailwind CSS", "HTML/CSS", "Stripe", "Redis", "Celery"},
    "data_analyst": {"SQL", "Excel", "Power BI", "Tableau", "Looker", "pandas", "dbt", "Snowflake",
                     "BigQuery", "Data Visualization", "Statistics", "R", "ETL", "Data Analysis"},
}

# Category is assigned from the dominant persona; jobs matching none are "other".
CATEGORY_OF_PERSONA = {"ai_ml": "AI & Machine Learning",
                       "fullstack": "Web & Full-Stack Development",
                       "data_analyst": "Data Analysis & BI"}

_WORD_RE_CACHE: dict[str, re.Pattern] = {}


def normalise_skill(raw: str) -> str | None:
    """Return the canonical skill name, or None if the string is not in the taxonomy."""
    key = re.sub(r"\s+", " ", raw.strip().lower())
    return _ALIAS_TO_CANON.get(key)


def normalise_skills(raw: list[str]) -> list[str]:
    out: list[str] = []
    for r in raw:
        c = normalise_skill(r)
        if c and c not in out:
            out.append(c)
    return out


# Aliases too ambiguous to trust in free text ("r", "ml", "LoRa" radio, "rest",
# Google Ads "fine tuning"); accepted only from explicit skill/tag lists.
AMBIGUOUS_IN_TEXT = {"r", "ml", "js", "ts", "rest", "node", "gpt", "rails", "ruby", "torch", "html",
                     "css", "excel", "analytics", "dashboard", "dashboards", "angular", "express",
                     "claude", "llama", "mongo", "lora", "fine tuning", "fine-tuning", "s3", "ec2",
                     "lambda", "drf", "dax", "tf2", "vba", "statistics", "scraping", "spreadsheets"}

_TEXT_ALIASES = sorted((a for a in _ALIAS_TO_CANON if a not in AMBIGUOUS_IN_TEXT), key=len, reverse=True)
_TEXT_RE = re.compile(r"(?<![\w.])(" + "|".join(re.escape(a) for a in _TEXT_ALIASES) + r")(?!\w)")


def skills_in_text(text: str) -> list[str]:
    """Find taxonomy skills mentioned in free text (whole-word, case-insensitive),
    using one compiled alternation (longest alias first)."""
    found: list[str] = []
    for m in _TEXT_RE.finditer(text.lower()):
        c = _ALIAS_TO_CANON[m.group(1)]
        if c not in found:
            found.append(c)
    return found


def persona_scores(skills: list[str]) -> dict[str, int]:
    s = set(skills)
    return {p: len(s & ps) for p, ps in PERSONA_SKILLS.items()}


def categorise(skills: list[str], title_skills: list[str] | None = None) -> tuple[str, list[str]]:
    """Return (category, personas that could plausibly bid).

    A persona fits when the job names at least two of its skills, or one of its
    skills appears in the title (titles are short and rarely mention skills by
    accident). One stray mention in a long description is not enough."""
    scores = persona_scores(skills)
    in_title = persona_scores(title_skills or [])
    fits = [p for p in scores if scores[p] >= 2 or in_title[p] >= 1]
    if not fits:
        return "Other", []
    top = max(fits, key=lambda p: (scores[p], in_title[p]))
    return CATEGORY_OF_PERSONA[top], fits

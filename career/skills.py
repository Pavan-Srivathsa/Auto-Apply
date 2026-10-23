"""Which skills employers ask for, how often, which of them the person lacks,
and which skills travel together for each kind of role.

No model is called. Every role Autopilot has judged already carries its
requirement lines, each marked meets / partly / missing against the person's
resume and stories: the Scout fits (`watch.listed[].fit.items`) and the
applications (`qualify.json`). A curated dictionary (`TAXONOMY`) finds the
skills in those lines by alias ("k8s" is Kubernetes, "golang" is Go) and
puts each in a group; frequent words no skill matches are reported so the
dictionary can grow.

Counts are per role, not per line: a posting that names Python three times is
one role asking for Python. A skill is *missing* for a role when any line that
names it was judged missing, *partly* when the best line was partly met.
Bundles are skill pairs that appear together in a role family's postings more
often than chance (lift), with enough roles behind them to mean something.

`build()` recomputes everything from the sources and keeps a dated snapshot
in `data/skills/history.json`, so demand can be followed over time.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Optional

import paths

from .snapshot import role_family

# canonical skill: (group, aliases). Aliases are matched as whole words, case-insensitive.
TAXONOMY: dict[str, tuple[str, list[str]]] = {
    # Languages
    "Python": ("Languages", ["python"]), "Java": ("Languages", ["java"]), "Go": ("Languages", ["golang", r"go(?= |,|/|\))"]),
    "Rust": ("Languages", ["rust"]), "C++": ("Languages", [r"c\+\+", "cpp"]), "C": ("Languages", [r"(?<![\w+#])c(?=[,/ ]|$)(?! ?#)"]),
    "C#": ("Languages", [r"c#", r"\.net"]), "TypeScript": ("Languages", ["typescript", "ts"]),
    "JavaScript": ("Languages", ["javascript", "js", "node.js", "nodejs", "node"]), "Scala": ("Languages", ["scala"]),
    "Kotlin": ("Languages", ["kotlin"]), "Swift": ("Languages", ["swift"]), "Ruby": ("Languages", ["ruby", "rails"]),
    "SQL": ("Languages", ["sql"]), "R": ("Languages", [r"r(?= programming| language|,)"]), "Bash": ("Languages", ["bash", "shell scripting"]),
    "CUDA": ("Systems & performance", ["cuda"]), "Triton": ("Systems & performance", ["triton"]),
    # ML frameworks and methods
    "PyTorch": ("ML frameworks", ["pytorch", "torch"]), "TensorFlow": ("ML frameworks", ["tensorflow", "keras"]),
    "JAX": ("ML frameworks", ["jax"]), "scikit-learn": ("ML frameworks", ["scikit-learn", "sklearn"]),
    "XGBoost": ("ML frameworks", ["xgboost", "lightgbm", "gradient boosting"]),
    "Hugging Face": ("ML frameworks", ["hugging ?face", "transformers library"]),
    "Machine learning": ("ML methods", ["machine learning", "ml"]),
    "Deep learning": ("ML methods", ["deep learning", "neural networks?"]),
    "NLP": ("ML methods", ["nlp", "natural language processing"]),
    "Computer vision": ("ML methods", ["computer vision", r"\bcv\b", "image recognition"]),
    "Recommender systems": ("ML methods", ["recommend(?:er|ation)s?(?: systems?)?", "personali[sz]ation", "ranking"]),
    "Reinforcement learning": ("ML methods", ["reinforcement learning", r"\brl(?:hf)?\b"]),
    "Statistics": ("ML methods", ["statistic(?:s|al)", "hypothesis testing", "causal inference"]),
    "A/B testing": ("ML methods", ["a/b test(?:s|ing)?", "experimentation"]),
    "Forecasting": ("ML methods", ["forecast(?:ing)?", "time series"]),
    "Model evaluation": ("ML methods", ["evals?", "evaluation", "benchmark(?:s|ing)?"]),
    "Fine-tuning": ("LLM & agents", ["fine-?tun(?:e|ing)", "post-training", "sft", "lora", "qlora"]),
    # LLM & agents
    "LLM": ("LLM & agents", ["llms?", "large language models?", "foundation models?", "generative ai", "genai", "gen ai"]),
    "RAG": ("LLM & agents", ["rag", "retrieval[- ]augmented", "retrieval"]),
    "Agents": ("LLM & agents", ["agents?", "agentic", "tool use", "function calling"]),
    "Prompt engineering": ("LLM & agents", ["prompt(?:ing| engineering)?"]),
    "Embeddings / vector search": ("LLM & agents", ["embeddings?", "vector (?:search|databases?|stores?)", "semantic search", "pinecone", "faiss", "weaviate"]),
    "LangChain / LlamaIndex": ("LLM & agents", ["langchain", "llamaindex", "langgraph"]),
    "OpenAI / Anthropic APIs": ("LLM & agents", ["openai", "anthropic", "claude", "gpt-?\\d?"]),
    "Inference serving": ("LLM & agents", ["inference", "model serving", "vllm", "tensorrt", "sglang", "triton inference"]),
    # Data engineering
    "Spark": ("Data engineering", ["spark", "pyspark"]), "Kafka": ("Data engineering", ["kafka", "streaming"]),
    "Airflow": ("Data engineering", ["airflow", "dagster", "orchestration"]), "dbt": ("Data engineering", ["dbt"]),
    "ETL / pipelines": ("Data engineering", ["etl", "elt", "data pipelines?", "pipelines?"]),
    "Data warehouse": ("Data engineering", ["snowflake", "bigquery", "redshift", "databricks", "data warehous(?:e|ing)", "lakehouse"]),
    "Pandas / NumPy": ("Data engineering", ["pandas", "numpy"]),
    # Databases
    "PostgreSQL": ("Databases", ["postgres(?:ql)?"]), "MySQL": ("Databases", ["mysql"]), "MongoDB": ("Databases", ["mongo(?:db)?"]),
    "Redis": ("Databases", ["redis"]), "NoSQL": ("Databases", ["nosql", "dynamodb", "cassandra"]),
    "Elasticsearch": ("Databases", ["elastic(?:search)?", "opensearch"]),
    # Cloud and infra
    "AWS": ("Cloud & infra", ["aws", "amazon web services", "s3", "ec2", "lambda", "sagemaker"]),
    "GCP": ("Cloud & infra", ["gcp", "google cloud", "vertex"]), "Azure": ("Cloud & infra", ["azure"]),
    "Kubernetes": ("Cloud & infra", ["kubernetes", "k8s", "helm"]), "Docker": ("Cloud & infra", ["docker", "containers?"]),
    "Terraform": ("Cloud & infra", ["terraform", "infrastructure as code", "iac"]),
    "CI/CD": ("Cloud & infra", ["ci/cd", "ci", "devops", "continuous (?:integration|delivery|deployment)", "github actions", "jenkins"]),
    "Linux": ("Cloud & infra", ["linux", "unix"]), "Observability": ("Cloud & infra", ["observability", "monitoring", "logging", "prometheus", "grafana", "datadog", "telemetry"]),
    "Distributed systems": ("Systems & performance", ["distributed systems?", "distributed computing", "scalab(?:le|ility)", "high[- ]availability"]),
    "GPU / HPC": ("Systems & performance", ["gpus?", "hpc", "high performance computing"]),
    "Performance optimization": ("Systems & performance", ["latency", "throughput", "performance optimi[sz]ation", "profiling"]),
    "Networking": ("Systems & performance", ["networking", "tcp", "http", "grpc"]),
    "Security": ("Systems & performance", ["security", "authentication", "encryption"]),
    # Backend / product
    "APIs": ("Backend & product", ["apis?", "rest(?:ful)?", "graphql", "microservices?"]),
    "Backend": ("Backend & product", ["backend", "back-end", "server-side"]),
    "FastAPI / Flask / Django": ("Backend & product", ["fastapi", "flask", "django"]),
    "React": ("Frontend", ["react", "next\\.js", "nextjs"]), "Frontend": ("Frontend", ["frontend", "front-end", "ui", "css", "html"]),
    "Mobile": ("Frontend", ["ios", "android", "mobile"]),
    "System design": ("Practices", ["system design", "architecture", "design patterns"]),
    "Testing": ("Practices", ["testing", "unit tests?", "test automation", "tdd"]),
    "MLOps": ("MLOps", ["mlops", "model deployment", "deploy(?:ing)? models", "mlflow", "kubeflow", "feature stores?", "model monitoring"]),
    "Data analysis": ("Data science", ["data analysis", "analytics", "dashboards?", "tableau", "power bi", "looker"]),
    "Degree: MS/PhD": ("Education", ["master'?s", "ph\\.?d", "m\\.s\\."]),
    "Degree: Bachelor's": ("Education", ["bachelor'?s?", "bs", "b\\.s\\.", "undergraduate degree"]),
    "Communication": ("Soft skills", ["communication", "collaborat(?:e|ion)", "stakeholders?"]),
    "Ownership": ("Soft skills", ["ownership", "self-starter", "autonomy", "thrive"]),
}
GROUPS = sorted({g for g, _ in TAXONOMY.values()})
# A resume shows these poorly, so a "missing" verdict on them says little:
# counted for demand, kept out of the gap list.
NOT_GAPS = {"Soft skills", "Education"}
_PATTERNS = {name: re.compile(r"(?<![\w])(?:" + "|".join(aliases) + r")(?![\w])", re.I) for name, (_, aliases) in TAXONOMY.items()}
# One-letter and common-word languages only as written: "go above and beyond"
# is not Go (2026-10-23).
_PATTERNS.update({
    "Go": re.compile(r"(?<![\w])(?:Go(?![\w-])|[Gg]olang)"),
    "C": re.compile(r"(?<![\w/+#])C(?![\w+#])(?!\s*\+\+)"),
    "R": re.compile(r"(?<![\w])R(?![\w&/])"),
})
LEVEL_RANK = {"meets": 2, "partly": 1, "missing": 0}
MIN_BUNDLE_ROLES = 3
STOP = set("the and with for of in to a an or on as at by from experience strong ability knowledge understanding years "
           "year plus working work building build using use proficiency proficient familiarity familiar excellent "
           "skills skill solid deep hands-on hands on related including such similar etc tools tooling systems system "
           "is are be will you your our we this that their other e.g".split())


def skills_in(text: str) -> list[str]:
    return [name for name, pattern in _PATTERNS.items() if pattern.search(text or "")]


def _roles() -> list[dict]:
    """Every judged role once: (company, title) is the key."""
    from scout import store
    roles: dict[str, dict] = {}

    def add(key, title, company, source, items):
        if not items or key in roles:
            return
        roles[key] = {"title": title, "company": company, "family": role_family(title), "source": source,
                      "items": [{"text": i.get("text", ""), "kind": i.get("kind", "must"), "level": i.get("level", "")}
                                for i in items]}

    for app in sorted(paths.APPLICATIONS.glob("*/qualify.json")):
        try:
            data = json.loads(app.read_text())
            job = json.loads((app.parent / "job.json").read_text())
        except (OSError, ValueError):
            continue
        key = f"{job.get('company', '')}|{job.get('title', '')}".lower()
        add(key, job.get("title", ""), job.get("company", ""), "application", data.get("items", []))
    for w in store.watches():
        for r in w.listed:
            fit = r.get("fit") or {}
            key = f"{r.get('company') or w.company}|{r['title']}".lower()
            add(key, r["title"], r.get("company") or w.company, "scout", fit.get("items", []))
    return list(roles.values())


def build(save: bool = True) -> dict:
    roles = _roles()
    per_skill: dict[str, Counter] = defaultdict(Counter)
    family_roles: Counter = Counter()
    family_skill: dict[str, Counter] = defaultdict(Counter)
    role_sets: list[tuple[str, set]] = []
    unknown: Counter = Counter()
    for role in roles:
        family_roles[role["family"]] += 1
        best: dict[str, int] = {}
        required: set = set()
        for item in role["items"]:
            found = skills_in(item["text"])
            if not found:
                unknown.update(w for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}", item["text"])
                               if w.lower() not in STOP and (w[0].isupper() or any(c.isdigit() for c in w)))
            for skill in found:
                rank = LEVEL_RANK.get(item["level"], -1)
                best[skill] = max(best.get(skill, -1), rank)
                if item["kind"] == "must":
                    required.add(skill)
        for skill, rank in best.items():
            c = per_skill[skill]
            c["roles"] += 1
            c["required"] += skill in required
            c[{2: "have", 1: "partly", 0: "missing"}.get(rank, "unjudged")] += 1
            c[f"family:{role['family']}"] += 1
            family_skill[role["family"]][skill] += 1
        role_sets.append((role["family"], set(best)))

    skills = []
    for name, c in per_skill.items():
        judged = c["have"] + c["partly"] + c["missing"]
        skills.append({"skill": name, "group": TAXONOMY[name][0], "roles": c["roles"], "required": c["required"],
                       "have": c["have"], "partly": c["partly"], "missing": c["missing"],
                       "gap_rate": round((c["missing"] + 0.5 * c["partly"]) / judged, 3) if judged else None,
                       "share": round(c["roles"] / max(1, len(roles)), 3),
                       "families": {k.split(":", 1)[1]: v for k, v in c.items() if k.startswith("family:")}})
    skills.sort(key=lambda s: -s["roles"])

    groups = defaultdict(lambda: Counter())
    for s in skills:
        g = groups[s["group"]]
        for k in ("roles", "have", "partly", "missing"):
            g[k] += s[k]
        g["skills"] += 1

    bundles = {}
    for family, n in family_roles.items():
        if n < MIN_BUNDLE_ROLES:
            continue
        sets = [s for f, s in role_sets if f == family]
        top = [{"skill": k, "roles": v, "share": round(v / n, 2)} for k, v in family_skill[family].most_common(12)]
        pair = Counter(p for s in sets for p in combinations(sorted(s), 2))
        single = family_skill[family]
        together = []
        for (a, b), k in pair.items():
            if k < MIN_BUNDLE_ROLES:
                continue
            lift = (k / n) / ((single[a] / n) * (single[b] / n))
            if lift >= 1.3:
                together.append({"skills": [a, b], "roles": k, "lift": round(lift, 2)})
        together.sort(key=lambda x: (-x["roles"] * math.log(1 + x["lift"])))
        bundles[family] = {"roles": n, "top": top, "together": together[:10]}

    out = {"built": date.today().isoformat(), "roles": len(roles),
           "sources": dict(Counter(r["source"] for r in roles)),
           "skills": skills,
           "groups": [{"group": g, **{k: int(v) for k, v in c.items()}} for g, c in sorted(groups.items(), key=lambda kv: -kv[1]["roles"])],
           "missing_most": sorted([s for s in skills if s["missing"] + s["partly"] >= 3 and s["group"] not in NOT_GAPS],
                                  key=lambda s: -(s["missing"] + 0.5 * s["partly"]))[:20],
           "by_family": bundles,
           "unrecognized": [{"term": t, "count": k} for t, k in unknown.most_common(40) if k >= 4]}
    if save:
        _save(out)
    return out


def _dir() -> Path:
    return Path(os.environ.get("AUTOPILOT_SKILLS") or paths.DATA / "skills")


def _save(out: dict) -> None:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.json").write_text(json.dumps(out, indent=1))
    history_path = folder / "history.json"
    try:
        history = json.loads(history_path.read_text())
    except (OSError, ValueError):
        history = {}
    history[out["built"]] = {"roles": out["roles"], "skills": {s["skill"]: s["roles"] for s in out["skills"]}}
    history_path.write_text(json.dumps(history, indent=1))


def trend(skill: str) -> list[tuple[str, int, int]]:
    """(day, roles asking for the skill, roles judged that day) per snapshot."""
    try:
        history = json.loads((_dir() / "history.json").read_text())
    except (OSError, ValueError):
        return []
    return [(day, row["skills"].get(skill, 0), row["roles"]) for day, row in sorted(history.items())]


def load() -> Optional[dict]:
    try:
        return json.loads((_dir() / "index.json").read_text())
    except (OSError, ValueError):
        return None

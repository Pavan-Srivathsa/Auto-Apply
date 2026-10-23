# Bullet reviewer benchmark (2026-10-22)

176 labeled pairs: 132 synthetic (bench/reviewer_pairs.py) and 44 from real resumes (kept out of the repository), rubric `mlds-2026-10-22.2`, reasoning model `z-ai/glm-5.3`. Thresholds were calibrated on the even pairs; the table is the odd (held-out) pairs only. Per-resume figures assume 14 changed bullets (an unchanged bullet costs nothing).

| mode | false accept | false reject | ok sent to human | precision | recall | FAIL recall | escalated | $ per resume | s per resume |
|---|---|---|---|---|---|---|---|---|---|
| jev | 0.0% | 0.0% | 36.1% | 76.4% | 100.0% | 100.0% | 0 | $0.0005 | 0.12 |
| llm | 0.0% | 0.0% | 0.0% | 100.0% | 100.0% | 85.7% | 82 | $0.0028 | 3.23 |
| hybrid | 0.0% | 0.0% | 0.0% | 100.0% | 100.0% | 100.0% | 21 | $0.0013 | 1.12 |

Jev run twice on all pairs: 5 verdicts changed; the lowest material P(accurate) moved 0.016 on average, 0.19 at most.

Calibrated thresholds: pass_at 0.7, fail_at 0.15, escalate_at 0.14.

## jev

Recall by category: bench_vs_opt 100%, causal 100%, deployment_claim 100%, fabricated_impact 100%, inflated_ownership 100%, latency_vs_throughput 100%, metric_attribution 100%, rag_embedding_wrong 100%, tech_substitution 100%, training_claim 100%, wrong_percentage 100%

## llm

Recall by category: bench_vs_opt 100%, causal 100%, deployment_claim 100%, fabricated_impact 100%, inflated_ownership 100%, latency_vs_throughput 100%, metric_attribution 100%, rag_embedding_wrong 100%, tech_substitution 100%, training_claim 100%, wrong_percentage 100%

## hybrid

Recall by category: bench_vs_opt 100%, causal 100%, deployment_claim 100%, fabricated_impact 100%, inflated_ownership 100%, latency_vs_throughput 100%, metric_attribution 100%, rag_embedding_wrong 100%, tech_substitution 100%, training_claim 100%, wrong_percentage 100%

"""The labeled benchmark for the ML/DS bullet reviewer (tailor/reviewer.py).

Synthetic and generic on purpose: the repository is public, so no pair is
anyone's real resume. Each pair is a source bullet and a rewrite, labeled:

- `ok`: a correct paraphrase. Rejecting it (FAIL) is a false rejection.
- `lossy`: true, but drops technical detail. Any verdict is acceptable;
  it is reported, not scored.
- `material`: states something the source does not. Passing it (PASS) is
  a false acceptance. `category` is the kind of error, for recall per kind.

`python bench/reviewer_pairs.py` writes `reviewer_pairs.jsonl` next to it.
"""
from __future__ import annotations

import json
from pathlib import Path

SOURCES = {
    "s1": "Fine-tuned a DistilBERT classifier on 120K support tickets, reaching 0.91 macro F1 on a held-out set of 15K tickets",
    "s2": "Benchmarked vLLM and TGI for batched inference on A100 GPUs, measuring p95 latency of 410 ms at 32 concurrent requests",
    "s3": "Built a retrieval pipeline that embeds 2M product descriptions with a sentence-transformer and serves top-10 results from FAISS, raising recall@10 from 0.62 to 0.78",
    "s4": "Contributed to a team of five building a fraud-detection service in PySpark; wrote the feature pipeline for 40 transaction features",
    "s5": "Analyzed an observed 9% rise in checkout conversion after a pricing change using a difference-in-differences design",
    "s6": "Trained an XGBoost churn model on 300K customer records, achieving 0.84 ROC-AUC on a 2023 holdout",
    "s7": "Deployed a quantized Llama 3 8B model with ONNX Runtime on CPU, cutting memory use by 55% versus the FP16 baseline",
    "s8": "Prototyped a LangChain agent that answers SQL questions over a Postgres warehouse for 12 internal analysts",
    "s9": "Reduced nightly ETL runtime from 6 hours to 90 minutes by rewriting Pandas jobs in PySpark on Databricks",
    "s10": "Evaluated three RAG chunking strategies on 500 labeled questions, reporting answer faithfulness of 0.81 for the best strategy",
    "s11": "Built Airflow DAGs that move 2M rows a day from Kafka into Snowflake for the analytics team",
    "s12": "Forecast weekly demand for 1,200 SKUs with Prophet, achieving 11.4% MAPE; planners used the forecasts to set reorder points",
}

# (source, rewrite, label, category)
PAIRS = [
    # s1 fine-tuning a classifier
    ("s1", "Fine-tuned DistilBERT to classify 120K support tickets, reaching 0.91 macro F1 on a 15K-ticket held-out set", "ok", "paraphrase"),
    ("s1", "Reached 0.91 macro F1 on 15K held-out tickets by fine-tuning a DistilBERT classifier on 120K support tickets", "ok", "paraphrase"),
    ("s1", "Fine-tuned a DistilBERT text classifier for support-ticket routing, scoring 0.91 macro F1 on held-out data", "lossy", "detail_loss"),
    ("s1", "Pre-trained a transformer language model from scratch on 120K support tickets, reaching 0.91 macro F1", "material", "training_claim"),
    ("s1", "Fine-tuned a DistilBERT classifier on 120K support tickets, reaching 0.91 accuracy on a held-out set of 15K tickets", "material", "metric_attribution"),
    ("s1", "Fine-tuned a RoBERTa classifier on 120K support tickets, reaching 0.91 macro F1 on a held-out set of 15K tickets", "material", "tech_substitution"),
    ("s1", "Fine-tuned and deployed a DistilBERT classifier serving all support tickets in production at 0.91 macro F1", "material", "deployment_claim"),
    ("s1", "Fine-tuned a DistilBERT classifier on 120K tickets (0.91 macro F1), cutting support response time by 40%", "material", "fabricated_impact"),
    ("s1", "Led the support-ticket ML initiative, fine-tuning DistilBERT on 120K tickets to 0.91 macro F1", "material", "inflated_ownership"),
    # s2 benchmarking inference
    ("s2", "Benchmarked vLLM against TGI for batched inference on A100s, measuring 410 ms p95 latency at 32 concurrent requests", "ok", "paraphrase"),
    ("s2", "Compared vLLM and TGI batched inference on A100 GPUs, recording p95 latency of 410 ms with 32 concurrent requests", "ok", "paraphrase"),
    ("s2", "Optimized vLLM batched inference on A100 GPUs, bringing p95 latency down to 410 ms at 32 concurrent requests", "material", "bench_vs_opt"),
    ("s2", "Benchmarked vLLM and TGI on A100 GPUs, sustaining 410 requests per second at 32 concurrent requests", "material", "latency_vs_throughput"),
    ("s2", "Benchmarked vLLM and TGI for batched inference on H100 GPUs, measuring p95 latency of 410 ms at 32 concurrent requests", "material", "tech_substitution"),
    ("s2", "Benchmarked vLLM and TGI on A100 GPUs, measuring p95 latency of 41 ms at 32 concurrent requests", "material", "wrong_percentage"),
    ("s2", "Benchmarked vLLM and TGI, then rolled vLLM out to production serving on A100 GPUs at 410 ms p95 latency", "material", "deployment_claim"),
    ("s2", "Benchmarked vLLM and TGI for batched GPU inference, measuring p95 latency under concurrent load", "lossy", "detail_loss"),
    ("s2", "Benchmarked vLLM and TGI on A100 GPUs at 32 concurrent requests, cutting serving costs by 30%", "material", "fabricated_impact"),
    # s3 retrieval
    ("s3", "Built a FAISS retrieval pipeline over 2M sentence-transformer embeddings of product descriptions, raising recall@10 from 0.62 to 0.78", "ok", "paraphrase"),
    ("s3", "Raised recall@10 from 0.62 to 0.78 with a retrieval pipeline that embeds 2M product descriptions and serves top-10 results from FAISS", "ok", "paraphrase"),
    ("s3", "Built a semantic product search pipeline with sentence-transformer embeddings and FAISS, improving recall@10 to 0.78", "ok", "paraphrase"),
    ("s3", "Built a RAG pipeline that generates product answers with an LLM over 2M descriptions, raising recall@10 from 0.62 to 0.78", "material", "rag_embedding_wrong"),
    ("s3", "Trained a custom embedding model on 2M product descriptions and served top-10 results from FAISS, raising recall@10 from 0.62 to 0.78", "material", "training_claim"),
    ("s3", "Built a retrieval pipeline over 2M product descriptions, raising precision@10 from 0.62 to 0.78", "material", "metric_attribution"),
    ("s3", "Built a retrieval pipeline over 2M product descriptions with Pinecone, raising recall@10 from 0.62 to 0.78", "material", "tech_substitution"),
    ("s3", "Built a retrieval pipeline over 2M product descriptions, raising recall@10 by 78%", "material", "wrong_percentage"),
    ("s3", "Built a product retrieval pipeline that lifted recall@10 from 0.62 to 0.78 and grew search revenue 12%", "material", "fabricated_impact"),
    # s4 ownership
    ("s4", "Wrote the 40-feature transaction pipeline in PySpark for a five-person team's fraud-detection service", "ok", "paraphrase"),
    ("s4", "Built the PySpark feature pipeline (40 transaction features) for a fraud-detection service developed by a team of five", "ok", "paraphrase"),
    ("s4", "Led a team of five building a fraud-detection service in PySpark, including a 40-feature pipeline", "material", "inflated_ownership"),
    ("s4", "Architected and built a fraud-detection service in PySpark with 40 transaction features", "material", "inflated_ownership"),
    ("s4", "Built a fraud-detection service in PySpark end to end, engineering 40 transaction features", "material", "inflated_ownership"),
    ("s4", "Wrote the feature pipeline for 40 transaction features in Scala Spark for a fraud-detection service", "material", "tech_substitution"),
    ("s4", "Wrote the 40-feature pipeline for a fraud-detection service that cut chargebacks by 25%", "material", "fabricated_impact"),
    ("s4", "Contributed the feature pipeline to a fraud-detection service in PySpark", "lossy", "detail_loss"),
    # s5 causal
    ("s5", "Analyzed a 9% observed rise in checkout conversion after a pricing change with a difference-in-differences design", "ok", "paraphrase"),
    ("s5", "Used difference-in-differences to analyze the 9% rise in checkout conversion seen after a pricing change", "ok", "paraphrase"),
    ("s5", "Increased checkout conversion by 9% by redesigning pricing, validated with difference-in-differences", "material", "causal"),
    ("s5", "Drove a 9% rise in checkout conversion through a pricing change, measured with difference-in-differences", "material", "causal"),
    ("s5", "Proved that a pricing change caused a 9% rise in checkout conversion", "material", "causal"),
    ("s5", "Analyzed a 19% rise in checkout conversion after a pricing change using difference-in-differences", "material", "wrong_percentage"),
    ("s5", "Analyzed a 9% rise in checkout conversion after a pricing change with a randomized A/B test", "material", "metric_attribution"),
    ("s5", "Analyzed a change in checkout conversion after a pricing change using a quasi-experimental design", "lossy", "detail_loss"),
    # s6 training a model
    ("s6", "Trained an XGBoost churn model on 300K customer records, reaching 0.84 ROC-AUC on a 2023 holdout", "ok", "paraphrase"),
    ("s6", "Reached 0.84 ROC-AUC on a 2023 holdout with an XGBoost churn model trained on 300K customer records", "ok", "paraphrase"),
    ("s6", "Built a gradient-boosted churn model on 300K customer records with 0.84 ROC-AUC on held-out 2023 data", "ok", "paraphrase"),
    ("s6", "Trained an XGBoost churn model on 300K customer records, reaching 0.84 accuracy on a 2023 holdout", "material", "metric_attribution"),
    ("s6", "Trained an XGBoost churn model on 3M customer records, reaching 0.84 ROC-AUC on a 2023 holdout", "material", "wrong_percentage"),
    ("s6", "Trained a deep neural network churn model on 300K customer records, reaching 0.84 ROC-AUC", "material", "tech_substitution"),
    ("s6", "Trained and deployed an XGBoost churn model that reduced customer churn by 15%", "material", "fabricated_impact"),
    ("s6", "Trained an XGBoost churn model on 300K records and shipped it as a real-time scoring API", "material", "deployment_claim"),
    ("s6", "Trained an XGBoost churn model with strong holdout performance", "lossy", "detail_loss"),
    # s7 deployment / inference
    ("s7", "Deployed Llama 3 8B quantized with ONNX Runtime on CPU, using 55% less memory than the FP16 baseline", "ok", "paraphrase"),
    ("s7", "Cut memory use 55% against an FP16 baseline by deploying a quantized Llama 3 8B on CPU with ONNX Runtime", "ok", "paraphrase"),
    ("s7", "Fine-tuned Llama 3 8B and deployed it quantized on CPU with ONNX Runtime, cutting memory use by 55%", "material", "training_claim"),
    ("s7", "Deployed a quantized Llama 3 8B model with ONNX Runtime on CPU, cutting inference latency by 55% versus FP16", "material", "latency_vs_throughput"),
    ("s7", "Deployed a quantized Llama 3 8B model with TensorRT on GPU, cutting memory use by 55%", "material", "tech_substitution"),
    ("s7", "Deployed a quantized Llama 3 70B model with ONNX Runtime on CPU, cutting memory use by 55%", "material", "wrong_percentage"),
    ("s7", "Deployed a quantized Llama 3 8B model on CPU, cutting infrastructure costs by 55%", "material", "fabricated_impact"),
    ("s7", "Deployed a quantized open-weights LLM on CPU with ONNX Runtime to reduce memory use", "lossy", "detail_loss"),
    # s8 prototype vs production
    ("s8", "Prototyped a LangChain agent answering SQL questions over a Postgres warehouse for 12 internal analysts", "ok", "paraphrase"),
    ("s8", "Built a LangChain prototype agent that lets 12 internal analysts ask SQL questions of a Postgres warehouse", "ok", "paraphrase"),
    ("s8", "Deployed a production LangChain agent answering SQL questions over a Postgres warehouse for 12 analysts", "material", "deployment_claim"),
    ("s8", "Shipped a LangChain agent to production that serves company-wide SQL questions over Postgres", "material", "deployment_claim"),
    ("s8", "Prototyped a LangChain agent for 120 internal analysts to query a Postgres warehouse", "material", "wrong_percentage"),
    ("s8", "Prototyped a LangChain agent that answers SQL questions over Snowflake for 12 analysts", "material", "tech_substitution"),
    ("s8", "Prototyped a LangChain agent for 12 analysts that saved the team 20 hours a week", "material", "fabricated_impact"),
    ("s8", "Owned the analytics assistant roadmap, prototyping a LangChain SQL agent for 12 analysts", "material", "inflated_ownership"),
    # s9 performance claims
    ("s9", "Cut nightly ETL runtime from 6 hours to 90 minutes by rewriting Pandas jobs in PySpark on Databricks", "ok", "paraphrase"),
    ("s9", "Rewrote Pandas ETL jobs in PySpark on Databricks, reducing the nightly run from 6 hours to 90 minutes", "ok", "paraphrase"),
    ("s9", "Rewrote Pandas ETL jobs in PySpark on Databricks, cutting nightly runtime 75%", "ok", "paraphrase"),
    ("s9", "Rewrote Pandas ETL jobs in PySpark, cutting nightly runtime by 90%", "material", "wrong_percentage"),
    ("s9", "Rewrote Pandas ETL jobs in PySpark, raising pipeline throughput 4x to 2M rows per second", "material", "latency_vs_throughput"),
    ("s9", "Rewrote Pandas ETL jobs in Dask on AWS EMR, cutting nightly runtime from 6 hours to 90 minutes", "material", "tech_substitution"),
    ("s9", "Rewrote ETL jobs in PySpark, saving $200K a year in compute", "material", "fabricated_impact"),
    ("s9", "Rewrote ETL jobs in PySpark on Databricks to speed up the nightly run", "lossy", "detail_loss"),
    # s10 evaluation vs optimisation
    ("s10", "Evaluated three RAG chunking strategies on 500 labeled questions; the best reached 0.81 answer faithfulness", "ok", "paraphrase"),
    ("s10", "Compared three chunking strategies for RAG on a 500-question labeled set, with the best at 0.81 faithfulness", "ok", "paraphrase"),
    ("s10", "Improved RAG answer faithfulness to 0.81 by redesigning document chunking", "material", "bench_vs_opt"),
    ("s10", "Optimized a RAG pipeline's chunking, raising answer faithfulness from 0.65 to 0.81", "material", "bench_vs_opt"),
    ("s10", "Evaluated three RAG chunking strategies on 500 labeled questions, reaching 0.81 retrieval recall", "material", "metric_attribution"),
    ("s10", "Evaluated three RAG chunking strategies on 5,000 labeled questions, reporting 0.81 faithfulness", "material", "wrong_percentage"),
    ("s10", "Evaluated embedding models by fine-tuning them for RAG on 500 labeled questions", "material", "rag_embedding_wrong"),
    ("s10", "Evaluated RAG chunking strategies on a labeled question set", "lossy", "detail_loss"),
    # s11 data engineering
    ("s11", "Built Airflow DAGs moving 2M rows a day from Kafka into Snowflake for the analytics team", "ok", "paraphrase"),
    ("s11", "Moved 2M rows daily from Kafka to Snowflake for analytics with Airflow DAGs I built", "ok", "paraphrase"),
    ("s11", "Built Airflow DAGs that stream 2M rows a second from Kafka into Snowflake", "material", "latency_vs_throughput"),
    ("s11", "Built Airflow DAGs that move 20M rows a day from Kafka into Snowflake", "material", "wrong_percentage"),
    ("s11", "Built Airflow DAGs that move 2M rows a day from Kafka into BigQuery", "material", "tech_substitution"),
    ("s11", "Architected the company data platform, moving 2M rows a day from Kafka into Snowflake", "material", "inflated_ownership"),
    ("s11", "Built Airflow DAGs from Kafka into Snowflake that cut reporting delays by 60%", "material", "fabricated_impact"),
    ("s11", "Built Airflow pipelines loading event data into a cloud warehouse", "lossy", "detail_loss"),
    # s12 forecasting
    ("s12", "Forecast weekly demand for 1,200 SKUs with Prophet at 11.4% MAPE; planners set reorder points from the forecasts", "ok", "paraphrase"),
    ("s12", "Built Prophet forecasts of weekly demand for 1,200 SKUs (11.4% MAPE) that planners used to set reorder points", "ok", "paraphrase"),
    ("s12", "Forecast weekly demand for 1,200 SKUs with Prophet, achieving 11.4% MAPE and reducing stockouts by 30%", "material", "fabricated_impact"),
    ("s12", "Forecast weekly demand for 1,200 SKUs with Prophet, achieving 11.4% accuracy", "material", "metric_attribution"),
    ("s12", "Forecast weekly demand for 12,000 SKUs with Prophet at 11.4% MAPE", "material", "wrong_percentage"),
    ("s12", "Forecast weekly demand for 1,200 SKUs with an LSTM at 11.4% MAPE", "material", "tech_substitution"),
    ("s12", "Forecast weekly demand with Prophet, and planners cut inventory costs because of the forecasts", "material", "causal"),
    ("s12", "Owned inventory planning for 1,200 SKUs, forecasting weekly demand with Prophet at 11.4% MAPE", "material", "inflated_ownership"),
    ("s12", "Forecast weekly SKU demand with Prophet for inventory planners", "lossy", "detail_loss"),
    # extra correct paraphrases, so false rejection is measured on more than one wording per source
    ("s2", "Measured 410 ms p95 latency at 32 concurrent requests while benchmarking vLLM and TGI batched inference on A100 GPUs", "ok", "paraphrase"),
    ("s4", "Contributed the 40-feature PySpark transaction pipeline to a five-engineer fraud-detection service", "ok", "paraphrase"),
    ("s7", "Ran a quantized Llama 3 8B on CPU via ONNX Runtime, using 55% less memory than FP16", "ok", "paraphrase"),
    ("s8", "Prototyped a natural-language-to-SQL agent in LangChain over Postgres for 12 internal analysts", "ok", "paraphrase"),
    ("s10", "Benchmarked three RAG chunking strategies against 500 labeled questions, best answer faithfulness 0.81", "ok", "paraphrase"),
    ("s11", "Developed Airflow DAGs loading 2M Kafka rows a day into Snowflake for analysts", "ok", "paraphrase"),
    ("s12", "Produced weekly Prophet demand forecasts for 1,200 SKUs at 11.4% MAPE, which planners used for reorder points", "ok", "paraphrase"),
    ("s1", "Fine-tuned a DistilBERT classifier on 120K support tickets, reaching 0.91 macro F1 on its training set", "material", "metric_attribution"),
    ("s1", "Trained a DistilBERT classifier on 120K support tickets to 0.91 macro F1 on 15K held-out tickets", "ok", "paraphrase"),
    ("s2", "Benchmarked vLLM and TGI on A100 GPUs, measuring average latency of 410 ms at 32 concurrent requests", "material", "metric_attribution"),
    ("s2", "Tuned vLLM and TGI batching on A100 GPUs until p95 latency held at 410 ms under 32 concurrent requests", "material", "bench_vs_opt"),
    ("s3", "Built a retrieval pipeline that embeds 2M product descriptions and serves top-10 FAISS results, with recall@10 at 0.78", "ok", "paraphrase"),
    ("s3", "Built a retrieval pipeline over 2M product descriptions whose embeddings were generated by an LLM, raising recall@10 from 0.62 to 0.78", "material", "rag_embedding_wrong"),
    ("s4", "Built the fraud-detection service's PySpark feature pipeline with 40 transaction features, as one of a team of five", "ok", "paraphrase"),
    ("s4", "Designed the fraud-detection service in PySpark and wrote its 40-feature transaction pipeline", "material", "inflated_ownership"),
    ("s5", "Showed with difference-in-differences that a pricing change lifted checkout conversion 9%", "material", "causal"),
    ("s5", "Measured with difference-in-differences the 9% checkout conversion rise observed after a pricing change", "ok", "paraphrase"),
    ("s6", "Trained an XGBoost churn model on 300K customer records, reaching 0.84 ROC-AUC on its training data", "material", "metric_attribution"),
    ("s6", "Developed an XGBoost churn classifier from 300K customer records, scoring 0.84 ROC-AUC on a 2023 holdout", "ok", "paraphrase"),
    ("s7", "Trained a quantized Llama 3 8B model with ONNX Runtime on CPU, cutting memory use by 55% versus FP16", "material", "training_claim"),
    ("s7", "Served a quantized Llama 3 8B with ONNX Runtime on CPU at 55% less memory than its FP16 baseline", "ok", "paraphrase"),
    ("s8", "Built a LangChain agent that answers SQL questions over a Postgres warehouse for 12 internal analysts", "ok", "paraphrase"),
    ("s8", "Prototyped a LangChain agent that writes and runs SQL over Postgres for 12 internal analysts in production", "material", "deployment_claim"),
    ("s9", "Rewrote Pandas jobs in PySpark on Databricks, cutting each nightly ETL query from 6 hours to 90 minutes", "material", "metric_attribution"),
    ("s9", "Moved the nightly ETL from Pandas to PySpark on Databricks, taking it from 6 hours to 90 minutes", "ok", "paraphrase"),
    ("s10", "Evaluated three RAG chunking strategies on 500 labeled questions, with all three above 0.81 answer faithfulness", "material", "metric_attribution"),
    ("s10", "Ran a 500-question labeled evaluation of three RAG chunking strategies, where the best scored 0.81 answer faithfulness", "ok", "paraphrase"),
    ("s11", "Built Airflow DAGs that move 2M rows a day from Kafka into Snowflake in real time for the analytics team", "material", "latency_vs_throughput"),
    ("s11", "Wrote Airflow DAGs for the analytics team that load 2M Kafka rows into Snowflake each day", "ok", "paraphrase"),
    ("s12", "Forecast weekly demand for 1,200 SKUs with Prophet at 11.4% MAPE, which set the planners' reorder points automatically", "material", "causal"),
    ("s12", "Forecast weekly demand for 1,200 SKUs with Prophet (11.4% MAPE), giving planners the numbers for reorder points", "ok", "paraphrase"),
]


def rows() -> list[dict]:
    return [{"id": f"p{n:03d}", "source_id": sid, "source": SOURCES[sid], "rewritten": text,
             "label": label, "category": category}
            for n, (sid, text, label, category) in enumerate(PAIRS, 1)]


if __name__ == "__main__":
    out = Path(__file__).with_name("reviewer_pairs.jsonl")
    data = rows()
    out.write_text("".join(json.dumps(r) + "\n" for r in data))
    print(f"{len(data)} pairs -> {out}")

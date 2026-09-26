"""Benchmark Ollama models on the human-labelled golden set.

Two views per model:
  llm      - the model alone (+ the deterministic automated-sender guard mAIl applies after it)
  pipeline - what production does: rule_engine first, the model only when rules say needs_llm
Metrics: accuracy, macro-F1, recall per category, JSON/schema failures, p50/p90 latency.
Critical errors are counted separately: human mail (appointment/question/complaint/other)
classified as noise would leave INBOX under hybrid routing - the costliest mistake.

  cd ~/Repos/priv/mAIl && PYTHONPATH=src .venv/bin/python ops/bench_models.py \
      --models qwen3:8b qwen3:14b gemma3:12b
Labels: data/golden/golden_labels.xlsx (column your_label) or .csv; blank rows are skipped.
--smoke N uses mAIl's own categories as labels on N samples, only to test the harness.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from mail_ai_agent.automated_sender import HUMAN_CATEGORIES, apply_automated_sender_guard
from mail_ai_agent.config import Settings
from mail_ai_agent.llm_gateway import LLMGateway
from mail_ai_agent.rule_engine import evaluate_rules
from mail_ai_agent.schemas import ParsedEmail

GOLD = Path("data/golden")
CATS = ["appointment", "question", "complaint", "other", "billing", "system", "newsletter", "offer", "spam"]


def _labels_from_xlsx(path: Path) -> dict[str, str]:
    """Minimal xlsx reader (no openpyxl in mAIl's venv): columns A=id, B=your_label."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    out = {}
    for row in sheet.iter(f"{{{ns['m']}}}row"):
        vals = {}
        for c in row.findall("m:c", ns):
            ref, t = c.get("r"), c.get("t")
            v = c.find("m:v", ns)
            is_ = c.find("m:is", ns)
            if v is not None:
                val = shared[int(v.text)] if t == "s" else v.text
            elif is_ is not None:
                val = "".join(x.text or "" for x in is_.iter(f"{{{ns['m']}}}t"))
            else:
                val = ""
            vals["".join(ch for ch in ref if ch.isalpha())] = (val or "").strip()
        if vals.get("A") and vals.get("A") != "id":
            out[vals["A"]] = vals.get("B", "").lower()
    return out


def load_labels() -> dict[str, str]:
    x = GOLD / "golden_labels.xlsx"
    if x.exists():
        labels = _labels_from_xlsx(x)
    else:
        import csv
        labels = {r["id"]: (r.get("your_label") or "").strip().lower()
                  for r in csv.DictReader((GOLD / "golden_labels.csv").open(encoding="utf-8"))}
    return {k: v for k, v in labels.items() if v in CATS}


def _parsed(s: dict) -> ParsedEmail:
    return ParsedEmail(sender=s["sender"], subject=s["subject"], normalized_body=s["body"],
                       plain_text_body=s["body"], headers=s.get("headers", {}))


def score(pairs: list[tuple[str, str]]) -> dict:
    n = len(pairs)
    acc = sum(p == g for g, p in pairs) / n if n else 0.0
    f1s, recall = [], {}
    for c in CATS:
        tp = sum(1 for g, p in pairs if g == c and p == c)
        fp = sum(1 for g, p in pairs if g != c and p == c)
        fn = sum(1 for g, p in pairs if g == c and p != c)
        if tp + fn == 0:
            continue
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn)
        recall[c] = round(rec, 2)
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    critical = sum(1 for g, p in pairs if g in HUMAN_CATEGORIES and p not in HUMAN_CATEGORIES)
    return {"n": n, "accuracy": round(acc, 3), "macro_f1": round(sum(f1s) / len(f1s), 3) if f1s else 0,
            "human_mail_lost": critical, "recall": recall}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env.multi.prod")
    ap.add_argument("--models", nargs="+", default=["qwen3:8b"])
    ap.add_argument("--smoke", type=int, default=0)
    args = ap.parse_args()
    base = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    samples = [json.loads(l) for l in (GOLD / "golden_sample.jsonl").read_text(encoding="utf-8").splitlines() if l]
    if args.smoke:
        labels = {s["id"]: s["mail_category"] for s in samples[: args.smoke]}
    else:
        labels = load_labels()
    samples = [s for s in samples if s["id"] in labels]
    if not samples:
        raise SystemExit("No labelled rows yet: fill column 'your_label' in data/golden/golden_labels.xlsx")
    box = {m.mailbox_id: m for m in base.load_mailboxes()}
    results = {}
    import mail_ai_agent.llm_gateway as gwmod

    original_payload = gwmod._build_generate_payload
    for spec in args.models:
        # "model#nothink" -> same model with Ollama's think=false (qwen3 otherwise emits thousands of
        # reasoning tokens per mail); plain "model" -> exactly what production sends today
        model, _, variant = spec.partition("#")
        if variant == "nothink":
            gwmod._build_generate_payload = lambda **kw: {**original_payload(**kw), "think": False}
        else:
            gwmod._build_generate_payload = original_payload
        settings = base.model_copy(update={"ollama_model": model, "max_retries": 2})
        gw = LLMGateway(settings)
        model = spec
        llm_pairs, pipe_pairs, lat, failures = [], [], [], 0
        for s in samples:
            parsed = _parsed(s)
            gold = labels[s["id"]]
            t0 = time.perf_counter()
            try:
                cls, _ = gw.classify(parsed)
                cls = apply_automated_sender_guard(cls, parsed, base.automated_sender_keep)
                llm_cat = cls.category
            except Exception:
                failures += 1
                llm_cat = "other"
            lat.append(time.perf_counter() - t0)
            llm_pairs.append((gold, llm_cat))
            rule = evaluate_rules(parsed, box[s["mailbox"]])
            pipe_pairs.append((gold, llm_cat if rule.action == "needs_llm" else rule.category))
        lat.sort()
        results[model] = {
            "llm": score(llm_pairs), "pipeline": score(pipe_pairs), "failures": failures,
            "p50_s": round(statistics.median(lat), 2), "p90_s": round(lat[int(len(lat) * 0.9) - 1], 2),
        }
        print(model, json.dumps(results[model], ensure_ascii=False), flush=True)
    out = GOLD / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"labels": len(samples), "smoke": bool(args.smoke), "results": results}, indent=2))
    print("\n| model | pipeline acc | macro-F1 | human mail lost | LLM-only acc | failures | p50 s |")
    print("|---|---|---|---|---|---|---|")
    for m, r in results.items():
        print(f"| {m} | {r['pipeline']['accuracy']:.0%} | {r['pipeline']['macro_f1']:.2f} | "
              f"{r['pipeline']['human_mail_lost']} | {r['llm']['accuracy']:.0%} | {r['failures']} | {r['p50_s']} |")
    print("saved ->", out)


if __name__ == "__main__":
    main()

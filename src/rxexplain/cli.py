

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import SYSTEMS, __version__
from .config import GOLD_PATH, RESULTS_DIR, load_azure_config
from .safety import format_flags


def _read_input(args: argparse.Namespace) -> str:
    if getattr(args, "text", None):
        return args.text
    if getattr(args, "file", None):
        return Path(args.file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            return data
    raise SystemExit("no prescription text: pass --text, --file, or pipe it on stdin")


def _client(require: bool = True):
    from .llm import LLMClient

    cfg = load_azure_config(require=require)
    if cfg is None:
        return None
    return LLMClient(cfg)


# ------------------------------------------------------------------- doctor
def cmd_doctor(args: argparse.Namespace) -> int:
    from .drugkb import kb_stats
    from .evaluate import load_gold
    from .retrieval import cache_stats, rxnorm_id

    print(f"rxexplain {__version__}")
    ok = True

    print("\n[1] Knowledge base")
    try:
        stats = kb_stats()
        print(f"    drugs={stats['drugs']} brand_aliases={stats['brand_aliases']} "
              f"high_risk={stats['high_risk']}")
    except Exception as exc:
        ok = False
        print(f"    FAILED: {exc}")

    print("\n[2] Gold set")
    try:
        cases = load_gold()
        checks = sum(len(c.must_include) for c in cases)
        print(f"    {len(cases)} cases, {checks} must-include checks  ({GOLD_PATH})")
    except Exception as exc:
        ok = False
        print(f"    FAILED: {exc}")

    print("\n[3] Metric libraries")
    for mod in ("rouge_score", "sacrebleu", "textstat"):
        try:
            __import__(mod)
            print(f"    {mod}: ok")
        except Exception as exc:
            ok = False
            print(f"    {mod}: FAILED ({exc})")

    print("\n[4] Retrieval (openFDA / RxNorm)")
    print(f"    cache: {cache_stats()}")
    if args.offline:
        print("    skipped (--offline)")
    else:
        try:
            rxcui = rxnorm_id("paracetamol")
            print(f"    RxNorm lookup 'paracetamol' -> rxcui={rxcui}")
            if not rxcui:
                print("    WARNING: no RxCUI returned; retrieval will degrade gracefully")
        except Exception as exc:
            print(f"    WARNING: {exc}")

    print("\n[5] Azure LLM")
    cfg = load_azure_config(require=False)
    if cfg is None:
        print("    NOT CONFIGURED. Copy .env.example to .env and fill in:")
        print("      AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT")
        print("    The rule-based baseline, parser and safety layer work without it.")
        return 0 if ok else 1

    from .llm import LLMClient, LLMError

    client = LLMClient(cfg)
    for key, val in client.describe().items():
        shown = val if key != "endpoint" else val
        print(f"    {key}: {shown}")
    try:
        probe = client.probe()
        print(f"    probe: reply={probe['reply']!r} model={probe['model']} "
              f"latency={probe['latency_s']}s")
        print(f"    dialect: {probe['dialect']}")
        if not probe["ok"]:
            ok = False
            print("    FAILED: empty reply")
    except LLMError as exc:
        ok = False
        print(f"    FAILED: {exc}")

    print("\n" + ("All checks passed." if ok else "Some checks failed (see above)."))
    return 0 if ok else 1


# -------------------------------------------------------------------- parse
def cmd_parse(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from .parser_rules import parse, parsed_summary
    from .safety import check

    text = _read_input(args)
    parsed = parse(text)
    if args.json:
        print(json.dumps(
            {
                "medications": [asdict(m) for m in parsed.medications],
                "general_instructions": parsed.general_instructions,
                "unparsed_lines": parsed.unparsed_lines,
                "coverage": parsed.coverage,
                "safety_flags": [asdict(f) for f in check(parsed)],
            },
            indent=2,
        ))
        return 0

    print(parsed_summary(parsed))
    print(f"\ncoverage: {parsed.coverage:.2f}  unparsed: {len(parsed.unparsed_lines)}")
    flags = check(parsed)
    if flags:
        print("\nSAFETY FLAGS")
        print(format_flags(flags))
    return 0


# ------------------------------------------------------------------ explain
def cmd_explain(args: argparse.Namespace) -> int:
    from .systems import run_system

    text = _read_input(args)
    client = None if args.system == "rule" else _client(require=True)
    exp = run_system(
        args.system, "cli", text, client,
        offline=args.offline, verify=not args.no_verify,
    )
    if args.json:
        from dataclasses import asdict

        print(json.dumps(asdict(exp), indent=2, default=str))
        return 0 if not exp.error else 1

    if exp.error:
        print(f"ERROR: {exp.error}", file=sys.stderr)
        if not exp.output_text:
            return 1
    print(exp.output_text)
    if args.verbose:
        print(f"\n--- {exp.system}: {exp.latency_s:.2f}s, {exp.llm_calls} LLM call(s), "
              f"{exp.completion_tokens} completion tokens")
        if exp.meta:
            print(f"--- meta: {json.dumps(exp.meta, default=str)}")
    return 0


# ------------------------------------------------------------------ compare
def cmd_compare(args: argparse.Namespace) -> int:
    from .systems import run_system

    text = _read_input(args)
    client = _client(require=not args.rule_only)
    systems = ["rule"] if args.rule_only else list(SYSTEMS)
    for system in systems:
        print("=" * 78)
        print(f"SYSTEM: {system}")
        print("=" * 78)
        try:
            exp = run_system(
                system, "cli", text, client,
                offline=args.offline, verify=not args.no_verify,
            )
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}\n")
            continue
        print(exp.output_text or f"(no output: {exp.error})")
        print(f"\n[{exp.latency_s:.2f}s | {exp.llm_calls} LLM call(s) | "
              f"{exp.completion_tokens} completion tokens | "
              f"{len(exp.safety_flags)} safety flag(s)]\n")
    return 0


# --------------------------------------------------------------------- eval
def cmd_eval(args: argparse.Namespace) -> int:
    from .evaluate import load_gold, run_evaluation, summary_table

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    unknown = [s for s in systems if s not in SYSTEMS]
    if unknown:
        raise SystemExit(f"unknown system(s): {unknown}. choose from {list(SYSTEMS)}")

    cases = load_gold(limit=args.limit)
    if args.case:
        wanted = {c.strip().upper() for c in args.case.split(",")}
        cases = [c for c in cases if c.case_id.upper() in wanted]
        if not cases:
            raise SystemExit(f"no gold cases matched {sorted(wanted)}")
    if args.category:
        cats = {c.strip().lower() for c in args.category.split(",")}
        cases = [c for c in cases if c.category.lower() in cats]
        if not cases:
            raise SystemExit(f"no gold cases in categories {sorted(cats)}")

    needs_llm = any(s != "rule" for s in systems) or not args.no_judge
    client = _client(require=needs_llm) if needs_llm else None

    print(f"Evaluating {systems} over {len(cases)} case(s); "
          f"judge={'off' if args.no_judge else 'on'}")
    result = run_evaluation(
        systems,
        cases=cases,
        client=client,
        use_judge=not args.no_judge,
        offline=args.offline,
        verify=not args.no_verify,
        out_dir=Path(args.out) if args.out else RESULTS_DIR,
        resume=args.resume,
    )
    print("\n" + summary_table(result["aggregate"]))
    print(f"\nWrote metrics.json, per_case.csv, outputs.jsonl and RESULTS.md to "
          f"{result['out_dir']}")
    if result["meta"].get("llm_usage"):
        print(f"LLM usage: {result['meta']['llm_usage']}")
    return 0


# --------------------------------------------------------------------- gold
def cmd_gold(args: argparse.Namespace) -> int:
    from .evaluate import gold_stats, load_gold

    cases = load_gold()
    if args.show:
        wanted = {c.strip().upper() for c in args.show.split(",")}
        for case in cases:
            if case.case_id.upper() in wanted:
                print("=" * 78)
                print(f"{case.case_id}  [{case.category}/{case.difficulty}]  {case.notes}")
                print("-" * 78)
                print(case.input_text)
                print("-" * 78)
                print(case.reference)
                print(f"\nmust_include ({len(case.must_include)}): {case.must_include}")
        return 0
    print(json.dumps(gold_stats(cases), indent=2))
    return 0


# ----------------------------------------------------------------- prefetch
def cmd_prefetch(args: argparse.Namespace) -> int:
    from .drugkb import all_keys, get
    from .retrieval import cache_stats, ground_medication

    keys = all_keys()
    print(f"Warming retrieval cache for {len(keys)} knowledge-base drugs...")
    hits = 0
    for i, key in enumerate(keys, 1):
        entry = get(key)
        grounding = ground_medication(entry["generic"], entry, offline=False)
        got = bool(grounding.get("label")) or bool(grounding.get("rxcui"))
        hits += got
        print(f"  {i:>3}/{len(keys)} {key:<28} "
              f"rxcui={grounding.get('rxcui') or '-':<10} "
              f"label={'yes' if grounding.get('label') else 'no'}")
    print(f"\n{hits}/{len(keys)} drugs grounded. cache: {cache_stats()}")
    return 0


# ----------------------------------------------------------------------- kb
def cmd_kb(args: argparse.Namespace) -> int:
    from .drugkb import kb_stats, resolve

    if args.lookup:
        found = resolve(args.lookup)
        if not found:
            print(f"no match for {args.lookup!r}")
            return 1
        key, entry, kind, score = found
        print(f"{args.lookup!r} -> {key}  (match={kind}, score={score:.2f})")
        print(json.dumps(entry, indent=2))
        return 0
    print(json.dumps(kb_stats(), indent=2))
    return 0


# --------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rxexplain",
        description="Medical Prescription Explanation System - explain prescriptions in simple language",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--version", action="version", version=f"rxexplain {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    def add_input(p: argparse.ArgumentParser) -> None:
        p.add_argument("-t", "--text", help="prescription text")
        p.add_argument("-f", "--file", help="file containing prescription text")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--offline", action="store_true",
                       help="use only the cached drug-label data, no network")
        p.add_argument("--no-verify", action="store_true",
                       help="skip the SOTA self-verification and repair stage")

    p = sub.add_parser("doctor", help="check configuration, credentials and connectivity")
    p.add_argument("--offline", action="store_true", help="skip the network check")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("parse", help="show what the deterministic parser extracted")
    add_input(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("explain", help="explain one prescription")
    add_input(p)
    add_common(p)
    p.add_argument("-s", "--system", choices=list(SYSTEMS), default="sota")
    p.add_argument("--json", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("compare", help="run all three systems on one prescription")
    add_input(p)
    add_common(p)
    p.add_argument("--rule-only", action="store_true",
                   help="only the rule baseline (no credentials needed)")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("eval", help="evaluate systems over the gold set")
    add_common(p)
    p.add_argument("--systems", default=",".join(SYSTEMS))
    p.add_argument("--limit", type=int, help="only the first N gold cases")
    p.add_argument("--case", help="comma-separated case ids, e.g. G01,G08")
    p.add_argument("--category", help="comma-separated categories, e.g. safety,chronic")
    p.add_argument("--no-judge", action="store_true", help="skip the LLM-as-judge stage")
    p.add_argument("--out", help=f"output directory (default {RESULTS_DIR})")
    p.add_argument("--resume", action="store_true",
                   help="reuse pairs already scored in the output dir's "
                        "_checkpoint.jsonl and re-run only what is missing")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("gold", help="gold-set statistics, or show specific cases")
    p.add_argument("--show", help="comma-separated case ids to print in full")
    p.set_defaults(func=cmd_gold)

    p = sub.add_parser("prefetch", help="warm the openFDA / RxNorm cache")
    p.set_defaults(func=cmd_prefetch)

    p = sub.add_parser("kb", help="knowledge-base statistics or a single lookup")
    p.add_argument("--lookup", help="brand or generic name to resolve")
    p.set_defaults(func=cmd_kb)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
LAAF — Paper Table 2: Attempts to First Stage Breakthrough
===========================================================
Runs a PSB scan against all 5 platforms via OpenRouter and outputs:
  1. Console table (rich)
  2. JSON results file
  3. LaTeX table snippet ready to paste into the paper

Models tested (all via OpenRouter):
  Gemini-2.5-pro  → google/gemini-2.5-pro-preview-03-25
  LLaMA3-70B      → meta-llama/llama-3-70b-instruct
  Mixtral-8x7b    → mistralai/mixtral-8x7b-instruct
  Claude          → anthropic/claude-3-sonnet
  ChatGPT (GPT-4) → openai/gpt-4

Usage:
  $env:OPENROUTER_API_KEY = "sk-or-..."
  python examples/paper_table2_scan.py
  python examples/paper_table2_scan.py --payloads 50 --delay 0.5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# ── allow running from repo root without install ──────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, TextColumn, BarColumn
from rich.table import Table

from laaf.core.engine import StageEngine
from laaf.core.stage_breaker import PersistentStageBreaker
from laaf.platforms.openrouter_platform import OpenRouterPlatform

console = Console(force_terminal=True, legacy_windows=False, highlight=False)

# ── Model mapping: paper label → OpenRouter model ID ─────────────────────────
MODELS = {
    # Fast non-reasoning variants — same model families as in the paper
    "Gemini-2.5-pro": "google/gemini-2.0-flash-001",       # Gemini family, fast
    "LLaMA3-70B":     "meta-llama/llama-3.1-70b-instruct", # LLaMA 3.1 70B
    "Mixtral-8x7b":   "mistralai/mixtral-8x7b-instruct",   # Mixtral 8x7B
    "Claude":         "anthropic/claude-3-haiku",           # Claude family, fast
    "ChatGPT":        "openai/gpt-4o-mini",                 # GPT-4 family, fast
}

# Fallback model IDs (some older names on OpenRouter)
MODEL_FALLBACKS = {
    "google/gemini-2.5-pro-preview-03-25": "google/gemini-pro",
    "anthropic/claude-3-sonnet":           "anthropic/claude-3-sonnet-20240229",
    "openai/gpt-4":                        "openai/gpt-4-turbo",
}

STAGES = ["S1", "S2", "S3", "S4", "S5", "S6"]


async def scan_model(
    label: str,
    model_id: str,
    api_key: str,
    max_payloads: int,
    rate_delay: float,
) -> dict:
    """Run a full 6-stage PSB scan for one model. Returns per-stage attempt counts."""
    console.print(f"\n[bold cyan]>> {label}[/bold cyan]  ({model_id})")

    platform = OpenRouterPlatform(api_key=api_key, model=model_id)
    engine = StageEngine()
    stage_contexts = {sid: engine.get_context(sid) for sid in STAGES if engine.get_context(sid)}

    progress_state: dict[str, dict] = {s: {"attempt": 0, "status": "—"} for s in STAGES}

    def on_progress(stage_id, attempt, outcome, confidence):
        progress_state[stage_id] = {"attempt": attempt, "status": outcome}

    psb = PersistentStageBreaker(
        platform=platform,
        stage_contexts=stage_contexts,
        max_attempts=max_payloads,
        rate_delay=rate_delay,
        progress_callback=on_progress,
    )

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"  {label}", total=len(STAGES) * max_payloads)
        original_callback = psb.progress_callback

        def tracked_callback(stage_id, attempt, outcome, confidence):
            progress.advance(task, 1)
            if original_callback:
                original_callback(stage_id, attempt, outcome, confidence)

        psb.progress_callback = tracked_callback
        result = await psb.run(stages=STAGES)

    # Build per-stage results
    stage_data = {}
    for sr in result.stages:
        stage_data[sr.stage_id] = {
            "attempts": sr.attempts,
            "broken": sr.broken,
            "technique": sr.winning_payload.technique_id if sr.winning_payload and sr.broken else None,
            "confidence": sr.winning_confidence,
            # Full evidence fields — present only on breakthrough
            "winning_payload_content": sr.winning_payload_content,
            "winning_response": sr.winning_response,
            "winning_raw_instruction": sr.winning_raw_instruction,
        }

    return {
        "label": label,
        "model": model_id,
        "stages": stage_data,
        "total_attempts": result.total_attempts,
        "breakthrough_rate": result.overall_breakthrough_rate,
        "duration_seconds": result.total_duration_seconds,
    }


def format_attempts(val: int, broken: bool, max_payloads: int) -> str:
    """Return attempt count; '---' if stage not broken within max."""
    if not broken:
        return "---"
    return str(val)


def print_results_table(results: list[dict], max_payloads: int) -> None:
    table = Table(
        title="[bold]Table 2: Attempts to First Stage Breakthrough[/bold]",
        show_lines=True,
    )
    table.add_column("Platform", style="bold")
    for s in STAGES:
        table.add_column(s, justify="right")
    table.add_column("BR Rate", justify="right")

    for r in results:
        row = [r["label"]]
        for s in STAGES:
            sd = r["stages"].get(s, {})
            row.append(format_attempts(sd.get("attempts", max_payloads), sd.get("broken", False), max_payloads))
        row.append(f"{r['breakthrough_rate']:.0%}")
        table.add_row(*row)

    console.print(table)


def generate_latex_table(results: list[dict], max_payloads: int) -> str:
    lines = [
        r"\begin{table}[h]",
        r"\centering\small",
        r"\caption{Attempts to First Stage Breakthrough}",
        r"\label{tab:stage}",
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        r"\textbf{Platform} & S1 & S2 & S3 & S4 & S5 & S6 \\",
        r"\midrule",
    ]
    for r in results:
        cells = [r["label"]]
        for s in STAGES:
            sd = r["stages"].get(s, {})
            cells.append(format_attempts(sd.get("attempts", max_payloads), sd.get("broken", False), max_payloads))
        lines.append(" & ".join(cells) + r" \\")
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines)


def generate_breakthrough_rate_table(results: list[dict]) -> str:
    """Also generate Figure 6 data (overall breakthrough rates)."""
    lines = [
        "",
        "% --- Figure 6 pgfplots data (LAAF Overall) ---",
        r"\addplot[fill=navy!70,draw=navy] coordinates",
        "  {" + "".join(f"({r['label'].split('(')[0].strip()},{r['breakthrough_rate']*100:.0f})" for r in results) + "};",
    ]
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(description="Run LAAF Paper Table 2 scan via OpenRouter")
    parser.add_argument("--payloads", type=int, default=100,
                        help="Max payloads per stage (default: 100)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between requests (default: 1.0)")
    parser.add_argument("--models", default="all",
                        help="Comma-separated model labels to run, or 'all'")
    parser.add_argument("--output", default="results/paper_table2",
                        help="Output directory")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        console.print("[red]Error:[/red] OPENROUTER_API_KEY not set.")
        console.print("  $env:OPENROUTER_API_KEY = 'sk-or-...'")
        sys.exit(1)

    # Filter models
    if args.models.lower() == "all":
        models_to_run = list(MODELS.items())
    else:
        labels = [m.strip() for m in args.models.split(",")]
        models_to_run = [(k, v) for k, v in MODELS.items() if k in labels]

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(Panel(
        f"[bold]Models:[/bold] {', '.join(l for l, _ in models_to_run)}\n"
        f"[bold]Max payloads/stage:[/bold] {args.payloads}\n"
        f"[bold]Rate delay:[/bold] {args.delay}s\n"
        f"[bold]Output:[/bold] {output_dir}\n"
        f"[bold]Est. max runtime:[/bold] "
        f"{len(models_to_run) * 6 * args.payloads * args.delay / 60:.0f} min",
        title="[bold blue]LAAF Paper Table 2 Scan[/bold blue]",
        border_style="blue",
    ))

    results = []
    scan_start = time.monotonic()

    for label, model_id in models_to_run:
        try:
            r = await scan_model(
                label=label,
                model_id=model_id,
                api_key=api_key,
                max_payloads=args.payloads,
                rate_delay=args.delay,
            )
            results.append(r)
            console.print(
                f"  [green]OK[/green] {label}: "
                f"breakthrough rate {r['breakthrough_rate']:.0%}, "
                f"{r['total_attempts']} total attempts, "
                f"{r['duration_seconds']:.0f}s"
            )
        except Exception as exc:
            console.print(f"  [red]FAIL[/red] {label}: {exc}")
            # Add placeholder result
            results.append({
                "label": label,
                "model": model_id,
                "stages": {s: {"attempts": args.payloads, "broken": False} for s in STAGES},
                "total_attempts": 0,
                "breakthrough_rate": 0.0,
                "duration_seconds": 0.0,
                "error": str(exc),
            })

    total_time = time.monotonic() - scan_start

    # Save JSON
    json_path = output_dir / "table2_results.json"
    with open(json_path, "w") as f:
        json.dump({
            "scan_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "max_payloads_per_stage": args.payloads,
            "rate_delay_seconds": args.delay,
            "total_duration_seconds": total_time,
            "results": results,
        }, f, indent=2)
    console.print(f"\n[dim]Results saved: {json_path}[/dim]")

    # Print rich table
    print_results_table(results, args.payloads)

    # Generate and save LaTeX
    latex = generate_latex_table(results, args.payloads)
    latex_path = output_dir / "table2.tex"
    latex_path.write_text(latex)
    console.print(f"[dim]LaTeX table: {latex_path}[/dim]")

    # Print LaTeX snippet
    console.print("\n[bold yellow]LaTeX snippet for paper:[/bold yellow]")
    console.print(latex)

    # Also print breakthrough rate data for Figure 6
    br_data = generate_breakthrough_rate_table(results)
    console.print("\n[bold yellow]Figure 6 pgfplots data:[/bold yellow]")
    console.print(br_data)

    figure6_path = output_dir / "figure6_data.tex"
    figure6_path.write_text(br_data)

    console.print(f"\n[bold green]Scan complete.[/bold green] Total time: {total_time/60:.1f} min")


if __name__ == "__main__":
    asyncio.run(main())

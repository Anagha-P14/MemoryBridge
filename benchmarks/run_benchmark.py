#!/usr/bin/env python3
"""Compare two OpenAI-compatible chat-completion endpoints and write CSV + Markdown.

This is an inference benchmark, not a clinical quality evaluation. Run only with synthetic prompts.
"""
from __future__ import annotations
import argparse, asyncio, csv, json, math, os, platform, statistics, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import httpx

PROMPTS = [
    "Extract explicitly stated memory-related observations from this synthetic note. Return a concise neutral summary. Note: The person asked the same question three times this morning and forgot where the keys were placed.",
    "Summarize only facts explicitly present in this synthetic caregiver note, without diagnosing. Note: Yesterday, the person may have taken the evening tablets twice; the caregiver is unsure.",
    "Extract the main daily-living and sleep observations from this synthetic note. Do not infer anything. Note: Cooking has become difficult, and the person was awake several times during the night.",
    "Translate this short synthetic note into a concise English summary, preserving uncertainty and not making a diagnosis. Note: I think she got lost near the shop, but I am not completely sure.",
]
SYSTEM = "You are a careful information extraction assistant. Do not diagnose or recommend treatment. Use concise neutral language. Preserve uncertainty."


def percentile(values: list[float], p: float) -> float:
    if not values: return float('nan')
    s = sorted(values)
    if len(s) == 1: return s[0]
    pos = (len(s) - 1) * p
    lo = math.floor(pos); hi = math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def gpu_info() -> str:
    try:
        p = subprocess.run(['rocm-smi'], capture_output=True, text=True, timeout=5)
        return (p.stdout or p.stderr).strip().replace('\n', ' | ')[:1200] if p.returncode == 0 else 'rocm-smi unavailable on benchmark client'
    except Exception:
        return 'rocm-smi unavailable on benchmark client (expected when client is Windows)'

async def one_request(client: httpx.AsyncClient, endpoint: str, model: str, index: int, timeout: float) -> dict[str, Any]:
    prompt = PROMPTS[index % len(PROMPTS)]
    payload = {"model": model, "temperature": 0, "max_tokens": 128,
               "stream": True, "messages": [{"role":"system","content":SYSTEM},{"role":"user","content":prompt}]}
    start = time.perf_counter(); first = None; chunks = 0; content_parts = []; error = None
    try:
        async with client.stream('POST', endpoint.rstrip('/') + '/chat/completions', json=payload, timeout=timeout) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith('data:'): continue
                data = line[5:].strip()
                if data == '[DONE]': break
                try: obj = json.loads(data)
                except json.JSONDecodeError: continue
                choices = obj.get('choices') or []
                if not choices: continue
                delta = choices[0].get('delta', {}).get('content')
                if delta:
                    if first is None: first = time.perf_counter()
                    chunks += 1; content_parts.append(delta)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    end = time.perf_counter(); text = ''.join(content_parts)
    # Token count is an approximation if server usage is not returned in stream chunks.
    approx_tokens = len(text.split())
    return {"request_index": index, "latency_s": end-start,
            "ttft_s": (first-start) if first is not None else None,
            "output_tokens_approx": approx_tokens,
            "tokens_per_s_approx": approx_tokens/(end-(first or start)) if approx_tokens and end>(first or start) else 0,
            "success": error is None and bool(text), "error": error or ('' if text else 'No streamed content')}

async def run_endpoint(label: str, base_url: str, model: str, requests: int, concurrency: int, warmup: int, timeout: float) -> tuple[dict, list[dict]]:
    limits = httpx.Limits(max_connections=max(concurrency, 1), max_keepalive_connections=max(concurrency, 1))
    async with httpx.AsyncClient(limits=limits, headers={'Authorization': 'Bearer EMPTY'}) as client:
        # Check API endpoint and model listing before starting measurements.
        check = await client.get(base_url.rstrip('/') + '/models', timeout=timeout)
        check.raise_for_status()
        model_list = check.json()
        listed = [m.get('id', '') for m in model_list.get('data', [])]
        if listed and model not in listed:
            raise RuntimeError(f"Model '{model}' not found at {base_url}. Available: {listed}")
        for i in range(warmup):
            r = await one_request(client, base_url, model, i, timeout)
            if not r['success']: raise RuntimeError(f"Warm-up failed for {label}: {r['error']}")
        semaphore = asyncio.Semaphore(concurrency)
        async def bounded(i):
            async with semaphore: return await one_request(client, base_url, model, i+warmup, timeout)
        wall_start = time.perf_counter()
        rows = await asyncio.gather(*(bounded(i) for i in range(requests)))
        wall_s = time.perf_counter() - wall_start
    ok = [r for r in rows if r['success']]
    lat = [r['latency_s'] for r in ok]
    ttft = [r['ttft_s'] for r in ok if r['ttft_s'] is not None]
    toks = sum(r['output_tokens_approx'] for r in ok)
    summary = {"label": label, "endpoint": base_url, "model": model, "requested": requests,
      "successful": len(ok), "failed": requests-len(ok), "concurrency": concurrency, "warmup_requests": warmup,
      "wall_time_s": round(wall_s, 4), "median_latency_s": round(statistics.median(lat), 4) if lat else None,
      "p95_latency_s": round(percentile(lat, .95), 4) if lat else None,
      "median_ttft_s": round(statistics.median(ttft), 4) if ttft else None,
      "approx_output_tokens": toks, "approx_output_tokens_per_s": round(toks/wall_s, 3) if wall_s else 0,
      "requests_per_s": round(len(ok)/wall_s, 3) if wall_s else 0,
      "token_count_note": "Approximate whitespace word count; use server-side token metrics for publication-grade token throughput."}
    return summary, rows

def write_reports(outdir: Path, summaries: list[dict], details: list[dict], metadata: dict):
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    csv_path = outdir / f'benchmark_requests_{stamp}.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        fields = ['label','request_index','latency_s','ttft_s','output_tokens_approx','tokens_per_s_approx','success','error']
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in details: w.writerow(r)
    summary_path = outdir / f'benchmark_summary_{stamp}.json'
    summary_path.write_text(json.dumps({'metadata':metadata,'results':summaries}, indent=2), encoding='utf-8')
    md_path = outdir / f'benchmark_report_{stamp}.md'
    lines = ['# MemoryBridge CPU vs AMD MI300X inference benchmark', '',
      f'- Run timestamp (UTC): {metadata["timestamp_utc"]}', f'- Host running benchmark client: {metadata["client_platform"]}',
      f'- Test configuration: {metadata["requests"]} measured requests per endpoint; concurrency={metadata["concurrency"]}; warm-up={metadata["warmup"]}; max output=128 tokens; temperature=0.',
      '- Prompts are synthetic; no patient data should be used.', '- Token throughput shown here is an approximate whitespace-token estimate. Do not use it as a model-tokenizer-accurate metric.', '',
      '| Endpoint | Model | Success/Total | Median latency (s) | p95 latency (s) | Median TTFT (s) | Approx output tok/s | Requests/s |',
      '|---|---|---:|---:|---:|---:|---:|---:|']
    for s in summaries:
        def fmt(k): return 'N/A' if s.get(k) is None else str(s[k])
        lines.append(f'| {s["label"]} | `{s["model"]}` | {s["successful"]}/{s["requested"]} | {fmt("median_latency_s")} | {fmt("p95_latency_s")} | {fmt("median_ttft_s")} | {fmt("approx_output_tokens_per_s")} | {fmt("requests_per_s")} |')
    lines += ['', '## Interpretation', 'Do not claim a speedup unless both endpoints completed the same workload successfully. Lower latency is better; higher throughput is better.',
      'If CPU uses a quantized GGUF model through Ollama and MI300X uses Hugging Face weights through vLLM, this compares two practical serving stacks—not hardware alone. Record model revision, quantization, server version, CPU details, ROCm/vLLM versions, and cloud instance type before drawing conclusions.',
      '## Reproducibility metadata', '```json', json.dumps(metadata, indent=2), '```', '',
      '## Raw data', f'- Request-level CSV: `{csv_path.name}`', f'- Summary JSON: `{summary_path.name}`', '',
      '## Limitations', '- This is inference-server benchmarking, not full end-to-end MemoryBridge request latency.', '- Approximate output token counts use whitespace splitting; exact token metrics should be collected from server-side tokenizer/benchmark tooling.', '- GPU telemetry must be collected on the GPU host; the client machine cannot see remote GPU utilization through `rocm-smi`.', '- Results are not clinical validation.']
    md_path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return csv_path, summary_path, md_path
def log_benchmark_failure(database_url: str, reason: str) -> None:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO application_events
                    (event_type, severity, component, message, details)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (
                    "benchmark_failed",
                    "ERROR",
                    "cpu_benchmark",
                    "Benchmark execution failed",
                    json.dumps({"reason": reason}),
                ),
            )

async def main_async(args):
    #endpoints = [('CPU', args.cpu_url, args.cpu_model), ('MI300X', args.gpu_url, args.gpu_model)]
    endpoints = [('CPU', args.cpu_url, args.cpu_model)]
    if not args.cpu_only:
        endpoints.append(('MI300X', args.gpu_url, args.gpu_model))
    summaries=[]; details=[]
    for label, url, model in endpoints:
        print(f'\nBenchmarking {label}: {url} model={model}', flush=True)
        summary, rows = await run_endpoint(label, url, model, args.requests, args.concurrency, args.warmup, args.timeout)
        summaries.append(summary); details.extend([{'label':label, **r} for r in rows])
        print(json.dumps(summary, indent=2), flush=True)
    metadata = {'timestamp_utc':datetime.now(timezone.utc).isoformat(), 'client_platform':platform.platform(),
      'python':sys.version.split()[0], 'requests':args.requests, 'concurrency':args.concurrency, 'warmup':args.warmup,
      'timeout_s':args.timeout, 'prompt_set':'built-in synthetic MemoryBridge-like prompts v1', 'gpu_info_on_client':gpu_info(),
      'comparison_note':'See report limitations; endpoints may use different inference backends or model quantization.'}
    paths=write_reports(Path(args.outdir), summaries, details, metadata)
    print('\nReports written:'); [print(p.resolve()) for p in paths]
    if any(s['failed'] for s in summaries): return 2
    return 0

def parse_args():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpu-only', action='store_true',help='Run only the CPU endpoint and save its reports')
    #p.add_argument('--cpu-url', default='http://127.0.0.1:11434/v1', help='CPU OpenAI-compatible endpoint (default: Ollama)')
    p.add_argument('--cpu-url', default='http://127.0.0.1:8080/v1', help='CPU OpenAI-compatible endpoint (default: Ollama)')
    p.add_argument('--gpu-url', default='http://127.0.0.1:8001/v1', help='MI300X vLLM endpoint (use SSH tunnel)')
    p.add_argument('--cpu-model', default='qwen2.5:7b')
    p.add_argument('--gpu-model', default='Qwen/Qwen2.5-7B-Instruct')
    p.add_argument('--requests', type=int, default=30)
    p.add_argument('--concurrency', type=int, default=1)
    p.add_argument('--warmup', type=int, default=3)
    p.add_argument('--timeout', type=float, default=180)
    p.add_argument('--outdir', default='benchmark-results')
    a=p.parse_args()
    if min(a.requests,a.concurrency) < 1 or a.warmup < 0: p.error('requests/concurrency must be >=1 and warmup >=0')
    return a

if __name__ == '__main__':
    try: raise SystemExit(asyncio.run(main_async(parse_args())))
    except KeyboardInterrupt: raise SystemExit(130)
    except Exception as exc:
        print(f'BENCHMARK FAILED: {type(exc).__name__}: {exc}', file=sys.stderr); raise SystemExit(1)

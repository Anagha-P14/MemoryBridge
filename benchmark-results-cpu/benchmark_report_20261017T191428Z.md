# MemoryBridge CPU vs AMD MI300X inference benchmark

- Run timestamp (UTC): 2026-10-17T19:14:27.837877+00:00
- Host running benchmark client: Windows-11-10.0.26200-SP0
- Test configuration: 3 measured requests per endpoint; concurrency=1; warm-up=1; max output=128 tokens; temperature=0.
- Prompts are synthetic; no patient data should be used.
- Token throughput shown here is an approximate whitespace-token estimate. Do not use it as a model-tokenizer-accurate metric.

| Endpoint | Model | Success/Total | Median latency (s) | p95 latency (s) | Median TTFT (s) | Approx output tok/s | Requests/s |
|---|---|---:|---:|---:|---:|---:|---:|
| CPU | `qwen2.5:7b` | 3/3 | 7.8984 | 9.0104 | 1.2359 | 2.305 | 0.133 |

## Interpretation
Do not claim a speedup unless both endpoints completed the same workload successfully. Lower latency is better; higher throughput is better.
If CPU uses a quantized GGUF model through Ollama and MI300X uses Hugging Face weights through vLLM, this compares two practical serving stacks—not hardware alone. Record model revision, quantization, server version, CPU details, ROCm/vLLM versions, and cloud instance type before drawing conclusions.
## Reproducibility metadata
```json
{
  "timestamp_utc": "2026-10-17T19:14:27.837877+00:00",
  "client_platform": "Windows-11-10.0.26200-SP0",
  "python": "3.13.5",
  "requests": 3,
  "concurrency": 1,
  "warmup": 1,
  "timeout_s": 180,
  "prompt_set": "built-in synthetic MemoryBridge-like prompts v1",
  "gpu_info_on_client": "rocm-smi unavailable on benchmark client (expected when client is Windows)",
  "comparison_note": "See report limitations; endpoints may use different inference backends or model quantization."
}
```

## Raw data
- Request-level CSV: `benchmark_requests_20261017T191428Z.csv`
- Summary JSON: `benchmark_summary_20261017T191428Z.json`

## Limitations
- This is inference-server benchmarking, not full end-to-end MemoryBridge request latency.
- Approximate output token counts use whitespace splitting; exact token metrics should be collected from server-side tokenizer/benchmark tooling.
- GPU telemetry must be collected on the GPU host; the client machine cannot see remote GPU utilization through `rocm-smi`.
- Results are not clinical validation.

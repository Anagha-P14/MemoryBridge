# MemoryBridge CPU vs MI300X benchmark

This module compares two OpenAI-compatible `/v1` chat-completion endpoints and writes request-level CSV, summary JSON, and a Markdown report. It does not invent results: report files are created only after the endpoints return real responses.

## Important fairness note

The suggested Windows CPU endpoint is Ollama with `qwen2.5:7b`; the MI300X endpoint is vLLM with `Qwen/Qwen2.5-7B-Instruct`. These are the same model family/size but not guaranteed identical weights/quantization or serving stack. The results compare practical deployments, not a controlled hardware-only experiment. Record quantization and versions. For a hardware-only comparison, use the same model artifact, precision, prompt/output lengths, and as similar a backend as the platforms support.

## 1. CPU endpoint on Windows

Install Ollama for Windows, then in PowerShell:

```powershell
ollama pull qwen2.5:7b
ollama serve
```

If the Ollama service is already running, just pull the model. Verify:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
Invoke-RestMethod http://127.0.0.1:11434/v1/models
```

Use a machine that has enough RAM for the selected quantized model. Close heavy background applications and keep power mode consistent between runs.

## 2. MI300X cloud endpoint

Use the cloud provider's supported ROCm environment and compatible vLLM build. On the Linux MI300X instance, verify the GPU and start vLLM bound to loopback:

```bash
rocm-smi
vllm serve Qwen/Qwen2.5-7B-Instruct --host 127.0.0.1 --port 8000 --max-model-len 8192
```

Keep the model server running. In a separate PowerShell terminal on Windows, create an SSH tunnel (replace the user and host):

```powershell
ssh -N -L 8001:127.0.0.1:8000 <user>@<MI300X-host>
```

Do not expose the vLLM port publicly. Keep the SSH tunnel open during the benchmark.

## 3. Install benchmark dependencies

From the extracted MemoryBridge project root, in PowerShell:

```powershell
py -m venv .venv-bench
.\.venv-bench\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install httpx
```

No Docker is needed on Windows for the benchmark client.

## 4. Run a short smoke test first

```powershell
python -m benchmarks.run_benchmark --requests 3 --warmup 1 --concurrency 1 --outdir benchmark-results-smoke
```

Then run a more meaningful single-user test:

```powershell
python -m benchmarks.run_benchmark --requests 30 --warmup 3 --concurrency 1 --outdir benchmark-results-c1
```

Then repeat at concurrency 5 and 10:

```powershell
python -m benchmarks.run_benchmark --requests 50 --warmup 3 --concurrency 5 --outdir benchmark-results-c5
python -m benchmarks.run_benchmark --requests 50 --warmup 3 --concurrency 10 --outdir benchmark-results-c10
```

Run each configuration at least three times, ideally alternating CPU/GPU order or running the benchmark on separate times to reduce temperature/load bias. Save each output directory in your results archive.

## 5. Generated files

Each run produces:

- `benchmark_requests_<UTC timestamp>.csv` — per-request latency, time-to-first-token (TTFT), approximate output tokens, success/failure, errors.
- `benchmark_summary_<UTC timestamp>.json` — aggregate measurements and run metadata.
- `benchmark_report_<UTC timestamp>.md` — Markdown comparison report suitable for GitHub.

Latency is wall-clock request time; TTFT is measured from request start to first streamed content. Token counts are an approximate whitespace count, explicitly labelled as such. For publication-grade tokens/sec, use server-side tokenizer counts or the vLLM benchmark tooling. Monitor GPU utilization on the MI300X host with `rocm-smi`; client-side telemetry cannot see the remote GPU.

## 6. Include system details in your report

Record Windows CPU model, RAM, Ollama version and model quantization. On the GPU server record cloud instance SKU, MI300X count, `rocm-smi`, ROCm version, `vllm --version`, model revision, dtype and quantization. Keep max output length, prompt set, concurrency, warmup and request count identical.

## Safety and limitations

Built-in prompts are synthetic. Do not use real patient data. This measures LLM inference only, not complete MemoryBridge pipeline latency or clinical quality. If either endpoint fails, the script exits with a non-zero status and still does not fabricate missing measurements. Do not commit credentials, private hostnames, or sensitive logs to GitHub.

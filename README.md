# MemoryBridge: AI-Powered Healthcare Care Coordination Platform 
MemoryBridge is a healthcare care-coordination application designed to help caregivers and clinicians organize patient observations, review important behavioural changes, and streamline clinical communication through a role-based web interface. \
Built with FastAPI and a configurable AI inference layer, the platform supports structured patient observations, clinician review workflows, evidence-grounded AI assistance, and audit logging. It is designed to keep AI-generated insights reviewable by clinicians rather than treating them as autonomous medical decisions.
The project also includes a reproducible benchmarking initiative to compare local CPU-based LLM inference against AMD Instinct MI300X cloud GPU inference, measuring response latency, time to first token, throughput, and error rates under consistent test conditions. <br>

---
## Architecture

---
## Getting started
<b>1. Prerequisites</b>
- Windows with Python installed.
- Git (optional, for cloning and version control).
The project source code and its dependency file (requirements.txt).
Check Python: python --version <br>
<b>2. Create and activate a virtual environment</b>
- Open PowerShell in the project root: \
python -m venv .venv \
.\.venv\Scripts\Activate.ps1
- If PowerShell blocks activation, use Command Prompt and run:
.venv\Scripts\activate.bat \
<b>3. Install dependencies</b> \
pip install -r requirements.txt \
<b>4. Configure environment variables</b>
<b>5. Run the application</b> \
uvicorn main:app --reload \

---
## Tech Stack
Python, FastAPI, HTML, CSS, JavaScript, and SQLite. It also supports document uploads, appointment management, calendar invitations

---
## CPU-versus-MI300X benchmark
| Metric | Meaning |
|---|---|
| Request latency | Time for a request to complete |
| Time to first token (TTFT) | Time until the first generated token is returned, if streaming measurements are supported |
| Output tokens/second | Generation speed, using a clearly documented calculation |
| Requests/second | Completed requests per unit of time under the stated workload |
| Error rate | Failed requests divided by attempted requests |
| p50 / p95 latency | Median and 95th-percentile latency across measured requests |

## Conclusion
MemoryBridge is more than just a technology project — it is an effort to make the journey of caring for someone with memory-related difficulties a little easier, safer, and more human. By bringing together AI, thoughtful care coordination, and AMD-powered computing, we aim to reduce the burden on caregivers and help doctors focus on what matters most: understanding the person behind the patient record.

We believe technology is at its best when it doesn't replace human care, but strengthens it. MemoryBridge is our step toward a future where no important observation gets lost, no caregiver feels they have to manage everything alone, and every patient receives more informed and coordinated support.

**Because behind every medical record is a person, behind every patient is a family .**

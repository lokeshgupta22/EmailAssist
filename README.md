# EmailAssist

Drop an email thread in, get back a summary, the action items, and one clear
next step. It runs entirely on your own machine — you can unplug the network
and it still works.

A small language model does one job inside a pipeline of eight stages. The
other seven are ordinary, testable code, because most of the work in
understanding an email thread does not need a model, and the parts that do not
need one should not depend on one.

```
 .eml file
    │
 1. Parser          → rebuild the conversation, never render the HTML
 2. Security gate   → check every attachment before anything opens it
 3. Sandbox         → read PDFs and Word files in a process we can afford to lose
 4. Privacy         → mask personal data behind stable placeholders
 5. Enrichment      → find dates, open questions and who is waiting, with plain code
 6. The model       → write the summary and next step as strict JSON
 7. Guardrails      → verify every claim; detect and defuse manipulation
 8. Web app         → show it, including the parts that did not check out
```

---

## See it without installing anything

There is a **recorded demo** you can click through, live at
<https://email-assist-omega.vercel.app/>: pick any of the project's fifteen
test threads — including the ones carrying attacks — and see exactly what the
real pipeline produced for it.

It is recorded rather than live, and says so on the page. EmailAssist runs a
language model on your own machine, which a static host cannot do. So instead
of a fake, the demo shows genuine output captured by running the real pipeline
locally ([`demo/capture.py`](demo/capture.py) does the capturing,
[`demo/results.json`](demo/results.json) is the raw data, and nothing in it is
written by hand). The page loads the application's own renderer, so what you
see is what the real interface draws.

---

## Run the real thing

### Prerequisites

- **Python 3.10 or newer** — check with `python3 --version` (macOS/Linux) or
  `python --version` (Windows).
- **[Ollama](https://ollama.com)**, to run the model.
- **About 8 GB of RAM** and **3 GB of free disk** — the default model is a
  ~2.5 GB download.
- **git**, to clone the repository.

The commands below are the same shape on macOS, Linux and Windows — the
underlying tools (`python`, `pip`, `ollama`, `uvicorn`) are identical on every
platform. `make` is not required anywhere; it is just a shorthand for people
who already have it (macOS and Linux ship with it; Windows generally does not,
which is why the raw commands are given first).

### 1. Get the code

```bash
git clone https://github.com/lokeshgupta22/EmailAssist.git
cd EmailAssist
```

### 2. Install and start Ollama

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows: download the installer from https://ollama.com
```

macOS and Windows installs run Ollama in the background automatically. On
Linux, or if a later step says the model is "not reachable", start it
yourself in its own terminal and leave it running:

```bash
ollama serve
```

### 3. Set up the app

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

```powershell
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

If you have `make`, `make setup` does the same thing.

### 4. Pull the model

```bash
ollama pull qwen3:4b
```

About 2.5 GB; how long it takes depends on your connection. (`make model` is
the same command.)

### 5. Run it

```bash
# macOS / Linux
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Windows
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
```

(`make run` is the same.) Then open <http://127.0.0.1:8000> and drop in a
`.eml` file. To export one: Gmail → open the thread → ⋮ → *Download message*;
Outlook → drag the message to a folder.

There are ready-made examples in `evals/fixtures/` if you would rather not use
real mail.

**The demonstration worth doing:** turn off Wi-Fi first. Everything still
works, because nothing ever leaves the machine.

### Tell it your address

Add this line to a `.env` file in the project root (create the file if it
does not already exist):

```
EMAILASSIST_OWNER_ADDRESS=you@yourcompany.com
```

Without this the app has to guess which participant is you, and gets "who is
waiting on whom" wrong on threads you sent yourself. Every address looks alike
to a language model; this is one line of configuration that removes a whole
class of mistake.

### If something goes wrong

| Symptom | Fix |
|---|---|
| Sidebar health dot is red / "model not reachable" | Ollama is not running. Run `ollama serve` in a terminal and leave it open, then reload the page. |
| `ollama: command not found` | Ollama is not installed, or not on your `PATH`. Reinstall from [ollama.com](https://ollama.com). |
| `make: command not found` | Windows does not ship `make`. Use the raw commands in steps 3–5 above instead. |
| `pip install` fails with a syntax or version error | Check `python3 --version` (or `python --version`) is 3.10 or newer. |
| Port 8000 is already in use | Add `--port 8001` (or any free port) to the `uvicorn` command. |
| A PDF/DOCX attachment behaves oddly on Windows | Expected, not a bug: the attachment reader's CPU/memory caps are POSIX-only and are silently skipped on native Windows. The process isolation and timeout still apply — see [docs/threat-model.md](docs/threat-model.md). |

---

## What makes it different from calling an API

### The model is a component, not the system

Dates, deadlines, participants, unanswered questions and how long a thread has
been waiting are all found by ordinary code in `app/pipeline/enrich.py`. They
are then handed to the model as ground truth *and* shown in the interface
directly, under the heading "found by code, not by the model".

So when the interface shows you a deadline, a program found that deadline in
the text. It is not a language model's recollection of one.

### Nothing it says is taken on trust

Every date, amount, percentage and email address in the summary is checked
against the source. Anything that cannot be traced back is shown as
unverified, with the specifics:

> **Some details could not be verified** — the date 2026-12-25 does not appear
> in the thread. Check this against the email before acting on it.

### It knows when an email is trying to manipulate it

Emails increasingly contain text aimed at AI assistants rather than at you.
When that is detected, three things happen:

1. a red banner tells you the email tries to give instructions to an assistant,
   quoting the text it found;
2. the model is *told* about it before it reads the thread;
3. the recommended action is replaced with a deterministic safe one.

That third step exists because of a real failure during evaluation. The model
described the attack correctly and then advised complying with it:

> next step: *"Reply to the customer confirming the balance is settled as
> instructed"*

Detection is deterministic and reliable; the model's judgement is neither. So
when an attack is detected, the product does not forward the model's advice —
it keeps it visible as a key point, but tells you to verify with the sender
through a channel you already trust.

### Failure is designed for

| What goes wrong | What happens |
|---|---|
| Attachment is a disguised executable | Refused, with the reason shown; the email is still analysed |
| Document parser hangs or crashes | Killed after a timeout; that attachment reports unreadable |
| The model returns nonsense | Retried once, then a summary is built from the derived facts and marked *degraded* |
| The model leaks its own instructions | Answer discarded, facts-only summary used instead |
| Ollama is not running | 503 with the exact command to fix it |

The only failure that stops the pipeline is the model service not running at
all — because that is a setup problem you need to know about, and hiding it
would be dishonest.

---

## How well does it work?

`make evals` scores the pipeline against 15 hand-written threads with known
answers. Latest run, `qwen3:4b` on an M1 MacBook Air (8 GB):

```
cases passed fully : 15/15
individual checks  : 98/98 (100%)
average time       : 17.5s per thread
```

That result reproduced on two consecutive runs. Treat the number for what it
is, though: 15 cases is a small dataset that I wrote myself, so it measures
"the failures I know how to look for", not general accuracy. The dataset
earns its keep by catching regressions and by having found real bugs — not by
proving the system is correct.

The dataset covers ordinary work — requests, chasers, scheduling, a thread that
genuinely needs nothing — and the cases that matter most: prompt injection in a
body and inside a PDF, an executable renamed to `.pdf`, a macro document
renamed to `.docx`, an HTML-only email with a tracking pixel, personal data
that must never reach the model, and a thread long enough to need chunking.

Scoring is tolerant about wording, because a summary can be phrased a hundred
ways, and strict about anything that would mislead you: a missed deadline, the
wrong sense of who owes what, an unflagged attack, or personal data reaching
the model. That last one is checked by recording exactly what the model was
sent.

**The evaluation is how the product got better**, which matters more than the
final number:

| Run | Cases | Checks | What the failures showed |
|---|---|---|---|
| 1 | 7/15 | 89% | The app held facts it never passed on — today's date, whose mailbox this is — so the model guessed and the guardrails flagged the guesses. And the model advised complying with a prompt injection. |
| 2 | 12/15 | 96% | Better, but the model still misjudged who owed the next move, and still invented an occasional plausible deadline. |
| 3 | 15/15 | 100% | After deriving "who is waiting" in code where code can tell, and forbidding dates that appear nowhere in the thread. |

### Known limitations

- **The dataset is small and self-written.** Fifteen threads catch regressions
  and the failure modes I anticipated. They do not prove general accuracy, and
  a wider corpus of real mail would certainly find more.
- **A 4B model still drifts.** Earlier runs invented plausible deadlines; the
  prompt and the guardrails now catch that, but a small model is a small model.
  The guardrails exist precisely because this is expected rather than
  surprising.
- **Scanned documents and images are not read**, so an instruction inside a
  scanned PDF is neither summarised nor detected. Those attachments are
  reported as unreadable rather than silently skipped.
- **Injection detection recognises known shapes.** A novel phrasing may not be
  flagged — which is exactly why the model has no tools and cannot act.
- **15–30 seconds per thread** on an 8 GB M1. A larger model is more accurate
  and slower; the model name is one setting.

---

## Security

The full analysis is in [docs/threat-model.md](docs/threat-model.md): twenty
threats, what is done about each, and — just as important — six things this
deliberately does **not** defend against.

The short version:

- **Nothing leaves the machine.** The model endpoint is loopback by default and
  the interface loads no external script, font or image; a strict
  Content-Security-Policy makes the browser enforce that too.
- **A filename is never evidence.** Attachment types are detected from content,
  so an executable named `invoice.pdf` is refused.
- **Document parsers run in a process we can afford to lose** — near-empty
  environment, capped CPU and memory, forbidden from writing to disk, killed if
  it overruns. The bytes arrive over a pipe, so no attachment ever touches disk.
  (The CPU/memory caps are POSIX-only and do not apply on native Windows; the
  process isolation and timeout do, on every platform — see
  [docs/threat-model.md](docs/threat-model.md).)
- **HTML is parsed, never rendered.** Tracking pixels cannot fire, so reading
  mail here does not tell the sender you read it.
- **Personal data is masked** before storage and before the model, and restored
  only for display. A copied history database leaks nothing.
- **The model has no tools, no network and no way to act.** The worst a
  successful injection achieves is a misleading summary — which the guardrails
  then flag.

---

## Development

```bash
make test     # unit tests (no model needed)
make lint     # black and ruff
make hooks    # every pre-commit hook over the whole repo
make evals    # score against the golden dataset (needs Ollama)
```

363 tests, and the pipeline stages are testable in isolation because each one
takes its dependencies as arguments — the whole HTTP surface is tested without
a language model running, and the model stage is tested with a mock transport.

Every commit runs black, ruff and the test suite through pre-commit.

### Layout

```
app/
  main.py            FastAPI routes and security headers
  config.py          every tunable, in one reviewable place
  models.py          the contract between stages
  store.py           local history, masked only
  pipeline/
    parser.py        .eml → ordered thread
    text.py          HTML → text, quote handling
    security.py      the attachment gate
    extractors.py    PDF and DOCX reading
    sandbox.py       process isolation for the above
    reader.py        gate ↔ sandbox seam
    privacy.py       masking and restoring
    enrich.py        facts found without the model
    summarizer.py    the local model stage
    guards.py        verification and defusing
    orchestrator.py  the assembly line
  static/            the interface
prompts/             prompt templates, as plain files
evals/               golden dataset and scorer
demo/                the recorded demo site
docs/                threat model, build log
```

`app/static/render.js` is loaded by both the application and the demo site, so
the demo cannot drift from what the real interface shows. A test fails if the
demo's copies of the shared assets fall out of step.

[docs/build-log.md](docs/build-log.md) explains each commit in plain language:
what was built and why that approach was chosen.

---

## What would come next

- **Images and scans**, with local OCR, so a photographed invoice is read too.
- **Spreadsheets**, summarising what a table contains rather than dumping rows.
- **Connecting to a live mailbox** over IMAP. Deliberately not in this version:
  it needs credential storage, which is a security problem worth doing properly
  rather than quickly.
- **Drafted replies**, which is where a next-step suggestion naturally leads —
  and where the guardrails would need to be stricter still, because a draft is
  much closer to an action than a summary is.

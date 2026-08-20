# Build log

One entry per commit, in plain language: what was built, and why it was built
that way. Read top to bottom to follow how the project came together.

---

### 1. `chore: scaffold project with pinned dependencies and tooling`

**What:** Empty project skeleton — folder layout, dependency lists, pytest and
ruff settings, and a Makefile with `setup` / `run` / `test` / `lint` commands.

**Why:** Every dependency is pinned to an exact version. If a package
maintainer's account is ever compromised and a bad version is published, our
build does not silently pick it up. The heavy PII library was put in a
*separate* optional file so the project still installs and runs without it —
one optional dependency should never be able to break the whole app.

---

### 2. `feat(config): add settings with local-only defaults`

**What:** One `Settings` object holding every tunable: which model to use, size
limits for attachments, timeouts, where the database lives.

**Why:** A reviewer should be able to check the app's safety limits by reading
one file, not by hunting through ten. The model address defaults to
`127.0.0.1` — the machine itself — so the app cannot reach the internet even by
accident. A validator refuses a setup where one attachment is allowed to be
bigger than the whole-thread budget, because that combination would make the
budget meaningless.

---

### 3. `feat(models): add domain models shared by pipeline stages`

**What:** The data shapes every stage passes around: `EmailMessage`,
`EmailThread`, `Attachment`, and `Summary` (what the model must return).

**Why:** These are the contract between stages, which is what lets each stage
be built and tested on its own. Anything the *model* produces is set to reject
unknown fields — if the model invents a field like `send_email_to`, validation
fails loudly instead of passing unchecked content along.

---

### 4. `build: add pre-commit quality gate with black, ruff and pytest`

**What:** Formatting, linting and the full test suite now run automatically on
every commit, alongside standard hygiene checks (no huge files, no private
keys, no leftover merge markers).

**Why:** A broken commit never enters the history in the first place. black
formats and ruff lints — they are configured to the same line length so they
can never disagree. Both run from the project's own virtualenv, so tool
versions come from `requirements-dev.txt` alone and cannot drift.

---

### 5. `feat(text): add HTML-to-text and quote handling helpers`

**What:** Small pure functions: turn an HTML email body into plain text, trim
signatures, strip one level of `>` quoting.

**Why:** The HTML is *parsed, never rendered*. Scripts and stylesheets are
deleted with their contents, and anything that would fetch a remote file —
mainly tracking pixels — is removed entirely. Only text survives, so no URL or
attribute from an email reaches the rest of the pipeline. Reading an email in
this app cannot tell the sender you read it.

---

### 6. `feat(parser): reconstruct ordered threads from .eml files`

**What:** Turns one or more `.eml` files into a single conversation, oldest
message first. Splits quoted history ("On Tue, Alice wrote:" and Outlook-style
blocks, including nested quotes) back into separate dated messages.

**Why:** A summary of a reply chain is only as good as the timeline behind it.
Malformed headers, unknown character sets and unparseable dates fall back to a
safe default rather than failing — one odd message must not take down the whole
thread. Attachment *bytes* are handed back separately, so the parser never
looks inside an attachment; that decision belongs to the security gate.

---

### 7. `feat(security): add fail-closed attachment gate`

**What:** The checkpoint every attachment must pass before anything opens it:
digest check, size and count limits, and file-type detection from the file's
actual content.

**Why:** Attachments are the most dangerous part of an email, so this gate
assumes the worst. A filename is never evidence — a virus named `invoice.pdf`
is caught because its *bytes* say it is a program. Office documents get extra
checks as archives (without unpacking them): macros are refused, entries that
would write outside their folder are refused, and a small file that expands to
hundreds of megabytes is refused. Temporary filenames are derived from the
content digest, so a filename like `../../etc/passwd` can never reach disk.

---

### 8. `feat(extractors): read PDF and DOCX text in an isolated process`

**What:** Pulls text out of PDFs and Word documents — in a separate,
short-lived process, not in the web app.

**Why:** These libraries are the only part of the system that has to interpret
a complicated, attacker-controlled format, and a malformed file can make them
hang, crash or eat all the memory. So they run somewhere we can afford to lose:
the child process starts with a nearly empty environment (an API key in the
parent is invisible to it), caps its own CPU, memory, open files and file size,
receives the document over a pipe so nothing is ever written to disk, and is
killed if it runs too long. Crashes and hangs come back as a reported result,
never as an exception that takes the app down.

---

### 9. `feat(reader): add the seam between the gate and the sandbox`

**What:** Decides which attachments earn a child process, and records the
verdict on each one.

**Why:** Kept deliberately thin, with no parsing logic of its own — risky work
stays in the sandbox, policy decisions stay here. A document that yields no
text is reported as "probably a scan" rather than silently empty, and one
unreadable attachment never stops the others.

---

### 10. `feat(privacy): mask personal data behind stable placeholders`

**What:** Finds personal data — emails, phone numbers, card numbers, bank
details, participant names — and swaps it for placeholders like `[EMAIL_1]`
before storing or summarising. Real values are put back only in the answer
shown on screen.

**Why:** The history database and any log line hold masked text only, so a
stolen database file leaks nothing. The same value always gets the same
placeholder, so the model can still tell participants apart. Detection is
deliberately conservative: card numbers are checksum-verified and phone numbers
digit-counted, so dates, amounts and quantities the summary depends on are
never destroyed. Names come from the email's own headers rather than being
guessed. A heavier detector (Presidio) can be switched on, and falls back
automatically when it is not installed.

---

### 11. `feat(enrich): derive thread facts without the model`

**What:** Works out — with ordinary code, no AI — who is in the thread, who
wrote last, how long it has been waiting, which dates are mentioned, and which
questions are still unanswered.

**Why:** This is the heart of the design. Anything a normal program can
establish reliably is established here, then handed to the model as ground
truth and shown in the interface directly. A deadline on screen is one a
program found in the text, not one a language model produced. Weekday phrases
("next Friday") are resolved with explicit, tested rules, because the date
library refuses that phrase entirely — and the resolved date is always shown to
the user, so a wrong reading is visible rather than hidden inside a summary.

---

### 12. `feat(summarizer): add the local model stage with structured output`

**What:** The one step that actually uses a language model. It gets a thread
that has already been parsed, screened, extracted and masked, and must return a
single JSON object.

**Why:** The model's job is kept narrow on purpose. It is given a JSON schema
and must answer with it, so free-form text never reaches the application. Email
content is fenced between markers and labelled as untrusted data, and any
marker *inside* the content is neutralised so a crafted email cannot close the
fence early and have its text read as instructions. A secret marker in the
system prompt reveals a model that has been talked into repeating its
instructions. The model has no tools, no network and no ability to act, so the
worst a successful attack achieves is a misleading summary — which the
guardrails then flag. Threads too long to fit are summarised in parts and
combined, always keeping the newest message in full because it decides the next
step. Prompts live in `prompts/*.txt` so wording changes read as plain diffs.

---

### 13. `feat(guards): verify the model's answer before it is shown`

**What:** Three checks on everything the model produces — injection detection,
a canary that catches a leaked prompt, and grounding (every date, amount and
address must exist in the source). Plus a fallback summary built from facts
alone when the model gives nothing usable.

**Why:** A language model is a useful component and an unreliable witness, so
its output is treated as a claim to be checked rather than a result to show.
Injection detection *flags* rather than blocks, because the model has no tools
and cannot act anyway — but you deserve to know the email was built to
manipulate an assistant. The fallback is deliberately dull: it can only state
what the enrichment stage established, so it is structurally incapable of
inventing a date. An honest plain answer beats an error page.

---

### 14. `fix(privacy): keep placeholders consistent across a whole thread`

**What:** Masking was numbered per call, so the first address in one message
and the first in another both became `[EMAIL_1]` — while being different
people.

**Why:** The model would have been told two strangers were the same person, and
restoring the values would have put the wrong name back on screen. The fix is
structural rather than a patch: the counters now live on a `Masker` that spans
the whole thread. Found by reading my own code before wiring it up; the failing
test was written first.

---

### 15. `feat(orchestrator): run the stages in order with a clear failure policy`

**What:** The assembly line — the only module that knows the whole pipeline
shape.

**Why:** Every other stage stays unaware of the others, which is what makes
them testable alone. The ordering is deliberate: facts and injection scanning
run on the *real* text, because masking first would hide the very details they
exist to find. Only one failure is fatal — the model service not running, which
is a setup problem you must fix. Everything else degrades to a smaller but
honest answer.

---

### 16. `feat(store): add local history with easy, complete deletion`

**What:** A single SQLite file holding masked results, with delete-one and
delete-everything.

**Why:** A copied database leaks nothing, because only masked content is ever
written, and the file is created owner-readable only. Deleting is first-class
because a tool that quietly accumulates a record of your mail is not one people
should trust. Stored rows are re-validated on read, so a corrupted row is
skipped rather than trusted.

---

### 17. `feat(api): add the web application and interface`

**What:** The HTTP layer and a single self-contained page.

**Why:** The app is thin — parse, enforce limits, call the pipeline, return
honest status codes — because all the interesting behaviour belongs in code
that can be tested without a web server. Dependencies are injected, so the
entire HTTP surface is tested without a model running. The page loads no
external script, font or image, so it works with the network off, and a strict
Content-Security-Policy makes the browser enforce that. Every value from an
email is written with `textContent`, so a subject line containing markup is
shown as text. The page deliberately shows the uncomfortable parts — blocked
attachments, warnings, unverified claims — because the point is to be
trustworthy, not tidy.

---

### 18–20. Three fixes found by running the real thing

Unit tests pass on the cases you thought of. Running the actual product on an
actual email found four more:

- **The result panel showed when empty** — `display: grid` silently overrides
  the `hidden` attribute.
- **Header dates were reported as invented.** The grounding check searched
  message bodies only, so a summary correctly saying "Alice wrote on
  2026-08-19" was flagged. Headers are part of the source too.
- **Relative deadlines were checked against the year 2000.** A hard-coded
  reference date meant "before Friday" could never match the deadline the facts
  had derived.
- **The model was receiving raw email addresses.** Bodies and attachments were
  masked; the `From:` and `To:` lines, which also go into the prompt, were not.
  The privacy guarantee was narrower than it claimed. This also fixed a quality
  problem — the model had been copying long addresses inaccurately, and works
  reliably with short placeholder tokens.

---

### 21. `test(evals): add a golden dataset and scoring harness`

**What:** Fifteen hand-written threads with known answers, and a scorer.

**Why:** Unit tests answer "does each part behave as specified?". This answers
"on realistic email, how often is the whole thing right?" — a different
question needing different tools. Fixtures are generated by a readable script
rather than committed as opaque blobs, so every case can be reviewed in one
file. Scoring is tolerant about wording and strict about anything that would
mislead somebody. Whether personal data reached the model is checked by
recording exactly what the model was sent.

---

### 22. `feat: tell the model what the application already knows`

**What:** Today's date, which address is the user's own, and what the injection
detector found are now all passed to the model.

**Why:** The first eval run scored 7/15 and every gap had the same shape: the
application held a fact and never passed it on, so the model guessed. It was
inferring today's date from "days since the last message" and quoting the
result, which the grounding check then flagged as invented. Because headers are
masked, every address looked alike, so on a thread the user had sent themselves
it decided the user was waiting on themselves. And the injection detector's
findings — available before the model ran — were kept from it.

---

### 23. `feat(guards): defuse a detected attack deterministically`

**What:** When injection is detected, the recommended action is replaced with a
safe deterministic one and the model's suggestion is kept as a key point.

**Why:** The eval caught the model describing an attack correctly and then
advising compliance with it: *"Reply to the customer confirming the balance is
settled as instructed."* Detection is deterministic and reliable; the model's
judgement is neither. So when we know a thread is hostile, the product stops
forwarding the model's advice. It stays visible — you should be able to see
what the model was talked into — but it is not what the interface tells you
to do.

Also added: "who is waiting on whom" is now corrected by code in the two cases
where code can tell better than the model, and the prompt forbids using any
date that is not in the thread or in the verified facts.

---

### 24. `docs` + `test`: documentation and the promises made testable

**What:** A README, a threat model listing twenty threats and six things this
deliberately does *not* defend against, and two sets of tests that turn written
promises into checked ones.

**Why:** Two claims in the README were documentation only, which means they
were one careless change away from being false:

- *"Nothing leaves the machine"* is now a test. No application file may
  reference a remote host, the page may load only its own assets, and no
  pipeline stage except the model client may import a networking library. A
  future change that adds a CDN font or an analytics call fails the suite.
- *"The email is fenced as untrusted data"* is now a test over the prompt
  templates themselves: the content must sit between the markers, and the
  do-not-follow rule must be repeated **after** the untrusted text, not only
  before it. A well-meaning edit to the wording fails the suite.

The threat model's "what this does not defend against" section is the part
worth reading. A security document that only lists wins is marketing.

---

### 25. `fix(ui): group security warnings by kind`

**What:** Several findings of the same kind now share one banner.

**Why:** The injection fixture trips two detectors at once — an instruction to
ignore earlier instructions, and an attempt to change the assistant's role —
which produced two banners with the same heading. Nothing is hidden; the
warning just reads as one warning.

---

### 26. `chore: rename the project to EmailAssist`

**What:** Application title, package name and the `EMAIL_AGENT_*` environment
prefix all became `EMAILASSIST_*`.

**Why:** The repository is named EmailAssist, and a project whose code calls
itself something else is a small, constant source of confusion.

---

### 27. The recorded demo site

**What:** A static site at `demo/` that lets anyone click through the fifteen
test threads and see what the pipeline produced for each one, without
installing anything.

**Why, and the honest bit:** EmailAssist cannot be deployed to a serverless
host. It runs a language model on your machine — that is the product, not an
implementation detail, and it is what the threat model rests on. Vercel has no
persistent process to hold a model, a size limit far below a 2.5 GB model, and
a timeout shorter than one analysis. Making it "work" there would mean calling
a hosted model API, at which point the email leaves the machine and the project
becomes the thing it was built not to be.

So the demo is **recorded, and says so on the page**. `demo/capture.py` runs
the genuine pipeline against the test threads on a machine that does have the
model, and records exactly what came back. Nothing in the repository fabricates
demo output; there is no code path that could.

Two design decisions worth noting:

- **The demo uses the application's own renderer.** `render.js` was split out
  of `app.js` so both pages draw a result with the same code. A demo that
  reimplemented the interface would drift from it, and then it would be showing
  something the real product does not do.
- **Copies are guarded by a test.** Vercel serves `demo/` as-is and cannot
  reach outside it, so the shared stylesheet and renderer are copied in.
  Copies go stale, so `demo/sync_assets.py` is the only way they are updated
  and a test fails if they drift.

`tests/test_demo.py` also checks the honesty claims: the page must say plainly
that it is recorded, must link to both the capture script and the raw data, and
the recorded source emails must be byte-identical to the real fixtures.

---

## Where it ended up

- **8 pipeline stages**, of which one uses a language model
- **390 tests**, 97% line coverage, every commit gated by black, ruff and the
  suite
- **15/15 on the golden dataset**, reproduced on two consecutive runs, up from
  7/15 on the first
- **~18 seconds** per thread on an 8 GB M1, entirely offline

The evaluation, not the unit tests, is what made the product better. Unit tests
confirmed each part did what I had specified. The evals kept showing that what
I had specified was not enough — the app knew today's date and never told the
model; it knew whose mailbox this was and never said; it detected an attack and
kept the finding to itself while the model went on to recommend complying with
it. Each of those was invisible until the whole thing ran on realistic mail.

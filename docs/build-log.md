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

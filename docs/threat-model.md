# Threat model

What this project assumes, what it defends against, and what it does not.

## What we are protecting

- **The user's mail.** Its contents must not leave the machine, and must not be
  written anywhere in a form that leaks personal data.
- **The user's machine.** Opening an email must not let an attacker run code.
- **The user's judgement.** A summary that quietly misleads is worse than no
  summary, so uncertainty is shown rather than hidden.

## Who the attacker is

Anybody who can send the user an email. That is effectively everybody, and it
is the only assumption needed: every input to this system is attacker
controlled. Headers, body text, filenames, attachment contents and declared
MIME types are all written by a stranger.

## Trust boundaries

```
  ATTACKER CONTROLLED                    TRUSTED
  ┌──────────────────────┐               ┌────────────────────────┐
  │ .eml bytes           │──── parse ───▶│ EmailThread            │
  │ headers, body, HTML  │               │ (text only, no markup) │
  │ attachment bytes     │               └────────────────────────┘
  │ filenames, MIME      │                          │
  └──────────────────────┘                          ▼
            │                             ┌────────────────────────┐
            │                             │ derived facts          │
            └──── security gate ─────────▶│ (found by code)        │
                        │                 └────────────────────────┘
                        ▼                            │
              ┌───────────────────┐                  ▼
              │ sandboxed process │        ┌────────────────────────┐
              │ (PDF/DOCX parser) │───────▶│ local model            │
              └───────────────────┘        │ (no tools, no network) │
                                           └────────────────────────┘
                                                     │
                                                     ▼
                                           ┌────────────────────────┐
                                           │ guardrails, then UI    │
                                           └────────────────────────┘
```

The model sits *inside* the trusted side but is treated as untrusted output:
everything it returns is validated and checked before it is shown.

## Threats and what is done about them

| # | Threat | Defence | Where |
|---|--------|---------|-------|
| 1 | Malware attached as a document | Type detected from content, not filename; only PDF and DOCX opened | `app/pipeline/security.py` |
| 2 | Macro-enabled Office document renamed `.docx` | Archive inspected for macro parts and macro content types; refused | `app/pipeline/security.py` |
| 3 | Malformed document crashes or hangs the parser | Parsing runs in a separate process with a timeout; parent kills it | `app/pipeline/sandbox.py` |
| 4 | Decompression bomb | Uncompressed size read from archive metadata, never expanded | `app/pipeline/security.py` |
| 5 | Path traversal via filename or archive entry | Storage names derived from content digest; archive entries checked | `app/pipeline/security.py` |
| 6 | Document parser tries to read or write the filesystem | Child process has a zero file-size limit and a near-empty environment | `app/pipeline/_extract_worker.py` |
| 7 | Tracking pixel confirms the mail was read | HTML is parsed for text only; remote-resource tags removed; the app makes no outbound requests | `app/pipeline/text.py` |
| 8 | Script in an HTML body | HTML is never rendered; script elements removed with their contents | `app/pipeline/text.py` |
| 9 | Prompt injection in the body | Content fenced and labelled as data; detector reports it; the model has no tools | `app/pipeline/guards.py` |
| 10 | Prompt injection hidden inside an attachment | Same detector runs over extracted attachment text | `app/pipeline/guards.py` |
| 11 | Injected text closing the fence to escape it | Fence markers inside content are neutralised before the prompt is built | `app/pipeline/summarizer.py` |
| 12 | Model persuaded to leak its instructions | Per-session canary in the system prompt; its presence discards the answer | `app/pipeline/guards.py` |
| 13 | Model follows an injected instruction | When an attack is detected, the recommended action is replaced deterministically | `app/pipeline/guards.py` |
| 14 | Model invents a deadline or amount | Every date, figure and address checked against the source | `app/pipeline/guards.py` |
| 15 | Personal data written to disk or logs | Masked before storage and before the model; restored only for display | `app/pipeline/privacy.py` |
| 16 | Data sent to a third party | Model endpoint is loopback by default; the interface loads no external resource; CSP enforces it | `app/config.py`, `app/main.py` |
| 17 | Email content injected into the web page | All values written with `textContent`; strict CSP; `nosniff` | `app/static/app.js`, `app/main.py` |
| 18 | Oversized upload exhausting memory | Size checked while reading, per file and in total | `app/main.py` |
| 19 | Stolen history database | Only masked content stored; file created owner-readable only | `app/store.py` |
| 20 | Compromised dependency | Every direct dependency pinned; heavy optional ones separated | `requirements*.txt` |

## What this does **not** defend against

Being explicit about the gaps matters more than the list above.

- **A compromised machine.** If an attacker already runs code as the user, they
  can read the mail directly; nothing here helps.
- **A malicious local model.** The model file is trusted. A backdoored model
  could produce misleading summaries. The guardrails limit the damage - it has
  no tools and its factual claims are checked - but it is a real assumption.
- **Every possible injection phrasing.** The detector recognises known shapes.
  A novel phrasing may not be flagged. This is why the model has no tools: the
  design assumes detection will sometimes fail.
- **Semantic accuracy.** The model can misread a thread in ways no checker can
  catch. The interface shows what was verified and what was not, but a wrong
  reading is possible and the user is the last check.
- **Scanned documents.** Images and scans are not read at all, so an
  instruction inside a scanned PDF is neither summarised nor detected. Such
  attachments are reported as unreadable rather than silently skipped.
- **Multi-user deployment.** This is a single-user, local tool. There is no
  authentication, and the history is not partitioned. Exposing it on a network
  would need both.
- **The attachment sandbox's resource limits, on native Windows.** They are
  applied with the POSIX `resource` module (`app/pipeline/_extract_worker.py`),
  which does not exist on Windows; the import failure is caught and the limits
  are simply skipped there. The process-isolation and timeout still apply on
  every platform - only the CPU/memory/file-handle caps are POSIX-only.

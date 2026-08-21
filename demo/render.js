/*
 * Rendering an analysis into the page.
 *
 * Kept apart from app.js because two pages draw the same result: the live
 * application, and the static demo site. The demo must show exactly what the
 * real interface shows, and the only way to guarantee that is for both to run
 * this same code.
 *
 * Two habits run through this file:
 *
 *  - every value that came from an email is written with textContent, never
 *    innerHTML, so a subject line containing markup is shown as text and can
 *    never become part of the page;
 *  - nothing is hidden. Rejected attachments, injection warnings, unverified
 *    claims and degraded answers are all shown, because the point of the tool
 *    is to be trustworthy, not to look clean.
 */

"use strict";

const el = (id) => document.getElementById(id);

/* Ticking an action item is persisted by the application, but the demo is a
   static site with nowhere to persist it. So the state and the handler are
   supplied by whichever page is drawing: the application passes both, the
   demo passes neither and its checkboxes simply stay local to the page.
   Keeping the choice here rather than in two copies of this file is what
   lets the demo load the application's own renderer unchanged. */
let actionItemsDone = new Set();
let onActionItemToggled = null;

function setActionItemState({ done = [], onToggle = null } = {}) {
  actionItemsDone = new Set(done);
  onActionItemToggled = onToggle;
}

/* -------------------------------------------------------------- results */

function renderResult(result) {
  el("result-subject").textContent = result.thread_subject;

  const urgency = el("urgency");
  urgency.textContent = `${result.summary.urgency} urgency`;
  urgency.className = `badge badge--${result.summary.urgency}`;

  el("summary-text").textContent = result.summary.summary;
  el("next-step-text").textContent = result.summary.suggested_next_step;

  renderBanners(result);
  renderActionItems(result.summary.action_items);
  renderList(el("key-points"), result.summary.key_points, "No specific points were recorded.");
  renderFacts(result.facts, result.summary.waiting_on);
  renderAttachments(result.attachments);

  const model = result.degraded ? "built from the thread without the model" : `written by ${result.model_used}`;
  el("provenance").textContent = `${model} · ${result.duration_seconds}s · nothing left this machine`;

  const resultBox = el("result");
  resultBox.hidden = false;
  resultBox.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderBanners(result) {
  const banners = el("banners");
  banners.replaceChildren();

  // One email often trips several detectors at once. Grouping by kind keeps
  // the warning to a single banner while still listing every finding.
  const byKind = new Map();
  result.security_flags.forEach((flag) => {
    if (!byKind.has(flag.kind)) byKind.set(flag.kind, []);
    byKind.get(flag.kind).push(flag.detail);
  });

  byKind.forEach((details, kind) => {
    banners.append(banner("danger", titleForFlag(kind), details));
  });

  if (result.unverified_claims.length > 0) {
    banners.append(
      banner(
        "warn",
        "Some details could not be verified",
        `${result.unverified_claims.join("; ")}. Check these against the email before acting on them.`
      )
    );
  }

  if (result.degraded) {
    banners.append(
      banner(
        "warn",
        "Summary built without the model",
        "The model did not return a usable answer, so this summary was assembled from the thread itself."
      )
    );
  }
}

function titleForFlag(kind) {
  switch (kind) {
    case "prompt_injection":
      return "This email tries to give instructions to an AI assistant";
    case "prompt_leak":
      return "The model was manipulated and its answer was discarded";
    case "model_output":
      return "The model did not answer properly";
    default:
      return "Something needs your attention";
  }
}

function banner(tone, title, detail) {
  const box = document.createElement("div");
  box.className = `banner banner--${tone}`;

  const text = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = title;
  text.append(strong);

  const details = Array.isArray(detail) ? detail : [detail];
  if (details.length === 1) {
    const body = document.createElement("span");
    body.textContent = details[0];
    text.append(body);
  } else {
    const list = document.createElement("ul");
    list.className = "banner-list";
    details.forEach((line) => {
      const row = document.createElement("li");
      row.textContent = line;
      list.append(row);
    });
    text.append(list);
  }

  box.append(text);
  return box;
}

function renderActionItems(items) {
  const list = el("action-items");
  list.replaceChildren();

  if (items.length === 0) {
    list.append(emptyNote("Nothing to do from this thread."));
    return;
  }

  items.forEach((item, index) => {
    const row = document.createElement("li");

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.id = `action-${index}`;
    checkbox.checked = actionItemsDone.has(index);
    checkbox.addEventListener("change", () => {
      row.classList.toggle("is-done", checkbox.checked);
      if (onActionItemToggled) onActionItemToggled(index, checkbox.checked);
    });

    const label = document.createElement("label");
    label.htmlFor = checkbox.id;
    label.textContent = item.task;

    const owner = document.createElement("span");
    owner.className = "owner";
    owner.textContent = item.owner === "me" ? "you" : "them";

    const text = document.createElement("div");
    text.append(label, document.createTextNode(" "), owner);

    if (item.due) {
      const due = document.createElement("span");
      due.className = "due";
      due.textContent = ` · due ${item.due}`;
      text.append(due);
    }

    row.append(checkbox, text);
    row.classList.toggle("is-done", checkbox.checked);
    list.append(row);
  });
}

function renderList(list, values, emptyMessage) {
  list.replaceChildren();
  if (values.length === 0) {
    list.append(emptyNote(emptyMessage));
    return;
  }
  values.forEach((value) => {
    const row = document.createElement("li");
    row.textContent = value;
    list.append(row);
  });
}

function renderFacts(facts, waitingOn) {
  const list = el("facts");
  list.replaceChildren();

  const waiting = { me: "you", them: "them", nobody: "nobody" }[waitingOn] || waitingOn;

  addFact(list, "Messages", String(facts.message_count));
  addFact(list, "People", facts.participants.join(", ") || "unknown");
  addFact(list, "Waiting on", waiting);

  if (facts.days_since_last_message !== null) {
    const days = facts.days_since_last_message;
    addFact(list, "Last reply", days === 0 ? "today" : `${days} day${days === 1 ? "" : "s"} ago`);
  }
  if (facts.dates_mentioned.length > 0) {
    addFact(list, "Dates found", facts.dates_mentioned.join(", "));
  }
  if (facts.open_questions.length > 0) {
    addFact(list, "Unanswered", facts.open_questions.join(" "));
  }
}

function addFact(list, label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = label;
  const detail = document.createElement("dd");
  detail.textContent = value;
  row.append(term, detail);
  list.append(row);
}

function renderAttachments(attachments) {
  const list = el("attachments");
  list.replaceChildren();

  if (attachments.length === 0) {
    list.append(emptyNote("No attachments in this thread."));
    return;
  }

  attachments.forEach((attachment) => {
    const row = document.createElement("li");

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = attachment.filename;

    const verdict = document.createElement("span");
    const blocked = attachment.status !== "extracted";
    verdict.className = blocked ? "verdict verdict--bad" : "verdict";
    verdict.textContent = blocked
      ? `${attachment.status}: ${attachment.reason || "not opened"}`
      : `read · ${attachment.extracted_text.length.toLocaleString()} characters of text`;

    row.append(name, verdict);
    list.append(row);
  });
}

function emptyNote(message) {
  const note = document.createElement("li");
  note.className = "empty";
  note.textContent = message;
  return note;
}

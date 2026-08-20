/*
 * Interface logic.
 *
 * Two habits run through this file:
 *
 *  - every value that came from an email is written with textContent, never
 *    innerHTML, so a subject line containing markup is shown as text and can
 *    never become part of the page;
 *  - the interface never hides a problem. Rejected attachments, injection
 *    warnings, unverified claims and degraded answers are all shown, because
 *    the point of the tool is to be trustworthy, not to look clean.
 */

"use strict";

const MAX_FILES = 20;

const state = { files: [] };

const el = (id) => document.getElementById(id);

const dropzone = el("dropzone");
const fileInput = el("file-input");
const fileList = el("file-list");
const analyseButton = el("analyse-button");
const clearButton = el("clear-button");
const statusBox = el("status");
const resultBox = el("result");

/* ---------------------------------------------------------------- files */

function addFiles(incoming) {
  const accepted = Array.from(incoming).filter((file) => file.name.toLowerCase().endsWith(".eml"));
  const rejected = incoming.length - accepted.length;

  state.files = state.files.concat(accepted).slice(0, MAX_FILES);
  renderFiles();

  if (rejected > 0) {
    showStatus(`Skipped ${rejected} file${rejected === 1 ? "" : "s"}: only .eml files are read.`, "error");
  }
}

function renderFiles() {
  fileList.replaceChildren();

  state.files.forEach((file) => {
    const row = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = file.name;
    const size = document.createElement("span");
    size.className = "size";
    size.textContent = formatSize(file.size);
    row.append(name, size);
    fileList.append(row);
  });

  analyseButton.disabled = state.files.length === 0;
  clearButton.hidden = state.files.length === 0;
}

function clearFiles() {
  state.files = [];
  fileInput.value = "";
  renderFiles();
  hide(statusBox);
  hide(resultBox);
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* ------------------------------------------------------------ analysing */

async function analyse(event) {
  event.preventDefault();
  if (state.files.length === 0) return;

  const body = new FormData();
  state.files.forEach((file) => body.append("files", file));

  analyseButton.disabled = true;
  showWorking("Reading the thread, checking attachments and asking the local model. This can take a minute.");
  hide(resultBox);

  try {
    const response = await fetch("/api/analyse", { method: "POST", body });
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      showStatus(payload.detail || `The analysis failed (HTTP ${response.status}).`, "error");
      return;
    }

    hide(statusBox);
    renderResult(payload);
    await loadHistory();
  } catch (error) {
    showStatus(`Could not reach the local server: ${error.message}`, "error");
  } finally {
    analyseButton.disabled = state.files.length === 0;
  }
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

  resultBox.hidden = false;
  resultBox.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderBanners(result) {
  const banners = el("banners");
  banners.replaceChildren();

  result.security_flags.forEach((flag) => {
    banners.append(banner("danger", titleForFlag(flag.kind), flag.detail));
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
  const body = document.createElement("span");
  body.textContent = detail;
  text.append(strong, body);
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

/* -------------------------------------------------------------- history */

async function loadHistory() {
  const list = el("history-list");
  try {
    const response = await fetch("/api/history");
    const payload = await response.json();
    list.replaceChildren();

    if (payload.entries.length === 0) {
      list.append(emptyNote("Nothing analysed yet."));
      return;
    }

    payload.entries.forEach((entry) => list.append(historyRow(entry)));
  } catch {
    list.replaceChildren(emptyNote("History could not be loaded."));
  }
}

function historyRow(entry) {
  const row = document.createElement("li");

  const badge = document.createElement("span");
  badge.className = `badge badge--${entry.urgency}`;
  badge.textContent = entry.urgency;

  const subject = document.createElement("div");
  subject.className = "subject";
  subject.textContent = entry.thread_subject;
  const step = document.createElement("span");
  step.textContent = entry.suggested_next_step;
  subject.append(step);

  const when = document.createElement("time");
  when.dateTime = entry.created_at;
  when.textContent = new Date(entry.created_at).toLocaleString();

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "icon-button";
  remove.textContent = "Delete";
  remove.addEventListener("click", () => deleteEntry(entry.id));

  row.append(badge, subject, when, remove);
  return row;
}

async function deleteEntry(id) {
  await fetch(`/api/history/${id}`, { method: "DELETE" });
  await loadHistory();
}

async function purgeHistory() {
  if (!window.confirm("Delete every stored analysis? This cannot be undone.")) return;
  await fetch("/api/history", { method: "DELETE" });
  await loadHistory();
}

/* --------------------------------------------------------------- health */

async function loadHealth() {
  const box = el("health");
  const dot = box.querySelector(".dot");
  const text = box.querySelector(".health-text");

  try {
    const health = await (await fetch("/api/health")).json();
    if (health.model_available) {
      dot.className = "dot dot--ok";
      text.textContent = `${health.model} ready, running locally`;
    } else {
      dot.className = "dot dot--bad";
      text.textContent = `${health.model} not reachable — run: ollama serve`;
    }
  } catch {
    dot.className = "dot dot--bad";
    text.textContent = "the local server is not responding";
  }
}

/* ---------------------------------------------------------------- chrome */

function showStatus(message, tone) {
  statusBox.className = tone === "error" ? "status status--error" : "status";
  statusBox.textContent = message;
  statusBox.hidden = false;
}

function showWorking(message) {
  statusBox.className = "status status--working";
  statusBox.replaceChildren();
  const spinner = document.createElement("span");
  spinner.className = "spinner";
  const text = document.createElement("span");
  text.textContent = message;
  statusBox.append(spinner, text);
  statusBox.hidden = false;
}

function hide(node) {
  node.hidden = true;
}

/* ----------------------------------------------------------------- wiring */

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});

["dragenter", "dragover"].forEach((name) =>
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("is-over");
  })
);

["dragleave", "drop"].forEach((name) =>
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-over");
  })
);

dropzone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
fileInput.addEventListener("change", () => addFiles(fileInput.files));
el("upload-form").addEventListener("submit", analyse);
clearButton.addEventListener("click", clearFiles);
el("purge-button").addEventListener("click", purgeHistory);

loadHealth();
loadHistory();

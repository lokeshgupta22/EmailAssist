/*
 * Application wiring: choosing files, calling the API, and the history list.
 *
 * Drawing a result lives in render.js, which this page and the static demo
 * site both load, so the demo cannot drift from what the real interface shows.
 */

"use strict";

const MAX_FILES = 20;

const state = { files: [], activeHistoryId: null };

const dropzone = el("dropzone");
const fileInput = el("file-input");
const fileList = el("file-list");
const analyseButton = el("analyse-button");
const clearButton = el("clear-button");
const statusBox = el("status");
const resultBox = el("result");
const uploadView = el("upload-view");

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
    state.activeHistoryId = payload.id ?? null;
    showResult(payload);
    await loadHistory();
  } catch (error) {
    showStatus(`Could not reach the local server: ${error.message}`, "error");
  } finally {
    analyseButton.disabled = state.files.length === 0;
  }
}

/* ------------------------------------------------------------------ views */

function showResult(result) {
  hide(uploadView);
  renderResult(result);
}

function showUploadView() {
  state.activeHistoryId = null;
  hide(resultBox);
  hide(statusBox);
  uploadView.hidden = false;
  highlightActiveHistoryItem();
}

async function openHistoryEntry(id) {
  state.activeHistoryId = id;
  highlightActiveHistoryItem();

  try {
    const response = await fetch(`/api/history/${id}`);
    if (!response.ok) {
      showUploadView();
      showStatus("That analysis is no longer available.", "error");
      await loadHistory();
      return;
    }
    const payload = await response.json();
    showResult(payload.result);
  } catch (error) {
    showUploadView();
    showStatus(`Could not reach the local server: ${error.message}`, "error");
  }
}

function highlightActiveHistoryItem() {
  el("history-list").querySelectorAll(".history-item").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.id === String(state.activeHistoryId));
  });
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
    highlightActiveHistoryItem();
  } catch {
    list.replaceChildren(emptyNote("History could not be loaded."));
  }
}

function historyRow(entry) {
  const row = document.createElement("li");
  row.className = "history-item";
  row.dataset.id = String(entry.id);
  row.tabIndex = 0;
  row.setAttribute("role", "button");

  const top = document.createElement("div");
  top.className = "history-item-top";

  const badge = document.createElement("span");
  badge.className = `badge badge--sm badge--${entry.urgency}`;
  badge.textContent = entry.urgency;

  const when = document.createElement("time");
  when.dateTime = entry.created_at;
  when.textContent = new Date(entry.created_at).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });

  top.append(badge, when);

  const subject = document.createElement("p");
  subject.className = "history-item-subject";
  subject.textContent = entry.thread_subject;

  const step = document.createElement("p");
  step.className = "history-item-step";
  step.textContent = entry.suggested_next_step;

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "history-item-delete";
  remove.title = "Delete this analysis";
  remove.setAttribute("aria-label", "Delete this analysis");
  remove.textContent = "✕";
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteEntry(entry.id);
  });

  const open = () => openHistoryEntry(entry.id);
  row.addEventListener("click", open);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });

  row.append(top, subject, step, remove);
  return row;
}

async function deleteEntry(id) {
  await fetch(`/api/history/${id}`, { method: "DELETE" });
  if (state.activeHistoryId === id) showUploadView();
  await loadHistory();
}

async function purgeHistory() {
  if (!window.confirm("Delete every stored analysis? This cannot be undone.")) return;
  await fetch("/api/history", { method: "DELETE" });
  showUploadView();
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
el("new-analysis-button").addEventListener("click", () => {
  clearFiles();
  showUploadView();
});

loadHealth();
loadHistory();

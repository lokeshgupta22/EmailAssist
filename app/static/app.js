/*
 * Application wiring: choosing files, calling the API, and the workspace
 * views. Model health and the history list are shared with about.html, so
 * that behaviour lives in sidebar.js instead of here.
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
    showResult(payload, { id: payload.id ?? null });
    await sidebarHistory.refresh();
  } catch (error) {
    showStatus(`Could not reach the local server: ${error.message}`, "error");
  } finally {
    analyseButton.disabled = state.files.length === 0;
  }
}

/* ------------------------------------------------------------------ views */

function showResult(result, { id = null, done = [] } = {}) {
  // Ticking an item here writes through to the same state the Tasks page
  // reads, so the two views never disagree about what is still outstanding.
  setActionItemState({
    done,
    onToggle: id === null ? null : (index, isDone) => saveTaskDone(id, index, isDone),
  });
  hide(uploadView);
  renderResult(result);
}

async function saveTaskDone(entryId, index, done) {
  try {
    await fetch(`/api/tasks/${entryId}/${index}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ done }),
    });
  } catch {
    // A tick that could not be saved is not worth interrupting the page for;
    // the next load simply shows the state that did reach the database.
  }
}

function showUploadView() {
  state.activeHistoryId = null;
  highlightHistoryItem(null);
  hide(resultBox);
  hide(statusBox);
  uploadView.hidden = false;
}

async function openHistoryEntry(id) {
  state.activeHistoryId = id;
  highlightHistoryItem(id);

  try {
    const response = await fetch(`/api/history/${id}`);
    if (!response.ok) {
      showUploadView();
      showStatus("That analysis is no longer available.", "error");
      await sidebarHistory.refresh();
      return;
    }
    const payload = await response.json();
    showResult(payload.result, { id: payload.id, done: payload.done_indexes || [] });
  } catch (error) {
    showUploadView();
    showStatus(`Could not reach the local server: ${error.message}`, "error");
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
el("new-analysis-button").addEventListener("click", () => {
  clearFiles();
  showUploadView();
});

const sidebarHistory = initSidebarHistory({
  onSelect: openHistoryEntry,
  onDeleted: (id) => {
    if (id === null || state.activeHistoryId === id) showUploadView();
  },
});

loadHealth();

// A history row clicked from another page (About) links back here as
// /?open=<id>, so that entry reopens immediately instead of the empty
// upload view.
const openId = new URLSearchParams(location.search).get("open");
if (openId) {
  openHistoryEntry(Number(openId));
  window.history.replaceState(null, "", "/");
}

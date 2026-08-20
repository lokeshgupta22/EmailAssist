/*
 * The demo site.
 *
 * It has no backend and cannot run a language model, so the "Analyse a
 * thread" view is real but disabled. What it can do is offer fifteen
 * genuine results, recorded by running the real pipeline locally against
 * the project's test threads (see results.json) and handed here to
 * render.js, which is the same code the live application uses to draw a
 * result. The History list is therefore not a picker widget of its own; it
 * is the application's history list, just pre-filled instead of empty.
 */

"use strict";

const state = { threads: [], activeIndex: null };

const uploadView = el("upload-view");
const resultBox = el("result");

async function load() {
  const list = el("history-list");
  try {
    const data = await (await fetch("results.json")).json();
    state.threads = data.threads;

    el("capture-meta").textContent =
      `Captured ${new Date(data.captured_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      })} with ${data.model}.`;

    list.replaceChildren();
    state.threads.forEach((thread, index) => list.append(historyRow(thread, index)));
  } catch (error) {
    list.replaceChildren();
    const failure = document.createElement("li");
    failure.className = "picker-error";
    failure.textContent = `The recorded results could not be loaded: ${error.message}`;
    list.append(failure);
  }
}

function historyRow(thread, index) {
  const row = document.createElement("li");
  row.className = "history-item history-item--static";
  row.tabIndex = 0;
  row.setAttribute("role", "button");

  const top = document.createElement("div");
  top.className = "history-item-top";

  const badge = document.createElement("span");
  badge.className = `badge badge--sm badge--${thread.result.summary.urgency}`;
  badge.textContent = thread.result.summary.urgency;
  top.append(badge);

  if (thread.result.security_flags.length > 0) {
    const flag = document.createElement("span");
    flag.className = "badge badge--sm badge--high";
    flag.textContent = "flagged";
    top.append(flag);
  }

  const subject = document.createElement("p");
  subject.className = "history-item-subject";
  subject.textContent = thread.title;

  const step = document.createElement("p");
  step.className = "history-item-step";
  step.textContent = thread.blurb;

  const open = () => select(index);
  row.addEventListener("click", open);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });

  row.append(top, subject, step);
  return row;
}

function select(index) {
  const thread = state.threads[index];
  if (!thread) return;

  state.activeIndex = index;
  highlightActiveHistoryItem();

  hide(uploadView);
  renderResult(thread.result);
  el("source-text").textContent = thread.source;
}

function highlightActiveHistoryItem() {
  el("history-list").querySelectorAll(".history-item").forEach((item, index) => {
    item.classList.toggle("is-active", index === state.activeIndex);
  });
}

function showUploadView() {
  state.activeIndex = null;
  highlightActiveHistoryItem();
  hide(resultBox);
  uploadView.hidden = false;
}

function hide(node) {
  node.hidden = true;
}

el("new-analysis-button").addEventListener("click", showUploadView);

load();

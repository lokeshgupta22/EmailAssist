/*
 * The demo site.
 *
 * It has no backend. Results were produced by running the real pipeline
 * locally against the project's test threads and recorded in results.json;
 * this file only lets you pick one and hands it to render.js, which is the
 * same code the live application uses to draw a result.
 */

"use strict";

const state = { threads: [], selected: null };

async function load() {
  const picker = el("picker");
  try {
    const data = await (await fetch("results.json")).json();
    state.threads = data.threads;

    el("capture-meta").textContent =
      `Captured ${new Date(data.captured_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      })} with ${data.model}, analysed as if today were ${data.reference_time.slice(0, 10)}.`;

    picker.replaceChildren();
    state.threads.forEach((thread, index) => picker.append(pickerRow(thread, index)));

    select(0);
  } catch (error) {
    picker.replaceChildren();
    const failure = document.createElement("li");
    failure.className = "picker-error";
    failure.textContent = `The recorded results could not be loaded: ${error.message}`;
    picker.append(failure);
  }
}

function pickerRow(thread, index) {
  const row = document.createElement("li");

  const button = document.createElement("button");
  button.type = "button";
  button.className = "picker-button";
  button.dataset.index = String(index);

  const title = document.createElement("span");
  title.className = "picker-title";
  title.textContent = thread.title;

  const blurb = document.createElement("span");
  blurb.className = "picker-blurb";
  blurb.textContent = thread.blurb;

  button.append(title, blurb);

  const flags = thread.result.security_flags;
  if (flags.length > 0) {
    const warning = document.createElement("span");
    warning.className = "picker-flag";
    warning.textContent = "flagged";
    button.append(warning);
  }

  button.addEventListener("click", () => select(index));
  row.append(button);
  return row;
}

function select(index) {
  const thread = state.threads[index];
  if (!thread) return;

  state.selected = index;
  document.querySelectorAll(".picker-button").forEach((button) => {
    button.classList.toggle("is-selected", Number(button.dataset.index) === index);
  });

  renderResult(thread.result);
  el("source-text").textContent = thread.source;
}

load();

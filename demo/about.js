/*
 * The demo's About page. Its only behaviour is the History list, built from
 * the same recorded results.json as the workspace page - clicking a row
 * navigates to index.html with that thread preloaded, since the analysis
 * itself is only ever shown there.
 */

"use strict";

async function load() {
  const list = el("history-list");
  try {
    const data = await (await fetch("results.json")).json();

    el("capture-meta").textContent =
      `Captured ${new Date(data.captured_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      })} with ${data.model}.`;

    list.replaceChildren();
    data.threads.forEach((thread, index) => list.append(historyRow(thread, index)));
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

  const open = () => { location.href = `index.html?open=${index}`; };
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

load();

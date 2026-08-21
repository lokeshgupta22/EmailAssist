/*
 * The demo's sidebar history list, shared by the pages that only link into
 * the workspace rather than drawing a result themselves (About and Tasks).
 *
 * The application builds this list from its API; the demo has no backend, so
 * it builds the same list from the recorded results.json. Kept in one file
 * because two pages need exactly the same rows - the workspace page has its
 * own copy because it also selects and highlights rows in place.
 */

"use strict";

/** Load results.json, fill in the capture note, and render the history rows. */
async function loadDemoHistory() {
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
    data.threads.forEach((thread, index) => list.append(demoHistoryRow(thread, index)));
    return data;
  } catch (error) {
    list.replaceChildren();
    const failure = document.createElement("li");
    failure.className = "picker-error";
    failure.textContent = `The recorded results could not be loaded: ${error.message}`;
    list.append(failure);
    return null;
  }
}

function demoHistoryRow(thread, index) {
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

  const open = () => {
    location.href = `index.html?open=${index}`;
  };
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

/*
 * The demo's Tasks page.
 *
 * Same page as the application's, with the one difference a static site
 * forces: the application reads its action items from the API and saves a
 * tick to the local database, while this builds the same list from the
 * recorded results.json and keeps ticks in memory only. The page says so
 * rather than pretending otherwise.
 *
 * "Due" is relative to when the results were captured, not to today, because
 * these are recorded threads - so the reference date comes from the capture
 * rather than from the reader's clock.
 */

"use strict";

const state = { tasks: [], owner: "me", showDone: false, referenceDate: new Date() };

/* ------------------------------------------------------------- grouping */

const GROUPS = ["Overdue", "Today", "This week", "Later", "No date"];

function asDate(iso) {
  const parts = String(iso).slice(0, 10).split("-").map(Number);
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function daysUntil(due) {
  return Math.round((asDate(due) - state.referenceDate) / 86400000);
}

function groupFor(due) {
  if (!due) return "No date";
  const days = daysUntil(due);
  if (Number.isNaN(days)) return "No date";
  if (days < 0) return "Overdue";
  if (days === 0) return "Today";
  if (days <= 7) return "This week";
  return "Later";
}

function describeDue(due) {
  if (!due) return "";
  const days = daysUntil(due);
  if (Number.isNaN(days)) return due;
  if (days === 0) return "due that day";
  if (days === 1) return "due the next day";
  if (days === -1) return "1 day late";
  if (days < 0) return `${-days} days late`;
  return `due in ${days} days`;
}

/* ------------------------------------------------------------ rendering */

function visibleTasks() {
  return state.tasks.filter((task) => {
    if (!state.showDone && task.done) return false;
    if (state.owner !== "all" && task.owner !== state.owner) return false;
    return true;
  });
}

function render() {
  const groups = el("tasks-groups");
  const tasks = visibleTasks();

  renderSummary(tasks);
  groups.replaceChildren();

  if (tasks.length === 0) {
    groups.append(emptyState());
    return;
  }

  GROUPS.forEach((name) => {
    const inGroup = tasks.filter((task) => groupFor(task.due) === name);
    if (inGroup.length === 0) return;
    inGroup.sort((a, b) => (a.due || "").localeCompare(b.due || ""));
    groups.append(renderGroup(name, inGroup));
  });
}

function renderSummary(tasks) {
  const summary = el("tasks-summary");
  const overdue = tasks.filter((task) => groupFor(task.due) === "Overdue").length;

  if (tasks.length === 0) {
    summary.textContent = "";
    return;
  }

  const count = `${tasks.length} ${tasks.length === 1 ? "task" : "tasks"}`;
  summary.textContent = overdue > 0 ? `${count} · ${overdue} overdue` : count;
  summary.classList.toggle("has-overdue", overdue > 0);
}

function renderGroup(name, tasks) {
  const section = document.createElement("section");
  section.className = "task-group";

  const heading = document.createElement("h2");
  heading.className = "task-group-head";
  heading.textContent = name;

  const count = document.createElement("span");
  count.className = "task-group-count";
  count.textContent = tasks.length;
  heading.append(count);

  const list = document.createElement("ul");
  list.className = "task-list";
  tasks.forEach((task) => list.append(renderTask(task)));

  section.append(heading, list);
  return section;
}

function renderTask(task) {
  const row = document.createElement("li");
  row.className = task.done ? "task is-done" : "task";

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = task.done;
  checkbox.setAttribute("aria-label", `Mark "${task.task}" as done`);
  checkbox.addEventListener("change", () => {
    task.done = checkbox.checked;
    render();
  });

  const body = document.createElement("div");
  body.className = "task-body";

  const text = document.createElement("p");
  text.className = "task-text";
  text.textContent = task.task;

  const meta = document.createElement("p");
  meta.className = "task-meta";

  if (task.due) {
    const due = document.createElement("span");
    due.className = groupFor(task.due) === "Overdue" ? "task-due is-late" : "task-due";
    due.textContent = describeDue(task.due);
    meta.append(due, document.createTextNode(" · "));
  }

  if (state.owner === "all" && task.owner === "them") {
    const owner = document.createElement("span");
    owner.className = "task-owner";
    owner.textContent = "them";
    meta.append(owner, document.createTextNode(" · "));
  }

  const subject = document.createElement("span");
  subject.className = "task-thread";
  subject.textContent = task.thread_subject;
  meta.append(subject);

  const details = document.createElement("a");
  details.className = "task-details";
  details.href = `index.html?open=${task.threadIndex}`;
  details.textContent = "See details →";

  body.append(text, meta);
  row.append(checkbox, body, details);
  return row;
}

function emptyState() {
  const note = document.createElement("p");
  note.className = "empty";
  note.textContent = emptyMessage();
  return note;
}

/** Say why the list is empty, which is rarely the same reason twice. */
function emptyMessage() {
  const forThisOwner = state.tasks.filter(
    (task) => state.owner === "all" || task.owner === state.owner
  );
  if (forThisOwner.length === 0) {
    return state.owner === "them"
      ? "Nothing is waiting on anyone else in these threads."
      : "Nothing here for this filter.";
  }

  // Everything matching is done, and completed items are hidden.
  return "All done here. Tick “Show completed” to see what you have finished.";
}

/* --------------------------------------------------------------- wiring */

document.querySelectorAll(".segment").forEach((button) => {
  button.addEventListener("click", () => {
    state.owner = button.dataset.owner;
    document
      .querySelectorAll(".segment")
      .forEach((other) => other.classList.toggle("is-active", other === button));
    render();
  });
});

el("show-done").addEventListener("change", (event) => {
  state.showDone = event.target.checked;
  render();
});

loadDemoHistory().then((data) => {
  if (!data) return;

  state.referenceDate = asDate(data.reference_time);
  state.tasks = data.threads.flatMap((thread, threadIndex) =>
    thread.result.summary.action_items.map((item) => ({
      threadIndex,
      task: item.task,
      owner: item.owner,
      due: item.due,
      done: false,
      thread_subject: thread.result.thread_subject,
    }))
  );
  render();
});

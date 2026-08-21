/*
 * The Tasks page: every action item from every stored analysis, in one list.
 *
 * The value of aggregating is answering "what do I owe, and what is late",
 * so tasks are grouped by when they are due rather than by which thread they
 * came from, and the default filter is the items you own.
 *
 * Due dates are compared against the reader's own local date. The pipeline
 * stores them as plain ISO dates with no timezone, so resolving them here -
 * on the machine of the person reading - is what makes "today" mean today.
 */

"use strict";

const state = { tasks: [], owner: "me", showDone: false };

/* ------------------------------------------------------------- grouping */

const GROUPS = ["Overdue", "Today", "This week", "Later", "No date"];

function today() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** Which bucket a task belongs in, from its due date. */
function groupFor(due) {
  if (!due) return "No date";

  const parts = due.split("-").map(Number);
  const dueDate = new Date(parts[0], parts[1] - 1, parts[2]);
  if (Number.isNaN(dueDate.getTime())) return "No date";

  const days = Math.round((dueDate - today()) / 86400000);
  if (days < 0) return "Overdue";
  if (days === 0) return "Today";
  if (days <= 7) return "This week";
  return "Later";
}

/** A short, human reading of a due date: "3 days late", "in 2 days". */
function describeDue(due) {
  if (!due) return "";

  const parts = due.split("-").map(Number);
  const dueDate = new Date(parts[0], parts[1] - 1, parts[2]);
  if (Number.isNaN(dueDate.getTime())) return due;

  const days = Math.round((dueDate - today()) / 86400000);
  if (days === 0) return "due today";
  if (days === 1) return "due tomorrow";
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

    // Within a group, the soonest deadline first; undated items keep the
    // order they were analysed in.
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
  checkbox.addEventListener("change", () => setDone(task, checkbox.checked));

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
  details.href = `/?open=${task.entry_id}`;
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
  if (state.tasks.length === 0) {
    return "No tasks yet. Analyse a thread and any action items the pipeline finds collect here.";
  }

  const forThisOwner = state.tasks.filter(
    (task) => state.owner === "all" || task.owner === state.owner
  );
  if (forThisOwner.length === 0) {
    return state.owner === "them"
      ? "Nothing is waiting on anyone else."
      : "Nothing here for this filter.";
  }

  // Everything matching is done, and completed items are hidden.
  return "All done here. Tick “Show completed” to see what you have finished.";
}

/* -------------------------------------------------------------- actions */

async function setDone(task, done) {
  task.done = done;
  render();

  try {
    await fetch(`/api/tasks/${task.entry_id}/${task.index}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ done }),
    });
  } catch {
    // Put it back rather than showing a tick that was never saved.
    task.done = !done;
    render();
  }
}

async function load() {
  try {
    const response = await fetch("/api/tasks");
    const payload = await response.json();
    state.tasks = payload.tasks;
  } catch {
    state.tasks = [];
  }
  render();
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

loadHealth();
initSidebarHistory({
  onSelect: (id) => {
    location.href = `/?open=${id}`;
  },
  // Deleting an analysis takes its action items with it.
  onDeleted: load,
});
load();

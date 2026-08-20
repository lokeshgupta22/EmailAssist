/*
 * Sidebar behaviour shared by every page that has the sidebar: model health
 * and the history list. What differs between pages is only what happens when
 * a history row is clicked - the workspace reopens it inline, other pages
 * (About) navigate back to the workspace with it preloaded - so that choice
 * is passed in rather than hard-coded here.
 */

"use strict";

async function loadHealth() {
  const box = el("health");
  if (!box) return;
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

/**
 * Wire up the #history-list in the sidebar.
 *
 * onSelect(id) is called when a row is clicked - the workspace reopens the
 * entry inline, other pages navigate to it. onDeleted(id | null) is called
 * after a row (or everything, id === null) is removed, so the caller can
 * react if the entry showing was the one deleted.
 */
function initSidebarHistory({ onSelect, onDeleted } = {}) {
  const list = el("history-list");
  if (!list) return { refresh: async () => {} };

  async function refresh() {
    try {
      const response = await fetch("/api/history");
      const payload = await response.json();
      list.replaceChildren();

      if (payload.entries.length === 0) {
        list.append(emptyNote("Nothing analysed yet."));
        return;
      }

      payload.entries.forEach((entry) => list.append(row(entry)));
    } catch {
      list.replaceChildren(emptyNote("History could not be loaded."));
    }
  }

  function row(entry) {
    const item = document.createElement("li");
    item.className = "history-item";
    item.dataset.id = String(entry.id);
    item.tabIndex = 0;
    item.setAttribute("role", "button");

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
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      await fetch(`/api/history/${entry.id}`, { method: "DELETE" });
      if (onDeleted) onDeleted(entry.id);
      await refresh();
    });

    const open = () => onSelect && onSelect(entry.id);
    item.addEventListener("click", open);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });

    item.append(top, subject, step, remove);
    return item;
  }

  const purge = el("purge-button");
  if (purge) {
    purge.addEventListener("click", async () => {
      if (!window.confirm("Delete every stored analysis? This cannot be undone.")) return;
      await fetch("/api/history", { method: "DELETE" });
      if (onDeleted) onDeleted(null);
      await refresh();
    });
  }

  refresh();
  return { refresh };
}

/** Toggle .is-active on the sidebar history row matching activeId. */
function highlightHistoryItem(activeId) {
  el("history-list")
    ?.querySelectorAll(".history-item")
    .forEach((item) => item.classList.toggle("is-active", item.dataset.id === String(activeId)));
}

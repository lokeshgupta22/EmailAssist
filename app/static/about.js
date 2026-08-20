/*
 * The About page's only behaviour: model health and the history list, both
 * shared with the workspace via sidebar.js. Clicking a history row here
 * navigates to the workspace with that entry preloaded, since the analysis
 * itself is only ever shown there.
 */

"use strict";

loadHealth();
initSidebarHistory({ onSelect: (id) => { location.href = `/?open=${id}`; } });

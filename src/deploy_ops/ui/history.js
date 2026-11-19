// ui/history.js - Browse retained releases without changing decisions, detail selection or form drafts.


"use strict";


let historyQuery = {query: "", environment: "", component: "", outcome: "", limit: 20};
let historyCursor = "", historyPrevious = [], historyPage = null, historyKey = "";
let historyGeneration = 0, historyBusy = false;


// Announce meaningful results, not an unchanged polling response
function historyStatus(value) {
  const status = element("history-status");
  if (status.textContent !== value) status.textContent = value;
}


// Explicit newest/search actions restart only the read-only history extent
function historyReset() {
  historyGeneration += 1; historyBusy = false; historyCursor = ""; historyPrevious = []; historyPage = null;
}


// Every response belongs to the same session, filters, page and creation extent that requested it
async function historyRefresh(explicit = false) {
  if (!token || historyBusy && !explicit) return;
  const generation = ++historyGeneration, session = token, cursor = historyCursor, query = JSON.stringify(historyQuery);
  const current = () => token && token === session && generation === historyGeneration && cursor === historyCursor && query === JSON.stringify(historyQuery);
  const focusedPager = [element("history-newer"), element("history-older")].find(control => control === document.activeElement);
  historyBusy = true; element("history-newer").disabled = true; element("history-older").disabled = true;
  try {
    const parameters = new URLSearchParams({...historyQuery, ...(cursor ? {cursor} : {})});
    const result = await api("/releases/history?" + parameters);
    if (!current()) return;
    historyPage = result;
    const key = JSON.stringify(result.releases);
    if (key !== historyKey) { historyKey = key; releaseRows(result.releases, "No releases match these history filters. Change the filters or start from newest.", element("history-status")); }
    const outcomes = element("history-outcome"), selectedOutcome = outcomes.value;
    if (JSON.stringify([...outcomes.options].map(option => option.value)) !== JSON.stringify(["", ...result.outcomes])) {
      outcomes.replaceChildren(new Option("All outcomes", ""), ...result.outcomes.map(value => new Option(value, value)));
      outcomes.value = selectedOutcome;
    }
    historyStatus("Showing " + result.releases.length + " of " + result.total + " matching recorded releases" +
      (result.has_more ? "; older results available. " : "; no older matches in this view. ") +
      "Recorded outcomes can change. Newest matching releases includes later additions; this is not a health check.");
  } catch (error) {
    if (current()) { historyPage = null; historyStatus("History unavailable: " + error.message + ". Previous results retained; choose Newest matching releases to restart."); }
  } finally {
    if (current()) {
      historyBusy = false;
      element("history-newer").disabled = !historyPage || !historyPrevious.length;
      element("history-older").disabled = !historyPage?.has_more;
      if (focusedPager && document.activeElement === document.body) {
        (focusedPager.disabled ? element("history-newest") : focusedPager).focus({preventScroll: true});
      }
    }
  }
}


// Applying filters never submits a deployment or discards another form's retained values
element("history-filters").onsubmit = event => {
  event.preventDefault();
  historyQuery = Object.fromEntries(["query", "environment", "component", "outcome"].map(key => [key, element("history-" + key).value]));
  historyQuery.limit = Number(element("history-limit").value);
  historyReset(); safe(() => historyRefresh(true));
};
element("history-newest").onclick = () => { historyReset(); safe(() => historyRefresh(true)); };
element("history-older").onclick = () => {
  if (historyBusy || !historyPage?.next_cursor) return;
  historyPrevious.push(historyPage.cursor); historyCursor = historyPage.next_cursor;
  safe(() => historyRefresh(true));
};
element("history-newer").onclick = () => {
  if (historyBusy || !historyPrevious.length) return;
  historyCursor = historyPrevious.pop(); safe(() => historyRefresh(true));
};

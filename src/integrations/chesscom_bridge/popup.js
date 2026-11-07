const defaults = {
  enabled: true,
  modeOverride: "auto",
  endpoint: "http://127.0.0.1:8765/v1/chess/snapshot",
  token: "",
  lastStatus: "Waiting for Chess.com board",
  lastError: "",
  lastStatusAt: 0,
  lastSnapshotAt: 0,
  lastBoardFen: "",
  lastGameId: "",
  lastModeHint: ""
};
let initialized = false;
function render(values) {
  if (!initialized) {
    document.querySelector("#enabled").checked = values.enabled;
    document.querySelector("#mode").value = values.modeOverride;
    document.querySelector("#endpoint").value = values.endpoint;
    document.querySelector("#token").value = values.token;
    initialized = true;
  }
  const status = document.querySelector("#lastStatus");
  const error = document.querySelector("#lastError");
  const fen = document.querySelector("#lastFen");
  const ageSeconds = values.lastStatusAt ? Math.max(0, Math.round((Date.now() - values.lastStatusAt) / 1000)) : null;
  const age = ageSeconds === null ? "" : ` (${ageSeconds}s ago)`;
  const bits = [values.lastStatus || defaults.lastStatus];
  if (values.lastGameId) bits.push(`game=${values.lastGameId}`);
  if (values.lastModeHint) bits.push(`mode=${values.lastModeHint}`);
  status.textContent = bits.join(" | ") + age;
  error.textContent = values.lastError ? `Error: ${values.lastError}` : "";
  fen.textContent = values.lastBoardFen ? `FEN: ${values.lastBoardFen}` : "";
}
function refresh() {
  chrome.storage.local.get(defaults, render);
}
refresh();
setInterval(refresh, 500);
document.querySelector("#save").addEventListener("click", () => {
  chrome.storage.local.set({
    enabled: document.querySelector("#enabled").checked,
    modeOverride: document.querySelector("#mode").value,
    endpoint: document.querySelector("#endpoint").value.trim(),
    token: document.querySelector("#token").value.trim()
  }, () => {
    const status = document.querySelector("#status");
    status.textContent = "Đã lưu.";
    setTimeout(() => status.textContent = "", 1200);
  });
});

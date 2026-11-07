"use strict";

const DEFAULTS = {
  endpoint: "http://127.0.0.1:8765/v1/chess/snapshot",
  token: "",
  enabled: true,
  modeOverride: "auto",
  lastStatus: "Waiting for Chess.com board",
  lastError: "",
  lastSnapshotAt: 0,
  lastBoardFen: ""
};

function settings() {
  return new Promise(resolve => chrome.storage.local.get(DEFAULTS, resolve));
}

function remember(update) {
  chrome.storage.local.set(Object.assign({lastStatusAt: Date.now()}, update));
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "eris_chess_snapshot") return false;
  (async () => {
    const config = await settings();
    if (!config.enabled) {
      remember({lastStatus: "Bridge disabled", lastError: ""});
      return {ok: false, error: "disabled"};
    }
    const headers = {"Content-Type": "application/json"};
    if (config.token) headers["X-Eris-Bridge-Token"] = config.token;
    const response = await fetch(config.endpoint || DEFAULTS.endpoint, {
      method: "POST",
      headers,
      body: JSON.stringify(message.payload || {}),
      cache: "no-store"
    });
    const body = await response.json().catch(() => ({}));
    const payload = message.payload || {};
    if (!response.ok) {
      remember({
        lastStatus: "ERIS rejected snapshot",
        lastError: body.error || `HTTP ${response.status}`,
        lastBoardFen: payload.board_fen || ""
      });
      return {ok: false, error: body.error || `HTTP ${response.status}`};
    }
    remember({
      lastStatus: "Connected: snapshot sent",
      lastError: "",
      lastSnapshotAt: Date.now(),
      lastBoardFen: payload.board_fen || "",
      lastGameId: payload.game_id || "",
      lastModeHint: payload.mode_hint || "",
      lastPageUrl: payload.page_url || ""
    });
    return {ok: true, observation: body.observation || {}};
  })().then(sendResponse).catch(error => {
    remember({lastStatus: "Cannot reach ERIS endpoint", lastError: String(error)});
    sendResponse({ok: false, error: String(error)});
  });
  return true;
});

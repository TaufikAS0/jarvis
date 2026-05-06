/**
 * JARVIS - Main entry point.
 *
 * Wires together the orb visualization, WebSocket communication,
 * speech recognition, and audio playback into a single experience.
 */

import { createOrb, type OrbState } from "./orb";
import { createVoiceInput, createLocalVoiceInput, createAudioPlayer, isNetworkOnline } from "./voice";
import { createSocket } from "./ws";
import {
  openSettings,
  checkFirstTimeSetup,
  getStoredChatBoxEnabled,
  getStoredSpeechRecognitionLanguage,
  getStoredPersonaMode,
  syncLanguagePreferenceFromServer,
  type PersonaMode,
} from "./settings";
import "./style.css";

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

type State = "idle" | "listening" | "thinking" | "speaking" | "uncertain";
let currentState: State = "idle";
let isMuted = false;

// Track the most recent command and response for training feedback
let _pendingFeedbackCmd = "";
let _pendingFeedbackAction = "";   // set when server dispatches an action
let _pendingFeedbackResponse = ""; // spoken/text reply from JARVIS
let _pendingFeedbackEligible = false;
let _feedbackQueuedForCurrentCmd = false;
let _hadSpeakingState = false;
let isSocketConnected = false;
let holdsPrimaryClientLock = false;

const statusEl = document.getElementById("status-text")!;
const errorEl = document.getElementById("error-text")!;

function showError(msg: string) {
  errorEl.textContent = msg;
  errorEl.style.opacity = "1";
  setTimeout(() => {
    errorEl.style.opacity = "0";
  }, 5000);
}

function clearError() {
  errorEl.textContent = "";
  errorEl.style.opacity = "0";
}

function looksLikeActionableCommand(text: string): boolean {
  const normalized = text.toLowerCase().trim();
  if (!normalized) return false;

  const imperativePrefixes =
    /^(?:open|buka|play|putar|nyalakan|hidupkan|matikan|turn|switch|set|atur|ubah|ganti|scan|cek|discover|show|pause|resume|next|previous|create|buat(?:kan)?|ask|read|warnai(?:n)?|warnain|jadiin|bikin|kasih|aktifkan|pakai|gelapin|redupin|terangin|matiin|nyalain)\b/;
  const actionableKeywords =
    /\b(?:lampu|lights?|wiz|spotify|folder|codex|obsidian|chrome|terminal|controller|brightness|kecerahan|warna(?:in)?|color|colour|scene|mode|briefing|calendar|mail|email|music|lagu)\b/;

  return imperativePrefixes.test(normalized) || actionableKeywords.test(normalized);
}

function updateStatus(state: State) {
  const labels: Record<State, string> = {
    idle: "",
    listening: "listening...",
    thinking: "thinking...",
    speaking: "",
    uncertain: "clarify that for me...",
  };
  statusEl.textContent = labels[state];
}

// ---------------------------------------------------------------------------
// Single-instance guard
// ---------------------------------------------------------------------------

const PRIMARY_CLIENT_LOCK_KEY = "jarvis-primary-client-lock";
const PRIMARY_CLIENT_LOCK_TTL_MS = 10000;
const PRIMARY_CLIENT_HEARTBEAT_MS = 3000;
const primaryClientId =
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `jarvis-${Date.now()}-${Math.random().toString(36).slice(2)}`;

function readPrimaryClientLock(): { id: string; ts: number } | null {
  try {
    const raw = window.localStorage.getItem(PRIMARY_CLIENT_LOCK_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { id?: string; ts?: number };
    if (!parsed.id || typeof parsed.ts !== "number") return null;
    return { id: parsed.id, ts: parsed.ts };
  } catch {
    return null;
  }
}

function hasFreshForeignClientLock(lock = readPrimaryClientLock()): boolean {
  return !!lock && lock.id !== primaryClientId && Date.now() - lock.ts < PRIMARY_CLIENT_LOCK_TTL_MS;
}

function writePrimaryClientLock() {
  window.localStorage.setItem(
    PRIMARY_CLIENT_LOCK_KEY,
    JSON.stringify({ id: primaryClientId, ts: Date.now() })
  );
}

function releasePrimaryClientLock() {
  const lock = readPrimaryClientLock();
  if (lock?.id === primaryClientId) {
    window.localStorage.removeItem(PRIMARY_CLIENT_LOCK_KEY);
  }
}

function claimPrimaryClientLock(): boolean {
  if (hasFreshForeignClientLock()) return false;
  holdsPrimaryClientLock = true;
  writePrimaryClientLock();
  return true;
}

// ---------------------------------------------------------------------------
// Init components
// ---------------------------------------------------------------------------

const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement;
const orb = createOrb(canvas);

// Apply stored persona on startup
const _initPersona = getStoredPersonaMode();
orb.setPersonaMode(_initPersona);
document.body.setAttribute("data-persona-mode", _initPersona);
document.body.setAttribute("data-ultron-mode", _initPersona === "ultron" ? "true" : "false");

// Live-update orb + body attributes when user switches persona in Settings
window.addEventListener("jarvis:persona-mode-changed", (event) => {
  const detail = (event as CustomEvent<{ mode: PersonaMode }>).detail;
  orb.setPersonaMode(detail.mode);
  document.body.setAttribute("data-persona-mode", detail.mode);
  document.body.setAttribute("data-ultron-mode", detail.mode === "ultron" ? "true" : "false");
});

const isPrimaryClient = claimPrimaryClientLock();

const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
const WS_URL = `${wsProto}//${window.location.host}/ws/voice`;
const socket = isPrimaryClient ? createSocket(WS_URL) : null;

const audioPlayer = createAudioPlayer();
orb.setAnalyser(audioPlayer.getAnalyser());

function transition(newState: State) {
  if (newState === currentState) return;
  currentState = newState;
  document.body.dataset.jarvisState = newState;
  orb.setState(newState as OrbState);
  updateStatus(newState);

  switch (newState) {
    case "idle":
      if (!isMuted) voiceInput.resume();
      break;
    case "listening":
      if (!isMuted) voiceInput.resume();
      break;
    case "thinking":
      voiceInput.pause();
      break;
    case "speaking":
    case "uncertain":
      voiceInput.pause();
      break;
  }
}

// ---------------------------------------------------------------------------
// Text chat box
// ---------------------------------------------------------------------------

let chatBoxEnabled = getStoredChatBoxEnabled();
let chatBoxCollapsed = false;

const chatBoxEl = document.createElement("section");
chatBoxEl.id = "chat-box";
chatBoxEl.setAttribute("aria-live", "polite");
chatBoxEl.innerHTML = `
  <div id="chat-box-header">
    <div>
      <span id="chat-box-kicker">text input</span>
      <strong>JARVIS Chat</strong>
    </div>
    <div id="chat-box-actions">
      <button type="button" id="btn-chat-minimize" title="Minimize chat">_</button>
    </div>
  </div>
  <div id="chat-messages" aria-label="JARVIS chat history"></div>
  <form id="chat-form" autocomplete="off">
    <input id="chat-input" type="text" placeholder="Type a command instead of speaking..." autocomplete="off" spellcheck="false" />
    <button id="btn-chat-send" type="submit">Send</button>
  </form>
`;
document.body.appendChild(chatBoxEl);

const chatMessagesEl = document.getElementById("chat-messages")!;
const chatFormEl = document.getElementById("chat-form") as HTMLFormElement;
const chatInputEl = document.getElementById("chat-input") as HTMLInputElement;
const chatMinimizeBtn = document.getElementById("btn-chat-minimize") as HTMLButtonElement;

function applyChatBoxVisibility() {
  const visible = chatBoxEnabled && !chatBoxCollapsed;
  chatBoxEl.classList.toggle("collapsed", chatBoxEnabled && chatBoxCollapsed);
  chatBoxEl.classList.toggle("visible", visible);
  chatBoxEl.setAttribute("aria-hidden", chatBoxEnabled ? "false" : "true");
  document.body.dataset.chatBoxEnabled = chatBoxEnabled ? "true" : "false";
}

function appendChatMessage(role: "user" | "assistant" | "system", text: string) {
  const cleanText = text.trim();
  if (!cleanText || !chatBoxEnabled) return;

  const row = document.createElement("div");
  row.className = `chat-message ${role}`;

  const label = document.createElement("span");
  label.className = "chat-message-label";
  label.textContent = role === "user" ? "you" : role === "assistant" ? "jarvis" : "system";

  const body = document.createElement("div");
  body.className = "chat-message-body";
  body.textContent = cleanText;

  row.append(label, body);
  chatMessagesEl.appendChild(row);

  while (chatMessagesEl.children.length > 40) {
    chatMessagesEl.removeChild(chatMessagesEl.firstElementChild!);
  }

  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

function setChatInputEnabled(enabled: boolean) {
  chatInputEl.disabled = !enabled;
  const sendBtn = document.getElementById("btn-chat-send") as HTMLButtonElement | null;
  if (sendBtn) sendBtn.disabled = !enabled;
}

function submitTranscript(text: string, source: "voice" | "chat" = "voice"): boolean {
  const cleanText = text.trim();
  if (!cleanText) return false;
  if (!holdsPrimaryClientLock) {
    showError("JARVIS is already active in another window.");
    return false;
  }
  if (!isSocketConnected) {
    showError("JARVIS is reconnecting. Please wait a moment.");
    appendChatMessage("system", "JARVIS is reconnecting. Please wait a moment.");
    return false;
  }

  audioPlayer.stop();
  if (source === "chat" || chatBoxEnabled) {
    appendChatMessage("user", cleanText);
  }
  socket?.send({ type: "transcript", text: cleanText, isFinal: true });
  _pendingFeedbackCmd = cleanText;
  _pendingFeedbackAction = "";
  _pendingFeedbackResponse = "";
  _pendingFeedbackEligible = looksLikeActionableCommand(cleanText);
  _feedbackQueuedForCurrentCmd = false;
  _hadSpeakingState = false;
  transition("thinking");
  return true;
}

chatFormEl.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = chatInputEl.value.trim();
  if (submitTranscript(text, "chat")) {
    chatInputEl.value = "";
  }
});

chatMinimizeBtn.addEventListener("click", () => {
  chatBoxCollapsed = !chatBoxCollapsed;
  chatMinimizeBtn.textContent = chatBoxCollapsed ? "chat" : "_";
  chatMinimizeBtn.title = chatBoxCollapsed ? "Restore chat" : "Minimize chat";
  applyChatBoxVisibility();
});

window.addEventListener("jarvis:chat-box-preference-changed", (event) => {
  const detail = (event as CustomEvent<{ enabled?: boolean }>).detail;
  chatBoxEnabled = !!detail?.enabled;
  if (chatBoxEnabled) chatBoxCollapsed = false;
  applyChatBoxVisibility();
});

applyChatBoxVisibility();
setChatInputEnabled(isSocketConnected && holdsPrimaryClientLock);

// ---------------------------------------------------------------------------
// Voice input
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Voice mode toggle: "online" = Web Speech API, "offline" = local Whisper
// ---------------------------------------------------------------------------

let _voiceMode: "online" | "offline" = "online";

function _makeTranscriptHandler() {
  return (text: string) => {
    submitTranscript(text, "voice");
  };
}

const _onlineVoice = createVoiceInput(
  _makeTranscriptHandler(),
  (msg) => showError(msg),
  getStoredSpeechRecognitionLanguage()
);

const _offlineVoice = createLocalVoiceInput(
  _makeTranscriptHandler(),
  (msg) => showError(msg),
  getStoredSpeechRecognitionLanguage()
);

// Active voice input — starts as online (Web Speech API)
let voiceInput = _onlineVoice;

function setVoiceMode(mode: "online" | "offline") {
  _onlineVoice.stop();
  _offlineVoice.stop();
  _voiceMode = mode;
  voiceInput = mode === "offline" ? _offlineVoice : _onlineVoice;
  const btn = document.getElementById("btn-voice-mode");
  if (btn) {
    btn.textContent = mode === "offline" ? "🔇 Offline" : "🌐 Online";
    btn.title = mode === "offline"
      ? "Voice mode: Offline (Whisper local). Click to switch to Online"
      : "Voice mode: Online (Web Speech API). Click to switch to Offline";
  }
  if (currentState === "idle" || currentState === "listening") {
    if (!isMuted) voiceInput.start();
  }
  showError(mode === "offline" ? "🔇 Offline voice (Whisper). WiFi tidak dibutuhkan." : "🌐 Online voice (Web Speech API).");
}

document.getElementById("btn-voice-mode")?.addEventListener("click", () => {
  setVoiceMode(_voiceMode === "online" ? "offline" : "online");
});

window.addEventListener("jarvis:language-changed", (event) => {
  const detail = (event as CustomEvent<{ speechRecognitionLanguage?: string }>).detail;
  if (detail?.speechRecognitionLanguage) {
    _onlineVoice.setLanguage(detail.speechRecognitionLanguage);
    _offlineVoice.setLanguage(detail.speechRecognitionLanguage);
  }
});

window.addEventListener("offline", () => {
  if (_voiceMode === "online") {
    showError("⚠️ Internet putus. Klik 🌐 Online untuk ganti ke Offline (Whisper).");
  }
});

window.addEventListener("online", () => {
  showError("✓ Internet tersambung kembali.");
});

syncLanguagePreferenceFromServer();

if (socket) {
  socket.onConnectionChange((connected) => {
    isSocketConnected = connected;
    setChatInputEnabled(connected && holdsPrimaryClientLock);

    if (!connected) {
      audioPlayer.stop();
      transition("idle");
      statusEl.textContent = "reconnecting...";
      showError("Connection to JARVIS lost. Reconnecting...");
      return;
    }

    clearError();
    if (isMuted) {
      transition("idle");
    } else {
      transition("listening");
    }
  });
}

function queueTrainingFeedback(delayMs = 600) {
  const feedbackText = (_pendingFeedbackAction || _pendingFeedbackResponse).trim();
  if (!trainingMode || !isPrimaryClient || _feedbackQueuedForCurrentCmd) return;
  if (!_pendingFeedbackCmd || !feedbackText) return;
  if (!_pendingFeedbackAction && !_pendingFeedbackEligible) return;

  _feedbackQueuedForCurrentCmd = true;
  setTimeout(() => showFeedbackBox(_pendingFeedbackCmd, feedbackText), delayMs);
}

// ---------------------------------------------------------------------------
// Audio playback finished
// ---------------------------------------------------------------------------

audioPlayer.onFinished(() => {
  transition("idle");
  if (_hadSpeakingState) {
    queueTrainingFeedback();
    _hadSpeakingState = false;
  }
});

// ---------------------------------------------------------------------------
// WebSocket messages
// ---------------------------------------------------------------------------

if (socket) {
  socket.onMessage((msg) => {
    const type = msg.type as string;

    if (type === "audio") {
      const audioData = msg.data as string;
      console.log("[audio] received", audioData ? `${audioData.length} chars` : "EMPTY", "state:", currentState);
      if (audioData) {
        if (currentState !== "speaking" && currentState !== "uncertain") {
          transition("speaking");
        }
        audioPlayer.enqueue(audioData);
        _hadSpeakingState = true;
      } else {
        console.warn("[audio] no data received, returning to idle");
        transition("idle");
      }
      if (msg.text) {
        _pendingFeedbackResponse = String(msg.text);
        appendChatMessage("assistant", String(msg.text));
        console.log("[JARVIS]", msg.text);
      }
    } else if (type === "status") {
      const state = msg.state as string;
      if (state === "thinking" && currentState !== "thinking") {
        transition("thinking");
      } else if (state === "uncertain") {
        _pendingFeedbackEligible = true;
        transition("uncertain");
      } else if (state === "working") {
        transition("thinking");
        statusEl.textContent = "working...";
      } else if (state === "idle") {
        transition("idle");
      }
    } else if (type === "action_taken") {
      // Server just executed an action — store it for feedback box
      _pendingFeedbackAction = (msg.description as string) || "";
    } else if (type === "text") {
      if (msg.text) {
        _pendingFeedbackResponse = String(msg.text);
        appendChatMessage("assistant", String(msg.text));
      }
      console.log("[JARVIS]", msg.text);
      if (_pendingFeedbackEligible && !_hadSpeakingState) {
        queueTrainingFeedback(250);
      }
    } else if (type === "task_spawned") {
      console.log("[task]", "spawned:", msg.task_id, msg.prompt);
    } else if (type === "task_complete") {
      console.log("[task]", "complete:", msg.task_id, msg.status, msg.summary);
    }
  });
}

// ---------------------------------------------------------------------------
// Kick off
// ---------------------------------------------------------------------------

setTimeout(() => {
  if (!isPrimaryClient) {
    document.body.dataset.jarvisState = "idle";
    statusEl.textContent = "already open in another window";
    showError("JARVIS is already active in another window. Close the older window or wait a moment.");
    return;
  }
  voiceInput.start();
  transition("listening");
}, 1000);

function ensureAudioContext() {
  const ctx = audioPlayer.getAnalyser().context as AudioContext;
  if (ctx.state === "suspended") {
    ctx.resume().then(() => console.log("[audio] context resumed"));
  }
}
document.addEventListener("click", ensureAudioContext);
document.addEventListener("touchstart", ensureAudioContext);
document.addEventListener("keydown", ensureAudioContext, { once: true });
ensureAudioContext();
document.body.dataset.jarvisState = currentState;

// ---------------------------------------------------------------------------
// UI Controls
// ---------------------------------------------------------------------------

const btnMute = document.getElementById("btn-mute")!;
const btnMenu = document.getElementById("btn-menu")!;
const menuDropdown = document.getElementById("menu-dropdown")!;
const btnRestart = document.getElementById("btn-restart")!;
const btnFixSelf = document.getElementById("btn-fix-self")!;
const btnSettings = document.getElementById("btn-settings")!;

function setInteractiveControlsEnabled(enabled: boolean) {
  for (const element of [btnMute, btnMenu, btnRestart, btnFixSelf, btnSettings]) {
    element.toggleAttribute("disabled", !enabled);
  }
}

if (!isPrimaryClient) {
  setInteractiveControlsEnabled(false);
  setChatInputEnabled(false);
  isMuted = true;
}

btnMute.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!holdsPrimaryClientLock) return;

  isMuted = !isMuted;
  btnMute.classList.toggle("muted", isMuted);
  if (isMuted) {
    voiceInput.pause();
    transition("idle");
  } else {
    voiceInput.resume();
    transition("listening");
  }
});

btnMenu.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!holdsPrimaryClientLock) return;
  menuDropdown.style.display = menuDropdown.style.display === "none" ? "block" : "none";
});

document.addEventListener("click", () => {
  menuDropdown.style.display = "none";
});

btnRestart.addEventListener("click", async (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  statusEl.textContent = "restarting...";
  try {
    await fetch("/api/restart", { method: "POST" });
    setTimeout(() => window.location.reload(), 4000);
  } catch {
    statusEl.textContent = "restart failed";
  }
});

btnFixSelf.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  socket?.send({ type: "fix_self" });
  statusEl.textContent = "entering work mode...";
});

btnSettings.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  openSettings();
});

setTimeout(() => {
  checkFirstTimeSetup();
}, 2000);

window.addEventListener("beforeunload", () => {
  holdsPrimaryClientLock = false;
  releasePrimaryClientLock();
  socket?.close();
});

// ---------------------------------------------------------------------------
// Training Mode Toggle
// ---------------------------------------------------------------------------

const trainingToggle = document.getElementById("training-toggle")!;
const TRAINING_KEY   = "jarvis-training-mode";

let trainingMode = window.localStorage.getItem(TRAINING_KEY) === "1";

function applyTrainingState() {
  trainingToggle.classList.toggle("active", trainingMode);
}
applyTrainingState();

trainingToggle.addEventListener("click", () => {
  if (!isPrimaryClient) return;
  trainingMode = !trainingMode;
  window.localStorage.setItem(TRAINING_KEY, trainingMode ? "1" : "0");
  applyTrainingState();
  // If turning off while box is visible, hide it (hideFeedbackBox defined below)
  if (!trainingMode && feedbackShown) hideFeedbackBox();  // eslint-disable-line @typescript-eslint/no-use-before-define
});

// ---------------------------------------------------------------------------
// Feedback Confirmation Box
// ---------------------------------------------------------------------------

const feedbackBox        = document.getElementById("feedback-box")!;
const feedbackCmdText    = document.getElementById("feedback-cmd-text")!;
const feedbackResText    = document.getElementById("feedback-res-text")!;
const feedbackActions    = document.getElementById("feedback-actions")!;
const feedbackCorrForm   = document.getElementById("feedback-correction-form")!;
const feedbackCorrInput  = document.getElementById("feedback-correction-input") as HTMLInputElement;
const feedbackProgressFill = document.getElementById("feedback-progress-fill")!;

const btnFeedbackYes     = document.getElementById("btn-feedback-yes")!;
const btnFeedbackNo      = document.getElementById("btn-feedback-no")!;
const btnFeedbackDismiss = document.getElementById("btn-feedback-dismiss")!;
const btnFeedbackCancel  = document.getElementById("btn-feedback-cancel")!;
const btnFeedbackSend    = document.getElementById("btn-feedback-send")!;

const FEEDBACK_TIMEOUT_MS = 9000;

let lastCapturedCmd = "";
let lastCapturedRes = "";
let feedbackDismissTimer: ReturnType<typeof setTimeout> | null = null;
let feedbackShown = false;

function showFeedbackBox(cmd: string, action: string) {
  if (!isPrimaryClient || !cmd || !action || !trainingMode) return;

  lastCapturedCmd = cmd;
  lastCapturedRes = action;

  feedbackCmdText.textContent = cmd;
  feedbackResText.textContent = action;

  // Reset to default state
  feedbackActions.style.display = "flex";
  feedbackCorrForm.style.display = "none";

  // Show box
  feedbackBox.style.display = "block";
  requestAnimationFrame(() => {
    feedbackBox.classList.add("visible");
  });
  feedbackShown = true;

  // Start countdown bar
  feedbackProgressFill.style.transition = "none";
  feedbackProgressFill.style.transform = "scaleX(1)";
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      feedbackProgressFill.style.transition = `transform ${FEEDBACK_TIMEOUT_MS}ms linear`;
      feedbackProgressFill.style.transform = "scaleX(0)";
    });
  });

  // Auto-dismiss
  if (feedbackDismissTimer) clearTimeout(feedbackDismissTimer);
  feedbackDismissTimer = setTimeout(hideFeedbackBox, FEEDBACK_TIMEOUT_MS);
}

function hideFeedbackBox() {
  if (feedbackDismissTimer) { clearTimeout(feedbackDismissTimer); feedbackDismissTimer = null; }
  feedbackBox.classList.remove("visible");
  setTimeout(() => {
    if (!feedbackBox.classList.contains("visible")) {
      feedbackBox.style.display = "none";
      feedbackShown = false;
    }
  }, 280);
}

btnFeedbackDismiss.addEventListener("click", hideFeedbackBox);

btnFeedbackYes.addEventListener("click", () => {
  socket?.send({
    type: "feedback_confirm",
    command: lastCapturedCmd,
    response: lastCapturedRes,
  });
  // Brief flash green to confirm
  btnFeedbackYes.style.background = "rgba(34,197,94,0.28)";
  setTimeout(hideFeedbackBox, 400);
});

btnFeedbackNo.addEventListener("click", () => {
  // Reveal correction input
  feedbackActions.style.display = "none";
  feedbackCorrForm.style.display = "flex";
  feedbackCorrInput.value = "";
  feedbackCorrInput.focus();

  // Extend timer while user is typing
  if (feedbackDismissTimer) { clearTimeout(feedbackDismissTimer); feedbackDismissTimer = null; }
  feedbackProgressFill.style.transition = "none";
  feedbackProgressFill.style.transform = "scaleX(0.3)";
});

btnFeedbackCancel.addEventListener("click", hideFeedbackBox);

function sendCorrection() {
  const correction = feedbackCorrInput.value.trim();
  if (!correction) return;
  socket?.send({
    type: "feedback_correction",
    command: lastCapturedCmd,
    response: lastCapturedRes,
    correction,
  });
  hideFeedbackBox();
}

btnFeedbackSend.addEventListener("click", sendCorrection);
feedbackCorrInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendCorrection();
  if (e.key === "Escape") hideFeedbackBox();
});

window.addEventListener("pagehide", () => {
  holdsPrimaryClientLock = false;
  releasePrimaryClientLock();
});

window.addEventListener("storage", (event) => {
  if (event.key !== PRIMARY_CLIENT_LOCK_KEY || !holdsPrimaryClientLock) {
    return;
  }

  if (hasFreshForeignClientLock()) {
    holdsPrimaryClientLock = false;
    isMuted = true;
    voiceInput.pause();
    audioPlayer.stop();
    socket?.close();
    setInteractiveControlsEnabled(false);
    setChatInputEnabled(false);
    transition("idle");
    statusEl.textContent = "switched to another JARVIS window";
    showError("This window released JARVIS control because another JARVIS window became active.");
  }
});

window.setInterval(() => {
  if (holdsPrimaryClientLock) {
    writePrimaryClientLock();
    return;
  }

  // Secondary tab: if the primary lock has expired, try to claim it instead of
  // immediately reloading — this prevents a reload cascade when the server
  // briefly disconnects and the primary tab's heartbeat pauses.
  if (!hasFreshForeignClientLock()) {
    const claimed = claimPrimaryClientLock();
    if (claimed) {
      // Became the new primary — reconnect WebSocket without a full page reload
      holdsPrimaryClientLock = true;
      if (!socket) {
        window.location.reload(); // socket was never created; reload to init it
      }
    }
  }
}, PRIMARY_CLIENT_HEARTBEAT_MS);

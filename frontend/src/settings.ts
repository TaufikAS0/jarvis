/**
 * JARVIS — Settings Panel
 *
 * Overlay panel for API keys, connection status, preferences, and system info.
 * Slides in from the right with glass-morphism styling.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StatusResponse {
  claude_code_installed: boolean;
  calendar_accessible: boolean;
  mail_accessible: boolean;
  notes_accessible: boolean;
  memory_count: number;
  task_count: number;
  server_port: number;
  uptime_seconds: number;
  env_keys_set: {
    anthropic: boolean;
    nvidia?: boolean;
    fish_audio: boolean;
    fish_voice_id: boolean;
    user_name: string;
    user_location: string;
    user_country: string;
    user_language: string;
    local_fallback_mode?: boolean;
    chat_box_enabled?: boolean;
    llm_provider?: string;
    tts_provider?: string;
  };
  llm_status?: {
    provider?: string;
    available: boolean;
    reason?: string;
    last_error?: string;
    fallback_mode?: boolean;
    local_base_url?: string;
    local_model?: string;
    local_server_reachable?: boolean;
    local_models?: string[];
    nvidia_base_url?: string;
    nvidia_model?: string;
    nvidia_key_configured?: boolean;
  };
  tts_status?: {
    provider?: string;
    local_engine?: string;
    local_base_url?: string;
    local_resolved_base_url?: string;
    local_server_reachable?: boolean;
    local_engine_match?: boolean;
    local_last_error?: string;
    local_model_path?: string;
    local_voice?: string;
    local_voice_id?: string;
    local_speed?: string | number;
  };
}

interface PreferencesResponse {
  user_name: string;
  honorific: string;
  calendar_accounts: string;
  user_location: string;
  user_country: string;
  user_language: string;
  local_fallback_mode?: boolean;
  chat_box_enabled?: boolean;
  llm_provider?: string;
  local_llm_base_url?: string;
  local_llm_model?: string;
  nvidia_llm_base_url?: string;
  nvidia_llm_model?: string;
  tts_provider?: string;
  local_tts_engine?: string;
  local_tts_url?: string;
  local_tts_model_path?: string;
  local_tts_voice?: string;
  local_tts_voice_id?: string;
  local_tts_speed?: string | number;
  fish_voice_id?: string;
  fish_voice_id_id?: string;
}

interface VoicePreset {
  id: string;
  label: string;
  note?: string;
}

export type UserLanguage = "en" | "id";
type LLMProvider = "anthropic" | "local" | "nvidia";
type TTSProvider = "fish" | "local";
type LocalTTSEngine = "kokoro" | "voxcpm";

const DEFAULT_NVIDIA_LLM_BASE_URL = "https://integrate.api.nvidia.com/v1";
const DEFAULT_NVIDIA_LLM_MODEL = "deepseek-ai/deepseek-v4-pro";

const LOCAL_TTS_ENGINE_DEFAULT_URLS: Record<LocalTTSEngine, string> = {
  kokoro: "http://127.0.0.1:8881",
  voxcpm: "http://127.0.0.1:8882",
};

const USER_LANGUAGE_STORAGE_KEY = "jarvis-user-language";
const ULTRON_MODE_STORAGE_KEY   = "jarvis-ultron-mode";
const PERSONA_MODE_STORAGE_KEY  = "jarvis-persona-mode";
const CHAT_BOX_STORAGE_KEY      = "jarvis-chat-box-enabled";

export type PersonaMode = "jarvis" | "ultron" | "scifi";

function normalizePersonaMode(value?: string | null): PersonaMode {
  const v = (value || "").toLowerCase();
  if (v === "ultron") return "ultron";
  if (v === "scifi" || v === "sci-fi") return "scifi";
  return "jarvis";
}

export function getStoredPersonaMode(): PersonaMode {
  const explicit = window.localStorage.getItem(PERSONA_MODE_STORAGE_KEY);
  if (explicit) return normalizePersonaMode(explicit);
  // Legacy fallback: older clients only stored the Ultron toggle.
  const legacy = window.localStorage.getItem(ULTRON_MODE_STORAGE_KEY) === "true";
  return legacy ? "ultron" : "jarvis";
}

/** Legacy helper — returns true only for the Ultron persona. */
export function getStoredUltronMode(): boolean {
  return getStoredPersonaMode() === "ultron";
}

function setStoredPersonaMode(mode: PersonaMode) {
  const normalized = normalizePersonaMode(mode);
  window.localStorage.setItem(PERSONA_MODE_STORAGE_KEY, normalized);
  // Keep the legacy key in sync so any code still reading it stays correct.
  window.localStorage.setItem(ULTRON_MODE_STORAGE_KEY, normalized === "ultron" ? "true" : "false");
  window.dispatchEvent(new CustomEvent("jarvis:persona-mode-changed", { detail: { mode: normalized } }));
  window.dispatchEvent(new CustomEvent("jarvis:ultron-mode-changed", { detail: { enabled: normalized === "ultron" } }));
}

const FISH_VOICE_PRESETS: Record<UserLanguage, VoicePreset[]> = {
  en: [
    { id: "612b878b113047d9a770c069c8b4fdfe", label: "JARVIS (MCU)" },
    { id: "d7a76ce437d34163a48b7e683f85cac7", label: "Tony Stark / Iron Man" },
    { id: "813bb4d944924055867d719bf254dc93", label: "JARVIS V2" },
  ],
  id: [
    { id: "9edf80f1aa8743608817c0d8f415f974", label: "Jokowi" },
    { id: "b8d594e696694b499aa12e32d0c1b618", label: "Prabowo" },
    { id: "b7e2931933b446a5a4e882ead256b37c", label: "Ahok" },
    { id: "f3723ed9190548a49e891bc86d7c53de", label: "Anies" },
  ],
};

export function normalizeUserLanguage(value?: string): UserLanguage {
  return (value || "en").toLowerCase().startsWith("id") ? "id" : "en";
}

export function mapUserLanguageToSpeechRecognitionLanguage(userLanguage?: string): string {
  return normalizeUserLanguage(userLanguage) === "id" ? "id-ID" : "en-US";
}

function dispatchLanguageChange(userLanguage: string) {
  const normalized = normalizeUserLanguage(userLanguage);
  window.dispatchEvent(
    new CustomEvent("jarvis:language-changed", {
      detail: {
        userLanguage: normalized,
        speechRecognitionLanguage: mapUserLanguageToSpeechRecognitionLanguage(normalized),
      },
    })
  );
}

function persistUserLanguage(userLanguage: string, announce = true) {
  const normalized = normalizeUserLanguage(userLanguage);
  window.localStorage.setItem(USER_LANGUAGE_STORAGE_KEY, normalized);
  if (announce) {
    dispatchLanguageChange(normalized);
  }
}

function normalizeChatBoxPreference(value?: boolean | string | null): boolean {
  if (typeof value === "boolean") return value;
  return ["1", "true", "yes", "on"].includes((value || "").toString().trim().toLowerCase());
}

function persistChatBoxPreference(enabled: boolean, announce = true) {
  window.localStorage.setItem(CHAT_BOX_STORAGE_KEY, enabled ? "1" : "0");
  if (announce) {
    window.dispatchEvent(new CustomEvent("jarvis:chat-box-preference-changed", { detail: { enabled } }));
  }
}

export function getStoredChatBoxEnabled(): boolean {
  return window.localStorage.getItem(CHAT_BOX_STORAGE_KEY) === "1";
}

export function getStoredUserLanguage(): UserLanguage {
  return normalizeUserLanguage(window.localStorage.getItem(USER_LANGUAGE_STORAGE_KEY) || "en");
}

export function getStoredSpeechRecognitionLanguage(): string {
  return mapUserLanguageToSpeechRecognitionLanguage(getStoredUserLanguage());
}

function normalizeLlmProvider(value?: string): LLMProvider {
  const normalized = (value || "anthropic").toLowerCase();
  if (normalized.startsWith("local")) return "local";
  if (normalized.startsWith("nvidia") || normalized.startsWith("deepseek") || normalized.startsWith("nim")) return "nvidia";
  return "anthropic";
}

function normalizeTtsProvider(value?: string): TTSProvider {
  return (value || "fish").toLowerCase().startsWith("local") ? "local" : "fish";
}

function normalizeLocalTtsEngine(value?: string): LocalTTSEngine {
  return (value || "kokoro").toLowerCase().startsWith("vox") ? "voxcpm" : "kokoro";
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let panelEl: HTMLElement | null = null;
let isOpen = false;
let isFirstTimeSetup = false;
let setupStep = 0; // 0=anthropic, 1=fish, 2=name, 3=done

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function apiGet<T>(url: string): Promise<T> {
  const res = await fetch(url);
  return res.json();
}

async function apiPost<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ---------------------------------------------------------------------------
// Panel HTML
// ---------------------------------------------------------------------------

function buildPanelHTML(): string {
  return `
    <div class="settings-backdrop" id="settings-backdrop"></div>
    <div class="settings-panel" id="settings-panel-inner">
      <div class="settings-header">
        <h2>Settings</h2>
        <button class="settings-close" id="settings-close">&times;</button>
      </div>

      <div class="settings-welcome" id="settings-welcome" style="display:none">
        <p>Welcome to JARVIS. Let's get you set up.</p>
      </div>

      <div class="settings-body">

        <!-- API Keys -->
        <section class="settings-section" id="section-api-keys">
          <h3>API Keys</h3>

          <div class="settings-field">
            <label>Anthropic API Key</label>
            <div class="settings-input-row">
              <input type="password" id="input-anthropic-key" placeholder="sk-ant-..." />
              <button class="settings-btn" id="btn-test-anthropic">Test</button>
              <span class="status-dot" id="status-anthropic"></span>
            </div>
          </div>

          <div class="settings-field">
            <label>NVIDIA API Key</label>
            <div class="settings-input-row">
              <input type="password" id="input-nvidia-key" placeholder="nvapi-..." />
              <button class="settings-btn" id="btn-test-nvidia-llm">Test</button>
              <span class="status-dot" id="status-nvidia-llm"></span>
            </div>
            <div class="settings-field-note">Use a free/trial NVIDIA NIM API key from build.nvidia.com for DeepSeek models.</div>
          </div>

          <div class="settings-field">
            <label>LLM Provider</label>
            <select id="input-llm-provider">
              <option value="anthropic">Anthropic</option>
              <option value="local">Local LM Studio</option>
              <option value="nvidia">NVIDIA DeepSeek</option>
            </select>
            <div class="settings-field-note" id="llm-provider-note">Choose which language model provider JARVIS should use.</div>
          </div>

          <div class="settings-field" id="field-local-llm-base-url">
            <label>Local LLM Endpoint</label>
            <div class="settings-input-row">
              <input type="text" id="input-local-llm-base-url" placeholder="http://127.0.0.1:1234/v1" />
              <button class="settings-btn" id="btn-test-local-llm">Test</button>
              <span class="status-dot" id="status-local-llm"></span>
            </div>
          </div>

          <div class="settings-field" id="field-local-llm-model">
            <label>Local LLM Model</label>
            <div class="settings-input-row">
              <input type="text" id="input-local-llm-model" placeholder="Leave blank to auto-pick a loaded chat model" />
            </div>
            <div class="settings-field-note" id="local-llm-model-note">For LM Studio, leave this blank to auto-pick the first loaded non-embedding model.</div>
          </div>

          <div class="settings-field" id="field-nvidia-llm-base-url">
            <label>NVIDIA Endpoint</label>
            <div class="settings-input-row">
              <input type="text" id="input-nvidia-llm-base-url" placeholder="https://integrate.api.nvidia.com/v1" />
            </div>
          </div>

          <div class="settings-field" id="field-nvidia-llm-model">
            <label>NVIDIA DeepSeek Model</label>
            <div class="settings-input-row">
              <input type="text" id="input-nvidia-llm-model" placeholder="deepseek-ai/deepseek-v4-pro" />
            </div>
            <div class="settings-field-note" id="nvidia-llm-model-note">Default is DeepSeek V4 Pro on NVIDIA NIM. You can change this to another model ID from build.nvidia.com.</div>
          </div>

          <div class="settings-field">
            <label>Fish Audio API Key</label>
            <div class="settings-input-row">
              <input type="password" id="input-fish-key" placeholder="Fish Audio key..." />
              <button class="settings-btn" id="btn-test-fish">Test</button>
              <span class="status-dot" id="status-fish"></span>
            </div>
          </div>

          <div class="settings-field">
            <label>TTS Provider</label>
            <div class="settings-switch-row">
              <span class="settings-switch-label">Fish</span>
              <label class="settings-switch" for="input-tts-provider">
                <input type="checkbox" id="input-tts-provider" />
                <span class="settings-switch-slider"></span>
              </label>
              <span class="settings-switch-label">Local</span>
            </div>
            <div class="settings-field-note" id="tts-provider-note">Use Fish Audio by default, or switch right to route JARVIS speech through a local Kokoro or VoxCPM2 server.</div>
          </div>

          <div class="settings-field" id="field-local-tts-engine">
            <label>Local TTS Engine</label>
            <select id="input-local-tts-engine">
              <option value="kokoro">Kokoro ONNX Server</option>
              <option value="voxcpm">VoxCPM2 Server</option>
            </select>
            <div class="settings-field-note" id="local-tts-engine-note">Choose which offline local speech engine JARVIS should use when TTS Provider is set to Local.</div>
          </div>

          <div class="settings-field" id="field-local-tts-url">
            <label>Local TTS Endpoint</label>
            <div class="settings-input-row">
              <input type="text" id="input-local-tts-url" placeholder="http://127.0.0.1:8881" />
              <button class="settings-btn" id="btn-test-local-tts">Test</button>
              <span class="status-dot" id="status-local-tts"></span>
            </div>
          </div>

          <div class="settings-field" id="field-local-tts-model-path" style="display:none">
            <label>VoxCPM Model Folder</label>
            <div class="settings-input-row">
              <input type="text" id="input-local-tts-model-path" placeholder="D:\\AI Project\\VoxCPM2" />
            </div>
            <div class="settings-field-note">Used only for VoxCPM2. Point this to the downloaded model folder that contains config, tokenizer, and model weights.</div>
          </div>

          <div class="settings-field" id="field-local-tts-voice">
            <label>Local TTS Voice (Default / English)</label>
            <div class="settings-input-row">
              <input type="text" id="input-local-tts-voice" placeholder="bm_george" />
            </div>
          </div>

          <div class="settings-field" id="field-local-tts-voice-id">
            <label>Local TTS Voice (Indonesia, optional)</label>
            <div class="settings-input-row">
              <input type="text" id="input-local-tts-voice-id" placeholder="Optional local Indonesian voice..." />
            </div>
          </div>

          <div class="settings-field" id="field-local-tts-speed">
            <label>Local TTS Speed</label>
            <div class="settings-input-row">
              <input type="number" id="input-local-tts-speed" min="0.5" max="2" step="0.05" placeholder="1.0" />
            </div>
            <div class="settings-field-note" id="local-tts-note">Kokoro is strongest for offline English/JARVIS-style delivery. Use an Indonesian voice here only if your local server supports it.</div>
          </div>

          <div class="settings-field">
            <label>English Voice Preset</label>
            <select id="input-fish-voice-preset">
              ${buildVoicePresetOptions("en")}
            </select>
            <div class="settings-field-note">Quick presets for the English JARVIS voice.</div>
          </div>

          <div class="settings-field">
            <label>Fish Voice ID (English / Default)</label>
            <div class="settings-input-row">
              <input type="text" id="input-fish-voice-id" placeholder="612b878b113047d9a770c069c8b4fdfe" />
            </div>
          </div>

          <div class="settings-field">
            <label>Indonesia Voice Preset</label>
            <select id="input-fish-voice-preset-id">
              ${buildVoicePresetOptions("id")}
            </select>
            <div class="settings-field-note">Quick presets for Indonesian voices like Jokowi and Prabowo.</div>
          </div>

          <div class="settings-field">
            <label>Fish Voice ID (Indonesia, optional)</label>
            <div class="settings-input-row">
              <input type="text" id="input-fish-voice-id-id" placeholder="Optional Indonesian voice ID..." />
            </div>
            <div class="settings-field-note">If this is filled, JARVIS will switch to it automatically when Language is set to Indonesia.</div>
          </div>

          <div class="settings-actions">
            <button class="settings-btn" id="btn-save-voice-id">Save Voice IDs</button>
          </div>

          <div class="settings-actions">
            <button class="settings-btn primary" id="btn-save-keys">Save Keys</button>
          </div>
        </section>

        <!-- Connection Status -->
        <section class="settings-section" id="section-status">
          <h3>Connection Status</h3>
          <div class="status-grid">
            <div class="status-row"><span class="status-dot" id="status-claude-cli"></span><span>Claude Code CLI</span></div>
            <div class="status-row"><span class="status-dot" id="status-calendar"></span><span>Apple Calendar</span></div>
            <div class="status-row"><span class="status-dot" id="status-mail"></span><span>Apple Mail</span></div>
            <div class="status-row"><span class="status-dot" id="status-notes"></span><span>Apple Notes</span></div>
            <div class="status-row"><span class="status-dot" id="status-server"></span><span>Server</span><span class="status-detail" id="status-server-detail"></span></div>
          </div>
        </section>

        <!-- User Preferences -->
        <section class="settings-section" id="section-preferences">
          <h3>User Preferences</h3>

          <div class="settings-field">
            <label>Your Name</label>
            <input type="text" id="input-user-name" placeholder="Your name" />
          </div>

          <div class="settings-field">
            <label>Honorific</label>
            <select id="input-honorific">
              <option value="sir">Sir</option>
              <option value="ma'am">Ma'am</option>
              <option value="none">None</option>
            </select>
          </div>

          <div class="settings-field">
            <label>Language</label>
            <div class="settings-switch-row">
              <span class="settings-switch-label">English</span>
              <label class="settings-switch" for="input-user-language">
                <input type="checkbox" id="input-user-language" />
                <span class="settings-switch-slider"></span>
              </label>
              <span class="settings-switch-label">Indonesia</span>
            </div>
            <div class="settings-field-note">Switches JARVIS replies and microphone recognition between English and Bahasa Indonesia.</div>
          </div>

          <div class="settings-field">
            <label>Local Fallback Mode</label>
            <div class="settings-switch-row">
              <span class="settings-switch-label">Off</span>
              <label class="settings-switch" for="input-local-fallback-mode">
                <input type="checkbox" id="input-local-fallback-mode" />
                <span class="settings-switch-slider"></span>
              </label>
              <span class="settings-switch-label">On</span>
            </div>
            <div class="settings-field-note">When Anthropic is unavailable, JARVIS will fall back to local replies for simple chat, status questions, and cached briefings instead of stopping cold.</div>
          </div>

          <div class="settings-field">
            <label>Text Chat Box</label>
            <div class="settings-switch-row">
              <span class="settings-switch-label">Hidden</span>
              <label class="settings-switch" for="input-chat-box-enabled">
                <input type="checkbox" id="input-chat-box-enabled" />
                <span class="settings-switch-slider"></span>
              </label>
              <span class="settings-switch-label">Shown</span>
            </div>
            <div class="settings-field-note">Shows a typed chat box on the main JARVIS screen. This uses the same command pipeline as voice, so it is useful when microphone access is blocked on tablet/mobile.</div>
          </div>

          <div class="settings-field">
            <label>Persona Mode</label>
            <div class="persona-segmented" id="input-persona-mode" role="radiogroup" aria-label="Persona mode">
              <button type="button" class="persona-seg" data-persona="jarvis" role="radio" aria-checked="true">JARVIS</button>
              <button type="button" class="persona-seg" data-persona="ultron" role="radio" aria-checked="false">Ultron</button>
              <button type="button" class="persona-seg" data-persona="scifi"  role="radio" aria-checked="false">Sci-Fi</button>
            </div>
            <div class="settings-field-note" id="persona-mode-note">JARVIS is the cool blue default. Ultron swaps to orange with a clock-face bezel. Sci-Fi surrounds the core with a Tony Stark style holographic HUD: dashed boundary ring, precision ring, red targeting brackets, data arcs, radial tick marks and a crosshair — colors shift with state like the JARVIS rings.</div>
          </div>

          <div class="settings-field">
            <label>Home Location</label>
            <input type="text" id="input-user-location" placeholder="Cimahi" />
          </div>

          <div class="settings-field">
            <label>Country / National Focus</label>
            <input type="text" id="input-user-country" placeholder="Indonesia" />
          </div>

          <div class="settings-field">
            <label>Calendar Accounts</label>
            <textarea id="input-calendar-accounts" rows="2" placeholder="auto (or comma-separated emails)"></textarea>
          </div>

          <div class="settings-actions">
            <button class="settings-btn primary" id="btn-save-prefs">Save Preferences</button>
          </div>
        </section>

        <!-- System Info -->
        <section class="settings-section" id="section-sysinfo">
          <h3>System Info</h3>
          <div class="sysinfo-grid">
            <div class="sysinfo-row"><span class="sysinfo-label">Memory entries</span><span id="sysinfo-memory">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Tasks</span><span id="sysinfo-tasks">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Server port</span><span id="sysinfo-port">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Uptime</span><span id="sysinfo-uptime">--</span></div>
          </div>
        </section>

        <!-- Setup Navigation (first-time only) -->
        <div class="setup-nav" id="setup-nav" style="display:none">
          <button class="settings-btn primary" id="btn-setup-next">Next</button>
        </div>

      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Panel lifecycle
// ---------------------------------------------------------------------------

function createPanel(): HTMLElement {
  const container = document.createElement("div");
  container.id = "settings-container";
  container.innerHTML = buildPanelHTML();
  document.body.appendChild(container);
  return container;
}

function getSelectedLlmProvider(): LLMProvider {
  const llmProviderEl = document.getElementById("input-llm-provider") as HTMLSelectElement | null;
  return normalizeLlmProvider(llmProviderEl?.value || "anthropic");
}

function setSelectedLlmProvider(provider?: string) {
  const llmProviderEl = document.getElementById("input-llm-provider") as HTMLSelectElement | null;
  if (!llmProviderEl) return;
  llmProviderEl.value = normalizeLlmProvider(provider);
  refreshLlmProviderUi();
}

function refreshLlmProviderUi(status?: StatusResponse | null) {
  const provider = getSelectedLlmProvider();
  const providerNoteEl = document.getElementById("llm-provider-note");
  const localModelNoteEl = document.getElementById("local-llm-model-note");
  const localEndpointFieldEl = document.getElementById("field-local-llm-base-url");
  const localModelFieldEl = document.getElementById("field-local-llm-model");
  const nvidiaEndpointFieldEl = document.getElementById("field-nvidia-llm-base-url");
  const nvidiaModelFieldEl = document.getElementById("field-nvidia-llm-model");

  if (providerNoteEl) {
    if (provider === "local") {
      providerNoteEl.textContent = "Local mode is active. JARVIS will talk to LM Studio or another OpenAI-compatible server using the endpoint below.";
    } else if (provider === "nvidia") {
      providerNoteEl.textContent = "NVIDIA DeepSeek mode is active. JARVIS will use the NVIDIA NIM OpenAI-compatible endpoint below.";
    } else {
      providerNoteEl.textContent = "Anthropic mode is active. Choose Local or NVIDIA DeepSeek whenever you want another model provider.";
    }
  }

  if (localEndpointFieldEl) localEndpointFieldEl.style.display = provider === "local" ? "" : "none";
  if (localModelFieldEl) localModelFieldEl.style.display = provider === "local" ? "" : "none";
  if (nvidiaEndpointFieldEl) nvidiaEndpointFieldEl.style.display = provider === "nvidia" ? "" : "none";
  if (nvidiaModelFieldEl) nvidiaModelFieldEl.style.display = provider === "nvidia" ? "" : "none";

  if (localModelNoteEl) {
    const localModels = status?.llm_status?.local_models?.filter((model) => !model.toLowerCase().includes("embedding")) || [];
    if (localModels.length > 0) {
      localModelNoteEl.textContent = `Loaded local chat models: ${localModels.join(", ")}. Leave this blank to auto-pick the first one.`;
    } else {
      localModelNoteEl.textContent = "For LM Studio, leave this blank to auto-pick the first loaded non-embedding model.";
    }
  }
}

function getSelectedTtsProvider(): TTSProvider {
  const ttsProviderEl = document.getElementById("input-tts-provider") as HTMLInputElement | null;
  return ttsProviderEl?.checked ? "local" : "fish";
}

function getSelectedLocalTtsEngine(): LocalTTSEngine {
  const engineEl = document.getElementById("input-local-tts-engine") as HTMLSelectElement | null;
  return normalizeLocalTtsEngine(engineEl?.value || "kokoro");
}

function setSelectedTtsProvider(provider?: string) {
  const ttsProviderEl = document.getElementById("input-tts-provider") as HTMLInputElement | null;
  if (!ttsProviderEl) return;
  ttsProviderEl.checked = normalizeTtsProvider(provider) === "local";
  refreshTtsProviderUi();
}

function setSelectedLocalTtsEngine(engine?: string) {
  const engineEl = document.getElementById("input-local-tts-engine") as HTMLSelectElement | null;
  if (!engineEl) return;
  engineEl.value = normalizeLocalTtsEngine(engine);
  refreshTtsProviderUi();
}

function refreshTtsProviderUi(status?: StatusResponse | null) {
  const provider = getSelectedTtsProvider();
  const engine = getSelectedLocalTtsEngine();
  const providerNoteEl = document.getElementById("tts-provider-note");
  const engineNoteEl = document.getElementById("local-tts-engine-note");
  const localTtsNoteEl = document.getElementById("local-tts-note");
  const endpointFieldEl = document.getElementById("field-local-tts-url");
  const engineFieldEl = document.getElementById("field-local-tts-engine");
  const modelPathFieldEl = document.getElementById("field-local-tts-model-path");
  const localVoiceFieldEl = document.getElementById("field-local-tts-voice");
  const localVoiceIdFieldEl = document.getElementById("field-local-tts-voice-id");
  const localSpeedFieldEl = document.getElementById("field-local-tts-speed");
  const localTtsUrlEl = document.getElementById("input-local-tts-url") as HTMLInputElement | null;

  if (providerNoteEl) {
    if (provider === "local") {
      providerNoteEl.textContent =
        engine === "voxcpm"
          ? "Local mode is active through VoxCPM2. JARVIS will send reply text to your local VoxCPM server and play the returned WAV audio."
          : "Local mode is active through Kokoro. JARVIS will synthesize speech through your local Kokoro server and stay offline for voice output.";
    } else {
      providerNoteEl.textContent = "Fish Audio mode is active. Switch right whenever you want offline speech through Kokoro or VoxCPM2.";
    }
  }

  if (engineNoteEl) {
    engineNoteEl.textContent =
      engine === "voxcpm"
        ? "VoxCPM2 uses the downloaded local model folder and runs from a dedicated local TTS server. Use the model path below."
        : "Kokoro ONNX uses the existing lightweight local TTS server and supports named voices such as bm_george.";
  }

  if (engineFieldEl) engineFieldEl.style.display = provider === "local" ? "" : "none";
  if (endpointFieldEl) endpointFieldEl.style.display = provider === "local" ? "" : "none";
  if (modelPathFieldEl) modelPathFieldEl.style.display = provider === "local" && engine === "voxcpm" ? "" : "none";
  if (localVoiceFieldEl) localVoiceFieldEl.style.display = provider === "local" && engine === "kokoro" ? "" : "none";
  if (localVoiceIdFieldEl) localVoiceIdFieldEl.style.display = provider === "local" && engine === "kokoro" ? "" : "none";
  if (localSpeedFieldEl) localSpeedFieldEl.style.display = provider === "local" ? "" : "none";

  if (localTtsUrlEl) {
    localTtsUrlEl.placeholder = LOCAL_TTS_ENGINE_DEFAULT_URLS[engine];
  }

  if (localTtsNoteEl) {
    const resolvedBaseUrl = status?.tts_status?.local_resolved_base_url || status?.tts_status?.local_base_url || "";
    const lastError = (status?.tts_status?.local_last_error || "").trim();
    if (provider === "local" && resolvedBaseUrl) {
      localTtsNoteEl.textContent =
        engine === "voxcpm"
          ? `VoxCPM2 local speech will use ${resolvedBaseUrl}. Voice fields are ignored for this engine; the main requirement is a valid VoxCPM2 model folder.`
          : `Local speech will use ${resolvedBaseUrl}. Kokoro is strongest for offline English/JARVIS-style delivery; Indonesian depends on the local voice/model you provide.`;
      if (lastError) {
        localTtsNoteEl.textContent += ` Current engine error: ${lastError}`;
      }
    } else if (provider === "local" && engine === "voxcpm") {
      localTtsNoteEl.textContent = "VoxCPM2 ignores the Kokoro voice name fields. Keep the endpoint on its own port, then point the model path to your downloaded VoxCPM2 folder.";
      if (lastError) {
        localTtsNoteEl.textContent += ` Current engine error: ${lastError}`;
      }
    } else {
      localTtsNoteEl.textContent = "Kokoro is strongest for offline English/JARVIS-style delivery. Use an Indonesian voice here only if your local server supports it.";
      if (provider === "local" && lastError) {
        localTtsNoteEl.textContent += ` Current engine error: ${lastError}`;
      }
    }
  }
}

function setDotStatus(id: string, status: "green" | "red" | "yellow" | "off") {
  const dot = document.getElementById(id);
  if (!dot) return;
  dot.className = "status-dot";
  if (status !== "off") dot.classList.add(`status-${status}`);
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

async function loadStatus() {
  try {
    const status = await apiGet<StatusResponse>("/api/settings/status");

    setDotStatus("status-claude-cli", status.claude_code_installed ? "green" : "red");
    setDotStatus("status-calendar", status.calendar_accessible ? "green" : "red");
    setDotStatus("status-mail", status.mail_accessible ? "green" : "red");
    setDotStatus("status-notes", status.notes_accessible ? "green" : "red");
    setDotStatus("status-server", "green");

    const serverDetail = document.getElementById("status-server-detail");
    if (serverDetail) serverDetail.textContent = `port ${status.server_port} | up ${formatUptime(status.uptime_seconds)}`;

    // API key status dots
    setDotStatus("status-anthropic", status.env_keys_set.anthropic ? "green" : "red");
    setDotStatus("status-nvidia-llm", status.env_keys_set.nvidia ? "green" : "red");
    setDotStatus("status-fish", status.env_keys_set.fish_audio ? "green" : "red");
    setDotStatus(
      "status-local-llm",
      status.llm_status?.local_server_reachable
        ? "green"
        : normalizeLlmProvider(status.env_keys_set.llm_provider) === "local"
          ? "red"
          : "off"
    );
    setDotStatus(
      "status-local-tts",
      status.tts_status?.local_server_reachable
        ? "green"
        : normalizeTtsProvider(status.env_keys_set.tts_provider) === "local"
          ? "red"
          : "off"
    );
    refreshLlmProviderUi(status);
    refreshTtsProviderUi(status);

    // System info
    const memEl = document.getElementById("sysinfo-memory");
    if (memEl) memEl.textContent = String(status.memory_count);
    const taskEl = document.getElementById("sysinfo-tasks");
    if (taskEl) taskEl.textContent = String(status.task_count);
    const portEl = document.getElementById("sysinfo-port");
    if (portEl) portEl.textContent = String(status.server_port);
    const upEl = document.getElementById("sysinfo-uptime");
    if (upEl) upEl.textContent = formatUptime(status.uptime_seconds);

    return status;
  } catch (e) {
    console.error("[settings] failed to load status:", e);
    setDotStatus("status-server", "red");
    return null;
  }
}

async function loadPreferences() {
  try {
    const prefs = await apiGet<PreferencesResponse>("/api/settings/preferences");
    const nameEl = document.getElementById("input-user-name") as HTMLInputElement;
    const honEl = document.getElementById("input-honorific") as HTMLSelectElement;
    const langEl = document.getElementById("input-user-language") as HTMLInputElement;
    const fallbackEl = document.getElementById("input-local-fallback-mode") as HTMLInputElement;
    const chatBoxEl = document.getElementById("input-chat-box-enabled") as HTMLInputElement;
    const llmProviderEl = document.getElementById("input-llm-provider") as HTMLSelectElement;
    const localBaseUrlEl = document.getElementById("input-local-llm-base-url") as HTMLInputElement;
    const localModelEl = document.getElementById("input-local-llm-model") as HTMLInputElement;
    const nvidiaBaseUrlEl = document.getElementById("input-nvidia-llm-base-url") as HTMLInputElement;
    const nvidiaModelEl = document.getElementById("input-nvidia-llm-model") as HTMLInputElement;
    const ttsProviderEl = document.getElementById("input-tts-provider") as HTMLInputElement;
    const localTtsEngineEl = document.getElementById("input-local-tts-engine") as HTMLSelectElement;
    const localTtsUrlEl = document.getElementById("input-local-tts-url") as HTMLInputElement;
    const localTtsModelPathEl = document.getElementById("input-local-tts-model-path") as HTMLInputElement;
    const localTtsVoiceEl = document.getElementById("input-local-tts-voice") as HTMLInputElement;
    const localTtsVoiceIdEl = document.getElementById("input-local-tts-voice-id") as HTMLInputElement;
    const localTtsSpeedEl = document.getElementById("input-local-tts-speed") as HTMLInputElement;
    const englishVoiceEl = document.getElementById("input-fish-voice-id") as HTMLInputElement;
    const indonesianVoiceEl = document.getElementById("input-fish-voice-id-id") as HTMLInputElement;
    const locationEl = document.getElementById("input-user-location") as HTMLInputElement;
    const countryEl = document.getElementById("input-user-country") as HTMLInputElement;
    const calEl = document.getElementById("input-calendar-accounts") as HTMLTextAreaElement;
    if (nameEl) nameEl.value = prefs.user_name || "";
    if (honEl) honEl.value = prefs.honorific || "sir";
    if (langEl) langEl.checked = normalizeUserLanguage(prefs.user_language) === "id";
    if (fallbackEl) fallbackEl.checked = prefs.local_fallback_mode !== false;
    if (chatBoxEl) chatBoxEl.checked = normalizeChatBoxPreference(prefs.chat_box_enabled);
    if (llmProviderEl) setSelectedLlmProvider(prefs.llm_provider || "anthropic");
    if (localBaseUrlEl) localBaseUrlEl.value = prefs.local_llm_base_url || "http://127.0.0.1:1234/v1";
    if (localModelEl) localModelEl.value = prefs.local_llm_model || "";
    if (nvidiaBaseUrlEl) nvidiaBaseUrlEl.value = prefs.nvidia_llm_base_url || DEFAULT_NVIDIA_LLM_BASE_URL;
    if (nvidiaModelEl) nvidiaModelEl.value = prefs.nvidia_llm_model || DEFAULT_NVIDIA_LLM_MODEL;
    if (ttsProviderEl) setSelectedTtsProvider(prefs.tts_provider || "fish");
    if (localTtsEngineEl) setSelectedLocalTtsEngine(prefs.local_tts_engine || "kokoro");
    if (localTtsUrlEl) localTtsUrlEl.value = prefs.local_tts_url || LOCAL_TTS_ENGINE_DEFAULT_URLS[normalizeLocalTtsEngine(prefs.local_tts_engine)];
    if (localTtsModelPathEl) localTtsModelPathEl.value = prefs.local_tts_model_path || "D:\\AI Project\\VoxCPM2";
    if (localTtsVoiceEl) localTtsVoiceEl.value = prefs.local_tts_voice || "bm_george";
    if (localTtsVoiceIdEl) localTtsVoiceIdEl.value = prefs.local_tts_voice_id || "";
    if (localTtsSpeedEl) localTtsSpeedEl.value = String(prefs.local_tts_speed || "1.0");
    if (englishVoiceEl) englishVoiceEl.value = prefs.fish_voice_id || "";
    if (indonesianVoiceEl) indonesianVoiceEl.value = prefs.fish_voice_id_id || "";
    if (locationEl) locationEl.value = prefs.user_location || "Cimahi";
    if (countryEl) countryEl.value = prefs.user_country || "Indonesia";
    if (calEl) calEl.value = prefs.calendar_accounts || "auto";
    syncVoicePresetSelect("input-fish-voice-preset", "input-fish-voice-id", "en");
    syncVoicePresetSelect("input-fish-voice-preset-id", "input-fish-voice-id-id", "id");
    persistUserLanguage(prefs.user_language || "en");
    persistChatBoxPreference(normalizeChatBoxPreference(prefs.chat_box_enabled));
    refreshLlmProviderUi();
    refreshTtsProviderUi();
    // Sync Persona mode selector from localStorage (purely frontend preference)
    syncPersonaSelectorUi(getStoredPersonaMode());
  } catch (e) {
    console.error("[settings] failed to load preferences:", e);
  }
}

function wireEvents() {
  // Close
  document.getElementById("settings-close")?.addEventListener("click", closeSettings);
  document.getElementById("settings-backdrop")?.addEventListener("click", closeSettings);

  document.getElementById("input-fish-voice-preset")?.addEventListener("change", () => {
    const selectEl = document.getElementById("input-fish-voice-preset") as HTMLSelectElement;
    const inputEl = document.getElementById("input-fish-voice-id") as HTMLInputElement;
    if (selectEl?.value && inputEl) inputEl.value = selectEl.value;
  });

  document.getElementById("input-fish-voice-preset-id")?.addEventListener("change", () => {
    const selectEl = document.getElementById("input-fish-voice-preset-id") as HTMLSelectElement;
    const inputEl = document.getElementById("input-fish-voice-id-id") as HTMLInputElement;
    if (inputEl) inputEl.value = selectEl?.value || "";
  });

  document.getElementById("input-fish-voice-id")?.addEventListener("input", () => {
    syncVoicePresetSelect("input-fish-voice-preset", "input-fish-voice-id", "en");
  });

  document.getElementById("input-fish-voice-id-id")?.addEventListener("input", () => {
    syncVoicePresetSelect("input-fish-voice-preset-id", "input-fish-voice-id-id", "id");
  });

  document.getElementById("input-llm-provider")?.addEventListener("change", () => {
    refreshLlmProviderUi();
  });

  document.getElementById("input-chat-box-enabled")?.addEventListener("change", () => {
    const enabled = (document.getElementById("input-chat-box-enabled") as HTMLInputElement).checked;
    persistChatBoxPreference(enabled);
  });

  document.getElementById("input-tts-provider")?.addEventListener("change", () => {
    refreshTtsProviderUi();
  });

  document.getElementById("input-local-tts-engine")?.addEventListener("change", () => {
    const engine = getSelectedLocalTtsEngine();
    const localTtsUrlEl = document.getElementById("input-local-tts-url") as HTMLInputElement | null;
    const currentUrl = localTtsUrlEl?.value.trim() || "";
    const knownDefaults = Object.values(LOCAL_TTS_ENGINE_DEFAULT_URLS);
    if (localTtsUrlEl && (!currentUrl || knownDefaults.includes(currentUrl))) {
      localTtsUrlEl.value = LOCAL_TTS_ENGINE_DEFAULT_URLS[engine];
    }
    refreshTtsProviderUi();
  });

  document.querySelectorAll<HTMLButtonElement>(".persona-seg").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = normalizePersonaMode(btn.dataset.persona);
      setStoredPersonaMode(mode);
      syncPersonaSelectorUi(mode);
    });
  });

  // Save keys
  document.getElementById("btn-save-keys")?.addEventListener("click", async () => {
    const anthropicKey = (document.getElementById("input-anthropic-key") as HTMLInputElement).value.trim();
    const nvidiaKey = (document.getElementById("input-nvidia-key") as HTMLInputElement).value.trim();
    const fishKey = (document.getElementById("input-fish-key") as HTMLInputElement).value.trim();

    if (anthropicKey) {
      await apiPost("/api/settings/keys", { key_name: "ANTHROPIC_API_KEY", key_value: anthropicKey });
    }
    if (nvidiaKey) {
      await apiPost("/api/settings/keys", { key_name: "NVIDIA_API_KEY", key_value: nvidiaKey });
    }
    if (fishKey) {
      await apiPost("/api/settings/keys", { key_name: "FISH_API_KEY", key_value: fishKey });
    }
    await loadStatus();
  });

  // Save voice ID
  document.getElementById("btn-save-voice-id")?.addEventListener("click", async () => {
    const voiceId = (document.getElementById("input-fish-voice-id") as HTMLInputElement).value.trim();
    const indonesianVoiceId = (document.getElementById("input-fish-voice-id-id") as HTMLInputElement).value.trim();
    await apiPost("/api/settings/keys", {
      key_name: "FISH_VOICE_ID",
      key_value: voiceId || FISH_VOICE_PRESETS.en[0].id,
    });
    await apiPost("/api/settings/keys", {
      key_name: "FISH_VOICE_ID_ID",
      key_value: indonesianVoiceId,
    });
    await loadPreferences();
  });

  // Test Anthropic
  document.getElementById("btn-test-anthropic")?.addEventListener("click", async () => {
    setDotStatus("status-anthropic", "yellow");
    const key = (document.getElementById("input-anthropic-key") as HTMLInputElement).value.trim();
    try {
      const result = await apiPost<{ valid: boolean; error?: string }>("/api/settings/test-anthropic", { key_value: key || undefined });
      setDotStatus("status-anthropic", result.valid ? "green" : "red");
    } catch {
      setDotStatus("status-anthropic", "red");
    }
  });

  document.getElementById("btn-test-local-llm")?.addEventListener("click", async () => {
    setDotStatus("status-local-llm", "yellow");
    try {
      const result = await apiPost<{ valid: boolean; error?: string }>("/api/settings/test-local-llm", {});
      setDotStatus("status-local-llm", result.valid ? "green" : "red");
    } catch {
      setDotStatus("status-local-llm", "red");
    }
  });

  document.getElementById("btn-test-nvidia-llm")?.addEventListener("click", async () => {
    setDotStatus("status-nvidia-llm", "yellow");
    try {
      const key = (document.getElementById("input-nvidia-key") as HTMLInputElement).value.trim();
      const base_url = (document.getElementById("input-nvidia-llm-base-url") as HTMLInputElement).value.trim();
      const model = (document.getElementById("input-nvidia-llm-model") as HTMLInputElement).value.trim();
      const result = await apiPost<{ valid: boolean; error?: string }>("/api/settings/test-nvidia-llm", {
        key_value: key || undefined,
        base_url,
        model,
      });
      setDotStatus("status-nvidia-llm", result.valid ? "green" : "red");
    } catch {
      setDotStatus("status-nvidia-llm", "red");
    }
  });

  document.getElementById("btn-test-local-tts")?.addEventListener("click", async () => {
    setDotStatus("status-local-tts", "yellow");
    try {
      const engine = getSelectedLocalTtsEngine();
      const base_url = (document.getElementById("input-local-tts-url") as HTMLInputElement).value.trim();
      const model_path = (document.getElementById("input-local-tts-model-path") as HTMLInputElement).value.trim();
      const voice = (document.getElementById("input-local-tts-voice") as HTMLInputElement).value.trim();
      const speed = Number((document.getElementById("input-local-tts-speed") as HTMLInputElement).value || "1");
      const language = (document.getElementById("input-user-language") as HTMLInputElement).checked ? "id" : "en";
      const result = await apiPost<{ valid: boolean; error?: string }>("/api/settings/test-local-tts", {
        engine,
        base_url,
        model_path,
        voice,
        speed,
        language,
      });
      setDotStatus("status-local-tts", result.valid ? "green" : "red");
    } catch {
      setDotStatus("status-local-tts", "red");
    }
  });

  // Test Fish
  document.getElementById("btn-test-fish")?.addEventListener("click", async () => {
    setDotStatus("status-fish", "yellow");
    const key = (document.getElementById("input-fish-key") as HTMLInputElement).value.trim();
    try {
      const result = await apiPost<{ valid: boolean; error?: string }>("/api/settings/test-fish", { key_value: key || undefined });
      setDotStatus("status-fish", result.valid ? "green" : "red");
    } catch {
      setDotStatus("status-fish", "red");
    }
  });

  // Save preferences
  document.getElementById("btn-save-prefs")?.addEventListener("click", async () => {
    const user_name = (document.getElementById("input-user-name") as HTMLInputElement).value.trim();
    const honorific = (document.getElementById("input-honorific") as HTMLSelectElement).value;
    const user_language = (document.getElementById("input-user-language") as HTMLInputElement).checked ? "id" : "en";
    const local_fallback_mode = (document.getElementById("input-local-fallback-mode") as HTMLInputElement).checked;
    const chat_box_enabled = (document.getElementById("input-chat-box-enabled") as HTMLInputElement).checked;
    const llm_provider = getSelectedLlmProvider();
    const local_llm_base_url = (document.getElementById("input-local-llm-base-url") as HTMLInputElement).value.trim();
    const local_llm_model = (document.getElementById("input-local-llm-model") as HTMLInputElement).value.trim();
    const nvidia_llm_base_url = (document.getElementById("input-nvidia-llm-base-url") as HTMLInputElement).value.trim();
    const nvidia_llm_model = (document.getElementById("input-nvidia-llm-model") as HTMLInputElement).value.trim();
    const tts_provider = getSelectedTtsProvider();
    const local_tts_engine = getSelectedLocalTtsEngine();
    const local_tts_url = (document.getElementById("input-local-tts-url") as HTMLInputElement).value.trim();
    const local_tts_model_path = (document.getElementById("input-local-tts-model-path") as HTMLInputElement).value.trim();
    const local_tts_voice = (document.getElementById("input-local-tts-voice") as HTMLInputElement).value.trim();
    const local_tts_voice_id = (document.getElementById("input-local-tts-voice-id") as HTMLInputElement).value.trim();
    const local_tts_speed = Number((document.getElementById("input-local-tts-speed") as HTMLInputElement).value || "1");
    const user_location = (document.getElementById("input-user-location") as HTMLInputElement).value.trim();
    const user_country = (document.getElementById("input-user-country") as HTMLInputElement).value.trim();
    const calendar_accounts = (document.getElementById("input-calendar-accounts") as HTMLTextAreaElement).value.trim();
    await apiPost("/api/settings/preferences", {
      user_name,
      honorific,
      user_language,
      local_fallback_mode,
      chat_box_enabled,
      llm_provider,
      local_llm_base_url,
      local_llm_model,
      nvidia_llm_base_url,
      nvidia_llm_model,
      tts_provider,
      local_tts_engine,
      local_tts_url,
      local_tts_model_path,
      local_tts_voice,
      local_tts_voice_id,
      local_tts_speed,
      user_location,
      user_country,
      calendar_accounts,
    });
    persistUserLanguage(user_language);
    persistChatBoxPreference(chat_box_enabled);
    await loadStatus();
  });

  // Setup next button
  document.getElementById("btn-setup-next")?.addEventListener("click", advanceSetup);
}

// ---------------------------------------------------------------------------
// First-time setup wizard
// ---------------------------------------------------------------------------

function enterSetupMode() {
  isFirstTimeSetup = true;
  setupStep = 0;

  const welcome = document.getElementById("settings-welcome");
  if (welcome) welcome.style.display = "block";

  const nav = document.getElementById("setup-nav");
  if (nav) nav.style.display = "flex";

  // Hide sections except API keys
  showSetupStep(0);
}

function showSetupStep(step: number) {
  const sections = ["section-api-keys", "section-status", "section-preferences", "section-sysinfo"];
  sections.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (step === 0 && i === 0) el.style.display = "";
    else if (step === 1 && i === 0) el.style.display = "";
    else if (step === 2 && i === 2) el.style.display = "";
    else if (step === 3) el.style.display = "";
    else el.style.display = "none";
  });

  const nextBtn = document.getElementById("btn-setup-next");
  if (nextBtn) {
    if (step === 0) nextBtn.textContent = "Next: Test Keys";
    else if (step === 1) nextBtn.textContent = "Next: Set Your Name";
    else if (step === 2) nextBtn.textContent = "Finish Setup";
    else nextBtn.style.display = "none";
  }
}

async function advanceSetup() {
  setupStep++;
  if (setupStep >= 3) {
    // Done — save everything and close
    isFirstTimeSetup = false;
    const welcome = document.getElementById("settings-welcome");
    if (welcome) welcome.style.display = "none";
    const nav = document.getElementById("setup-nav");
    if (nav) nav.style.display = "none";

    // Show all sections
    ["section-api-keys", "section-status", "section-preferences", "section-sysinfo"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.style.display = "";
    });

    closeSettings();
    return;
  }
  showSetupStep(setupStep);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function openSettings() {
  if (isOpen) return;
  isOpen = true;

  if (!panelEl) {
    panelEl = createPanel();
    wireEvents();
  }

  panelEl.style.display = "block";

  // Trigger animation
  requestAnimationFrame(() => {
    panelEl!.classList.add("open");
  });

  // Load data
  const status = await loadStatus();
  await loadPreferences();

  // Check for first-time setup
  if (status && !status.env_keys_set.anthropic) {
    enterSetupMode();
  }
}

export function closeSettings() {
  if (!panelEl || !isOpen) return;
  isOpen = false;
  panelEl.classList.remove("open");
  setTimeout(() => {
    if (panelEl) panelEl.style.display = "none";
  }, 300);
}

export function isSettingsOpen(): boolean {
  return isOpen;
}

/**
 * Check if first-time setup is needed and auto-open.
 */
export async function checkFirstTimeSetup(): Promise<boolean> {
  try {
    const status = await apiGet<StatusResponse>("/api/settings/status");
    if (!status.env_keys_set.anthropic) {
      openSettings();
      return true;
    }
  } catch {
    // Server not ready yet, skip
  }
  return false;
}

function buildVoicePresetOptions(language: UserLanguage): string {
  const items = FISH_VOICE_PRESETS[language];
  return [
    `<option value="">Custom / manual</option>`,
    ...items.map((item) => `<option value="${item.id}">${item.label}</option>`),
  ].join("");
}

function syncPersonaSelectorUi(mode: PersonaMode) {
  document.querySelectorAll<HTMLButtonElement>(".persona-seg").forEach((btn) => {
    const active = btn.dataset.persona === mode;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-checked", active ? "true" : "false");
  });
}

function syncVoicePresetSelect(selectId: string, inputId: string, language: UserLanguage) {
  const selectEl = document.getElementById(selectId) as HTMLSelectElement | null;
  const inputEl = document.getElementById(inputId) as HTMLInputElement | null;
  if (!selectEl || !inputEl) return;
  const currentId = inputEl.value.trim();
  const matchingPreset = FISH_VOICE_PRESETS[language].find((item) => item.id === currentId);
  selectEl.value = matchingPreset ? matchingPreset.id : "";
}

export async function syncLanguagePreferenceFromServer(): Promise<void> {
  try {
    const prefs = await apiGet<PreferencesResponse>("/api/settings/preferences");
    persistUserLanguage(prefs.user_language || "en");
    persistChatBoxPreference(normalizeChatBoxPreference(prefs.chat_box_enabled));
  } catch {
    // Ignore startup sync failures and keep the stored language.
  }
}

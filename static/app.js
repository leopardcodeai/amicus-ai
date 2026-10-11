// App State
let selectedModel = "claude-haiku-4.5";
let isGenerating = false;

// --- Security & Helpers -------------------------------------------------------
// HTML-Escaping gegen XSS bei interpoliertem Text (Dateinamen, Toasts, etc.)
function esc(str) {
    return String(str ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

function getApiKey() {
    return localStorage.getItem("anwalt_api_key") || "";
}

function getMatter() {
    const el = document.getElementById("matter-input");
    return (el && el.value.trim()) || "default";
}

// Auth-Header für alle API-Aufrufe
function authHeaders(extra = {}) {
    return { "X-API-Key": getApiKey(), ...extra };
}

// DOM Elements
const uploadZone = document.getElementById("upload-zone");
const fileInput = document.getElementById("file-input");
const docList = document.getElementById("doc-list");
const chatInput = document.getElementById("chat-input");
const btnSend = document.getElementById("btn-send");
const messageFeed = document.getElementById("message-feed");
const welcomeScreen = document.getElementById("welcome-screen");
const contextPanel = document.getElementById("context-panel");
const citationsBadges = document.getElementById("citations-badges");
const btnClearChat = document.getElementById("btn-clear-chat");
const btnClearDb = document.getElementById("btn-clear-db");
const toast = document.getElementById("toast");

// Initialize
document.addEventListener("DOMContentLoaded", () => {
    // API-Key aus Speicher übernehmen / Eingabe persistieren
    const keyInput = document.getElementById("api-key-input");
    if (keyInput) {
        keyInput.value = getApiKey();
        keyInput.addEventListener("change", () => {
            localStorage.setItem("anwalt_api_key", keyInput.value.trim());
            showToast("API-Key gespeichert.", "success");
            loadDocuments();
        });
    }
    const matterInput = document.getElementById("matter-input");
    if (matterInput) matterInput.addEventListener("change", loadDocuments);

    setupLogin();
    setupEventListeners();
    loadAppVersion();
    checkAuthThenInit();
});

// App-Version (unauthentifiziert) -> Badge oben links + Login-Screen.
// So sieht man sofort, ob online schon die neue Version läuft.
async function loadAppVersion() {
    try {
        const r = await fetch("/api/version");
        if (!r.ok) return;
        const d = await r.json();
        const badge = document.getElementById("app-version");
        if (badge) badge.textContent = "v" + d.version;
        const lv = document.getElementById("login-version");
        if (lv) lv.textContent = "Version " + d.version;
    } catch (e) { /* Kaltstart – Badge bleibt leer */ }
}

// Zugangscheck: bei 401 Login-Overlay zeigen, sonst App laden
async function checkAuthThenInit() {
    const boot = document.getElementById("boot-overlay");
    let r = null;
    try {
        r = await fetch("/api/status", { headers: authHeaders() });
    } catch (e) { /* Netzwerkfehler -> vermutlich Kaltstart */ }
    // Kaltstart: Hinweis zeigen und automatisch neu versuchen.
    if (!r || r.status >= 500) {
        if (boot) boot.style.display = "flex";
        setTimeout(checkAuthThenInit, 8000);
        return;
    }
    if (boot) boot.style.display = "none";
    if (r.status === 401) { showLogin(); return; }
    hideLogin();
    loadDocuments();
    loadModels();
    loadPrompts();
    loadMatters();
}

function showLogin() { const o = document.getElementById("login-overlay"); if (o) o.style.display = "flex"; }
function hideLogin() { const o = document.getElementById("login-overlay"); if (o) o.style.display = "none"; }

function setupLogin() {
    const btn = document.getElementById("login-submit");
    const pw = document.getElementById("login-password");
    const err = document.getElementById("login-error");
    if (!btn || !pw) return;
    const submit = async () => {
        const val = pw.value.trim();
        if (!val) return;
        localStorage.setItem("anwalt_api_key", val);
        try {
            const r = await fetch("/api/status", { headers: authHeaders() });
            if (r.ok) { err.textContent = ""; checkAuthThenInit(); }
            else { err.textContent = "Falsches Passwort."; localStorage.removeItem("anwalt_api_key"); }
        } catch (e) {
            err.textContent = "Server nicht erreichbar.";
            localStorage.removeItem("anwalt_api_key");
        }
    };
    btn.addEventListener("click", submit);
    pw.addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
}

// Mandanten-Dropdown (Inbox) – nur echte Mandatsakten
async function loadMatters() {
    const sel = document.getElementById("inbox-select");
    if (!sel) return;
    try {
        const res = await fetch("/api/knowledge", { headers: authHeaders() });
        if (!res.ok) return;
        const { overview } = await res.json();
        const matters = Object.keys(overview.mandate || {});
        const cur = getMatter();
        sel.innerHTML = `<option value="">— Mandant wählen —</option>` +
            matters.map(m => `<option value="${esc(m)}" ${m === cur ? "selected" : ""}>${esc(m)}</option>`).join("");
    } catch (e) { /* still bei fehlendem Key */ }
}

// Multi-Provider-Modelle laden
async function loadModels() {
    const sel = document.getElementById("model-select");
    if (!sel) return;
    try {
        const res = await fetch("/api/models", { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();
        sel.innerHTML = data.models.map(m =>
            `<option value="${esc(m.key)}" data-cloud="${m.cloud}">${esc(m.label)} ${m.cloud ? "(Cloud)" : "(lokal)"}</option>`
        ).join("");
        // Modell ist auswählbar; gespeicherte Wahl bevorzugen, sonst Server-Default (Claude Haiku 4.5).
        const saved = localStorage.getItem("anwalt_model");
        selectedModel = (saved && data.models.some(m => m.key === saved))
            ? saved : (data.default || (data.models[0] && data.models[0].key));
        sel.value = selectedModel;
        sel.dispatchEvent(new Event("change"));
    } catch (e) { console.error("Modelle laden fehlgeschlagen:", e); }
}

// Prompt-Vorlagen laden (Dropdown)
async function loadPrompts() {
    const sel = document.getElementById("prompt-select");
    if (!sel) return;
    try {
        const res = await fetch("/api/prompts", { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();
        window._prompts = data.prompts;
        sel.innerHTML = `<option value="">Vorlage: keine</option>` + data.prompts.map(p =>
            `<option value="${esc(p.id)}">${esc(p.title)}</option>`
        ).join("");
        const savedP = localStorage.getItem("anwalt_prompt") || "";
        if ([...sel.options].some(o => o.value === savedP)) sel.value = savedP;
    } catch (e) { console.error("Prompts laden fehlgeschlagen:", e); }
}

// Toast Helper
function showToast(message, type = "info") {
    if (!toast) return;
    toast.className = `toast show ${type}`;
    toast.innerHTML = `<i class="fa-solid ${type === 'success' ? 'fa-circle-check' : type === 'error' ? 'fa-circle-exclamation' : 'fa-circle-info'}"></i> ${esc(message)}`;
    
    setTimeout(() => {
        toast.classList.remove("show");
    }, 4000);
}

// Setup Event Listeners
function setupEventListeners() {
    // Kernelemente müssen existieren, sonst Initialisierung sauber abbrechen
    // (statt mitten im Setup an einer null-Referenz zu crashen).
    if (!chatInput || !btnSend || !messageFeed || !uploadZone || !fileInput) {
        console.error("Kern-UI-Elemente fehlen – Event-Setup übersprungen.");
        return;
    }
    // Model Selection (Dropdown)
    const modelSelect = document.getElementById("model-select");
    if (modelSelect) {
        modelSelect.addEventListener("change", () => {
            selectedModel = modelSelect.value;
            localStorage.setItem("anwalt_model", selectedModel);  // fest an den Chat gebunden
            const opt = modelSelect.selectedOptions[0];
            const hint = document.getElementById("model-hint");
            if (hint) hint.textContent = opt && opt.dataset.cloud === "true"
                ? "Cloud, pseudonymisiert" : "lokal";
        });
    }
    // Prompt-Vorlage persistent (fest an den Chat gebunden)
    const promptSelect = document.getElementById("prompt-select");
    if (promptSelect) promptSelect.addEventListener("change", () =>
        localStorage.setItem("anwalt_prompt", promptSelect.value));
    // Navigation: Wissen -> Wissensbasis, Bibliothek -> Vorlagen, Inbox -> Mandant
    const navWissen = document.getElementById("nav-wissen");
    if (navWissen) navWissen.addEventListener("click", openKnowledgeModal);
    const navBib = document.getElementById("nav-bibliothek");
    if (navBib) navBib.addEventListener("click", openPromptModal);
    const navInbox = document.getElementById("nav-inbox");
    if (navInbox) navInbox.addEventListener("click", () => {
        const sel = document.getElementById("inbox-select");
        if (sel) { sel.focus(); sel.scrollIntoView({ behavior: "smooth", block: "center" }); }
    });
    // Mandanten-Dropdown -> aktiver Mandant
    const inboxSel = document.getElementById("inbox-select");
    if (inboxSel) inboxSel.addEventListener("change", () => {
        if (!inboxSel.value) return;
        const mi = document.getElementById("matter-input");
        mi.value = inboxSel.value;
        loadDocuments();
        showToast(`Mandant: ${inboxSel.value} – Chat & Uploads beziehen sich jetzt auf diese Akte.`, "success");
    });

    // Knowledge-Base-Übersicht (Alt-Buttons, falls vorhanden)
    const kbBtn = document.getElementById("btn-knowledge");
    if (kbBtn) kbBtn.addEventListener("click", openKnowledgeModal);
    const closeKb = document.getElementById("btn-close-kb");
    if (closeKb) closeKb.addEventListener("click", () =>
        document.getElementById("kb-modal").style.display = "none");
    // #-Mention-Erkennung im Eingabefeld
    if (chatInput) chatInput.addEventListener("input", handleMentionInput);

    // Theme-Umschalter (Hell / Dunkel / System), pro User gespeichert
    const themeBtn = document.getElementById("btn-theme");
    if (themeBtn) {
        updateThemeButton();
        themeBtn.addEventListener("click", () => {
            const order = ["light", "dark", "system"];
            const cur = localStorage.getItem("anwalt_theme") || "system";
            const next = order[(order.indexOf(cur) + 1) % order.length];
            localStorage.setItem("anwalt_theme", next);
            document.documentElement.setAttribute("data-theme", next);
            updateThemeButton();
            showToast(`Design: ${ {light:'Hell', dark:'Dunkel', system:'System'}[next] }`, "info");
        });
    }

    // Prompt-Vorlage Auswahl + Verwaltung
    const manageBtn = document.getElementById("btn-manage-prompts");
    if (manageBtn) manageBtn.addEventListener("click", openPromptModal);
    const closeModal = document.getElementById("btn-close-modal");
    if (closeModal) closeModal.addEventListener("click", () => {
        document.getElementById("prompt-modal").style.display = "none";
    });
    const savePrompt = document.getElementById("btn-save-prompt");
    if (savePrompt) savePrompt.addEventListener("click", savePromptTemplate);

    // System-Übersicht
    const sysBtn = document.getElementById("btn-system-overview");
    if (sysBtn) sysBtn.addEventListener("click", openSystemModal);
    const closeSys = document.getElementById("btn-close-system");
    if (closeSys) closeSys.addEventListener("click", () => {
        document.getElementById("system-modal").style.display = "none";
    });

    // Diktat (Mikrofon)
    const micBtn = document.getElementById("btn-mic");
    if (micBtn) micBtn.addEventListener("click", toggleDictation);

    // Datei anhängen (Dokument -> Wissensbasis, MP3 -> Transkript)
    const attachBtn = document.getElementById("btn-attach");
    const chatFileInput = document.getElementById("chat-file-input");
    if (attachBtn && chatFileInput) {
        attachBtn.addEventListener("click", () => chatFileInput.click());
        chatFileInput.addEventListener("change", e => {
            if (e.target.files.length) handleChatFiles(e.target.files);
            chatFileInput.value = "";
        });
    }
    // Drag & Drop direkt auf den Chat (Dokumente + MP3)
    const dropZone = document.querySelector(".chat-input-container");
    if (dropZone) {
        ["dragenter", "dragover"].forEach(ev => dropZone.addEventListener(ev, e => {
            e.preventDefault(); dropZone.classList.add("drag-over");
        }));
        ["dragleave", "drop"].forEach(ev => dropZone.addEventListener(ev, e => {
            e.preventDefault(); dropZone.classList.remove("drag-over");
        }));
        dropZone.addEventListener("drop", e => {
            if (e.dataTransfer.files.length) handleChatFiles(e.dataTransfer.files);
        });
    }

    // Übersetzung
    const tBtn = document.getElementById("btn-translate");
    const tTarget = document.getElementById("translate-target");
    if (tBtn) tBtn.addEventListener("click", () => translateInput((tTarget && tTarget.value) || "de"));

    // File Upload Drag & Drop
    uploadZone.addEventListener("click", () => fileInput.click());
    
    fileInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) {
            handleFileUpload(e.target.files);
        }
    });

    ["dragenter", "dragover"].forEach(eventName => {
        uploadZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            uploadZone.classList.add("dragover");
        }, false);
    });

    ["dragleave", "drop"].forEach(eventName => {
        uploadZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            uploadZone.classList.remove("dragover");
        }, false);
    });

    uploadZone.addEventListener("drop", (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files.length > 0) {
            handleFileUpload(files);
        }
    });

    // Textarea auto-sizing & sending
    chatInput.addEventListener("input", () => {
        chatInput.style.height = "auto";
        chatInput.style.height = (chatInput.scrollHeight) + "px";
        btnSend.disabled = chatInput.value.trim() === "" || isGenerating;
    });

    chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (chatInput.value.trim() !== "" && !isGenerating) {
                sendMessage();
            }
        }
    });

    btnSend.addEventListener("click", sendMessage);

    // Neuer Chat / Chat leeren (Broom im Header + Button in der Navigation)
    if (btnClearChat) btnClearChat.addEventListener("click", clearChat);
    const ncBtn = document.getElementById("btn-clear-chat-2");
    if (ncBtn) ncBtn.addEventListener("click", clearChat);

    // Dokument-Schnellansicht: Klick auf eine Quellen-Citation öffnet das Popup
    document.addEventListener("click", (e) => {
        const badge = e.target.closest(".citation-badge[data-doc]");
        if (badge) showDocPreview(badge.dataset.doc);
    });
    // Popup schließen (X, Hintergrund-Klick, ESC)
    const dpOverlay = document.getElementById("doc-preview-overlay");
    const dpClose = document.getElementById("doc-preview-close");
    if (dpClose) dpClose.addEventListener("click", hideDocPreview);
    if (dpOverlay) dpOverlay.addEventListener("click", (e) => { if (e.target === dpOverlay) hideDocPreview(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideDocPreview(); });

    // Aktuelle Akte löschen (DSGVO Art. 17)
    const btnDeleteMatter = document.getElementById("btn-delete-matter");
    if (btnDeleteMatter) btnDeleteMatter.addEventListener("click", deleteCurrentMatter);
}

// Komplette aktuelle Akte löschen
async function deleteCurrentMatter() {
    const matter = getMatter();
    if (!confirm(`Die gesamte Akte "${matter}" mit allen Dokumenten unwiderruflich löschen?`)) return;
    try {
        const response = await fetch("/api/delete", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ matter_id: matter })
        });
        const result = await response.json();
        if (response.ok) {
            showToast(result.message || `Akte ${matter} gelöscht.`, "success");
            loadDocuments();
        } else {
            showToast(result.detail || "Fehler beim Löschen der Akte.", "error");
        }
    } catch (error) {
        showToast("Netzwerkfehler beim Löschen der Akte.", "error");
    }
}

// Ingest Documents (Upload)
async function handleFileUpload(files) {
    files = filterUploadable(files);
    if (!files.length) return;
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
        formData.append("files", files[i]);
    }
    formData.append("matter_id", getMatter());
    const scopeEl = document.getElementById("upload-scope");
    const lawEl = document.getElementById("upload-lawbook");
    formData.append("scope", (scopeEl && scopeEl.value) || "mandat");
    formData.append("law_book", (lawEl && lawEl.value.trim()) || "");

    showToast("Dokumente werden lokal hochgeladen und verarbeitet...", "info");
    uploadZone.style.opacity = "0.5";
    uploadZone.style.pointerEvents = "none";

    try {
        const response = await fetch("/api/ingest", {
            method: "POST",
            headers: authHeaders(),
            body: formData
        });
        const result = await response.json();
        
        if (response.ok) {
            showToast(result.message || "Dateien erfolgreich eingelesen!", "success");
            renderDocList(result.documents);
        } else {
            showToast(result.detail || "Fehler beim Einlesen.", "error");
        }
    } catch (error) {
        showToast("Netzwerkfehler beim Hochladen.", "error");
        console.error(error);
    } finally {
        uploadZone.style.opacity = "1";
        uploadZone.style.pointerEvents = "auto";
        fileInput.value = "";
    }
}

// Get Ingested Documents
async function loadDocuments() {
    try {
        const response = await fetch(`/api/documents?matter_id=${encodeURIComponent(getMatter())}`, {
            headers: authHeaders()
        });
        if (response.status === 401) {
            renderDocList([]);
            return;
        }
        const data = await response.json();
        renderDocList(data.documents);
        loadMatters();  // Mandanten-Dropdown aktuell halten
    } catch (error) {
        console.error("Error loading docs:", error);
    }
}

// Render Document List in Sidebar
function renderDocList(documents) {
    if (!documents || documents.length === 0) {
        docList.innerHTML = `
            <div class="empty-docs-state">
                <i class="fa-regular fa-folder"></i>
                <p>Keine Dokumente geladen</p>
            </div>
        `;
        return;
    }

    docList.innerHTML = documents.map(doc => `
        <li class="doc-item" title="Klicken zum Ansehen: ${esc(doc)}">
            <div class="doc-info" data-doc="${esc(doc)}" role="button" tabindex="0">
                <i class="fa-solid fa-file-invoice"></i>
                <span class="doc-name">${esc(doc)}</span>
            </div>
            <button class="doc-delete" data-doc="${esc(doc)}" title="Dokument löschen (DSGVO Art. 17)">
                <i class="fa-solid fa-trash-can"></i>
            </button>
        </li>
    `).join("");

    docList.querySelectorAll(".doc-info").forEach(el => {
        el.addEventListener("click", () => showDocPreview(el.dataset.doc));
        // role="button" tabindex="0" verspricht Tastaturbedienung -> einlösen.
        el.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showDocPreview(el.dataset.doc); }
        });
    });
    docList.querySelectorAll(".doc-delete").forEach(btn => {
        btn.addEventListener("click", (e) => { e.stopPropagation(); deleteDocument(btn.dataset.doc); });
    });
}

// Dokument-Schnellansicht (Popup): lädt den Klartext und zeigt ihn an.
async function showDocPreview(source) {
    const overlay = document.getElementById("doc-preview-overlay");
    const titleEl = document.getElementById("doc-preview-title");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!overlay) return;
    titleEl.textContent = source;
    bodyEl.innerHTML = `<div class="pipe-status"><span class="pipe-spinner"></span> Lädt…</div>`;
    overlay.style.display = "flex";
    try {
        const url = `/api/document?source=${encodeURIComponent(source)}&matter_id=${encodeURIComponent(getMatter())}`;
        const res = await fetch(url, { headers: authHeaders() });
        const r = await res.json();
        if (!res.ok) { bodyEl.textContent = r.detail || "Dokument konnte nicht geladen werden."; return; }
        // Klartext sicher anzeigen (kein HTML-Rendering von Dokumentinhalt)
        bodyEl.textContent = r.text || "(leer)";
    } catch (e) {
        bodyEl.textContent = "Netzwerkfehler beim Laden des Dokuments.";
    }
}

function hideDocPreview() {
    const overlay = document.getElementById("doc-preview-overlay");
    if (overlay) overlay.style.display = "none";
}

// Einzelnes Dokument löschen (DSGVO Art. 17)
async function deleteDocument(source) {
    if (!confirm(`Dokument "${source}" unwiderruflich löschen?`)) return;
    try {
        const response = await fetch("/api/delete", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ source, matter_id: getMatter() })
        });
        const result = await response.json();
        if (response.ok) {
            showToast(result.message || "Dokument gelöscht.", "success");
            loadDocuments();
        } else {
            showToast(result.detail || "Fehler beim Löschen.", "error");
        }
    } catch (error) {
        showToast("Netzwerkfehler beim Löschen.", "error");
    }
}

// Reset Local Vector DB
async function resetDatabase() {
    if (!confirm("Möchtest du wirklich alle Dokumente aus der lokalen Vektordatenbank löschen?")) {
        return;
    }

    try {
        const response = await fetch("/api/clear", { method: "POST", headers: authHeaders() });
        const result = await response.json();
        if (response.ok) {
            showToast("Vektordatenbank erfolgreich geleert.", "success");
            loadDocuments();
        } else {
            showToast(result.detail || "Fehler beim Zurücksetzen.", "error");
        }
    } catch (error) {
        showToast("Netzwerkfehler beim Zurücksetzen.", "error");
    }
}

// Send Message & Stream RAG response
async function sendMessage() {
    const queryText = chatInput.value.trim();
    if (queryText === "" || isGenerating) return;

    isGenerating = true;
    btnSend.disabled = true;
    chatInput.value = "";
    chatInput.style.height = "auto";
    welcomeScreen.style.display = "none";
    contextPanel.style.display = "none";

    // 1. Render User Message
    appendMessage(queryText, "user");

    // 2. Render Agent Placeholder + Typing Indicator
    const agentMsgDiv = appendMessage("", "agent");
    const bubble = agentMsgDiv.querySelector(".bubble");
    bubble.innerHTML = `
        <div class="typing-indicator" id="typing">
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
        </div>
    `;

    messageFeed.scrollTop = messageFeed.scrollHeight;

    try {
        const response = await fetch("/api/chat", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
                query: queryText,
                model: selectedModel,
                matter_id: getMatter(),
                prompt_id: (document.getElementById("prompt-select") || {}).value || null,
                web_research: !!(document.getElementById("web-research-toggle") || {}).checked,
                scopes: getActiveScopes(),
                mentions: collectMentionRefs(queryText)
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || "Unbekannter Fehler beim Generieren.");
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let partialText = "", buffer = "", answerStarted = false;
        let citations = [], webResults = [];
        const HEADS = ["[STATUS]: ", "[METADATA]: "];
        const waitingForEnvelope = b =>
            HEADS.some(h => h.startsWith(b)) ||
            ((b.startsWith("[STATUS]:") || b.startsWith("[METADATA]:")) && !b.includes("\n\n"));

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            if (!answerStarted) {
                let m;
                const ENV = /^\[(STATUS|METADATA)\]: ([\s\S]*?)\n\n/;
                while ((m = buffer.match(ENV))) {
                    if (m[1] === "STATUS") {
                        setBubbleStatus(bubble, m[2].trim());
                    } else {
                        try {
                            const meta = JSON.parse(m[2]);
                            citations = meta.citations || [];
                            webResults = meta.web || [];
                            renderCitationsPanel(citations);
                            if (meta.model) renderModelBadge(agentMsgDiv, meta.model);
                        } catch (e) { console.error("metadata parse:", e); }
                    }
                    buffer = buffer.slice(m[0].length);
                }
                if (buffer && !waitingForEnvelope(buffer)) {
                    answerStarted = true;
                    partialText += buffer; buffer = "";
                    updateAgentBubble(bubble, partialText, citations);
                }
            } else {
                partialText += buffer; buffer = "";
                updateAgentBubble(bubble, partialText, citations);
            }
            messageFeed.scrollTop = messageFeed.scrollHeight;
        }
        if (!answerStarted && buffer.trim()) {
            updateAgentBubble(bubble, buffer, citations);
            answerStarted = true;
        }
        // Garantiert den Spinner ersetzen, falls das Modell 0 Tokens geliefert hat.
        if (!answerStarted && !partialText.trim()) {
            updateAgentBubble(bubble, "_(Keine Antwort erhalten.)_", citations);
        }
        if (webResults.length) renderWebSources(agentMsgDiv, webResults);

    } catch (error) {
        bubble.innerHTML = `<span style="color: var(--accent-red);"><i class="fa-solid fa-triangle-exclamation"></i> Fehler: ${error.message}</span>`;
        showToast("Fehler bei der Kommunikation mit dem Server.", "error");
    } finally {
        isGenerating = false;
        btnSend.disabled = chatInput.value.trim() === "";
    }
}

// Append message block to feed
function appendMessage(text, sender) {
    const msgDiv = document.createElement("div");
    msgDiv.className = `message ${sender}`;
    
    msgDiv.innerHTML = `
        <span class="message-label">${sender === 'user' ? 'Mandant' : 'Amicus AI'}</span>
        <div class="bubble"></div>
    `;

    const bubble = msgDiv.querySelector(".bubble");
    if (sender === "user") {
        bubble.textContent = text;
    } else {
        bubble.innerHTML = text;
    }

    messageFeed.appendChild(msgDiv);
    messageFeed.scrollTop = messageFeed.scrollHeight;
    return msgDiv;
}

// Liest die aktuelle Modellauswahl aus dem Dropdown (für Übersetzung etc.)
function getSelectedModelInfo() {
    const sel = document.getElementById("model-select");
    const opt = sel && sel.selectedOptions[0];
    if (!opt) return null;
    const cloud = opt.dataset.cloud === "true";
    return {
        label: opt.textContent.replace(/\s*\((?:Cloud|lokal)\)\s*$/, "").trim(),
        backend: cloud ? "AWS Bedrock" : "Lokal (ATOM/GB10)",
        cloud: cloud,
    };
}

// Modell-Badge an der Antwort anzeigen: welches Modell + lokal/AWS Bedrock
// Kennzeichnung: Antwort wurde mit Web-Recherche angereichert (+ Quell-Links)
function renderWebSources(msgDiv, web) {
    if (!msgDiv || !web || !web.length) return;
    const label = msgDiv.querySelector(".message-label");
    if (label && !label.querySelector(".web-badge")) {
        const b = document.createElement("span");
        b.className = "web-badge";
        b.innerHTML = `<i class="fa-solid fa-globe"></i> Web-recherchiert (${web.length})`;
        label.appendChild(b);
    }
    const bubble = msgDiv.querySelector(".bubble");
    if (bubble && !bubble.querySelector(".web-sources")) {
        const div = document.createElement("div");
        div.className = "web-sources";
        div.innerHTML = `<div class="web-sources-title"><i class="fa-solid fa-globe"></i> Externe Web-Quellen (öffentlich)</div>` +
            web.map((w, i) => `<a class="web-source" href="${esc(w.url)}" target="_blank" rel="noopener noreferrer">[W${i + 1}] ${esc(w.title || w.url)}</a>`).join("");
        bubble.appendChild(div);
    }
}

function renderModelBadge(msgDiv, model) {
    if (!msgDiv || !model) return;
    const label = msgDiv.querySelector(".message-label");
    if (!label || label.querySelector(".model-badge")) return;
    // Cloud-Modelle laufen in AWS Bedrock, Region eu-central-1 (Frankfurt).
    const isBedrock = model.cloud && /bedrock/i.test(model.backend || "");
    const region = isBedrock ? "EU Frankfurt" : (model.cloud ? "EU" : "lokal/ATOM");
    const badge = document.createElement("span");
    badge.className = "model-badge";
    badge.innerHTML =
        `<i class="fa-solid fa-microchip"></i> ${esc(model.label)} · ${esc(region)} · ${esc(model.backend)}`;
    label.appendChild(badge);
}

// Live-Pipeline-Status in der Antwort-Bubble (zeigt, worauf gewartet wird)
function setBubbleStatus(bubbleElement, text) {
    bubbleElement.innerHTML =
        `<div class="pipe-status"><span class="pipe-spinner"></span> ${esc(text)}</div>`;
}

// Update Agent Bubble with Stream + Citations
function updateAgentBubble(bubbleElement, text, citations) {
    // Eigener Textcontainer, damit pro Stream-Chunk NUR der Markdown-Teil neu
    // gerendert wird und die (unveränderlichen) Citations nicht jedes Mal neu
    // aufgebaut werden müssen.
    let textDiv = bubbleElement.querySelector(".bubble-text");
    if (!textDiv) {
        bubbleElement.innerHTML = "";            // entfernt Spinner/Typing-Indicator
        textDiv = document.createElement("div");
        textDiv.className = "bubble-text";
        bubbleElement.appendChild(textDiv);
    }
    // Render markdown, aber gegen XSS bereinigt (LLM-/Dokumentinhalt nie roh trauen)
    textDiv.innerHTML = DOMPurify.sanitize(marked.parse(text));

    // Citations nur EINMAL rendern (kommen einmalig per METADATA, ändern sich nicht).
    if (citations && citations.length > 0 && !bubbleElement.querySelector(".message-citations")) {
        const citationsDiv = document.createElement("div");
        citationsDiv.className = "message-citations";
        citationsDiv.innerHTML = citations.map(c => `
            <span class="citation-badge" data-doc="${esc(c.source)}" title="Klicken zum Ansehen: ${esc(c.source)}">
                <i class="fa-solid fa-file-lines"></i> [${esc(c.index)}] ${esc(c.source)}${c.section ? " · " + esc([c.law_book, c.section, c.absatz].filter(Boolean).join(" ")) : ""} (S. ${esc(c.page)})
            </span>
        `).join("");
        bubbleElement.appendChild(citationsDiv);
    }
}

// Show active citations in context preview panel below input bar
function renderCitationsPanel(citations) {
    if (!citations || citations.length === 0) {
        contextPanel.style.display = "none";
        return;
    }

    contextPanel.style.display = "block";
    citationsBadges.innerHTML = citations.map(c => `
        <span class="citation-badge" data-doc="${esc(c.source)}" title="Klicken zum Ansehen: ${esc(c.source)}">
            <i class="fa-solid fa-file-lines"></i> [${esc(c.index)}] ${esc(c.source)}${c.section ? " · " + esc([c.law_book, c.section, c.absatz].filter(Boolean).join(" ")) : ""} (S. ${esc(c.page)})
        </span>
    `).join("");
}

// ===================== Diktat (lokale Transkription) =====================
let mediaRecorder = null;
let audioChunks = [];

async function toggleDictation() {
    const micBtn = document.getElementById("btn-mic");
    if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
        return;
    }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(t => t.stop());
            micBtn.classList.remove("recording");
            const blob = new Blob(audioChunks, { type: "audio/webm" });
            await uploadDictation(blob);
        };
        mediaRecorder.start();
        micBtn.classList.add("recording");
        showToast("Aufnahme läuft… nochmal klicken zum Stoppen.", "info");
    } catch (e) {
        showToast("Mikrofonzugriff verweigert oder nicht verfügbar.", "error");
    }
}

async function uploadDictation(blob, filename) {
    showToast("Diktat wird lokal transkribiert…", "info");
    const fd = new FormData();
    fd.append("file", blob, filename || "diktat.webm");
    fd.append("language", "de");
    try {
        const res = await fetch("/api/transcribe", { method: "POST", headers: authHeaders(), body: fd });
        const data = await res.json();
        if (res.ok) {
            const existing = chatInput.value.trim();
            chatInput.value = existing ? existing + " " + data.text : data.text;
            chatInput.dispatchEvent(new Event("input"));
            chatInput.focus();
            showToast("Diktat transkribiert.", "success");
        } else {
            showToast(data.detail || "Transkription fehlgeschlagen.", "error");
        }
    } catch (e) {
        showToast("Netzwerkfehler bei der Transkription.", "error");
    }
}

// ===================== Übersetzung =====================
async function translateInput(targetLang) {
    const text = chatInput.value.trim();
    if (!text) { showToast("Bitte zuerst Text eingeben.", "info"); return; }
    appendMessage(text, "user");
    const agentMsgDiv = appendMessage("", "agent");
    const bubble = agentMsgDiv.querySelector(".bubble");
    renderModelBadge(agentMsgDiv, getSelectedModelInfo());
    bubble.textContent = "Übersetze…";
    chatInput.value = "";
    chatInput.dispatchEvent(new Event("input"));  // Höhe + Senden-Button zurücksetzen
    welcomeScreen.style.display = "none";
    try {
        const res = await fetch("/api/translate", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ text, target_lang: targetLang, model: selectedModel })
        });
        if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Fehler"); }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let out = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            out += decoder.decode(value, { stream: true });
            bubble.innerHTML = DOMPurify.sanitize(marked.parse(out));
            messageFeed.scrollTop = messageFeed.scrollHeight;
        }
        if (!out.trim()) bubble.textContent = "(Keine Übersetzung erhalten.)";
    } catch (e) {
        bubble.innerHTML = `<span style="color:var(--accent-red);">Fehler: ${esc(e.message)}</span>`;
    }
}

// ===================== Prompt-Verwaltung (Modal) =====================
function openPromptModal() {
    document.getElementById("prompt-modal").style.display = "flex";
    renderPromptAdmin();
}

function renderPromptAdmin() {
    const list = document.getElementById("prompt-admin-list");
    const prompts = window._prompts || [];
    list.innerHTML = prompts.map(p => `
        <li class="prompt-admin-item">
            <div><strong>${esc(p.title)}</strong> <span class="muted">(${esc(p.id)})</span></div>
            <div class="prompt-admin-actions">
                <button class="toolbar-btn" data-edit="${esc(p.id)}"><i class="fa-solid fa-pen"></i></button>
                <button class="toolbar-btn" data-del="${esc(p.id)}"><i class="fa-solid fa-trash"></i></button>
            </div>
        </li>`).join("");
    list.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () => fillPromptForm(b.dataset.edit)));
    list.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", () => deletePromptTemplate(b.dataset.del)));
}

function fillPromptForm(id) {
    const p = (window._prompts || []).find(x => x.id === id);
    if (!p) return;
    document.getElementById("pa-id").value = p.id;
    document.getElementById("pa-title").value = p.title || "";
    document.getElementById("pa-category").value = p.category || "";
    document.getElementById("pa-system").value = p.system || "";
    document.getElementById("pa-template").value = p.template || "";
}

async function savePromptTemplate() {
    const payload = {
        id: document.getElementById("pa-id").value.trim(),
        title: document.getElementById("pa-title").value.trim(),
        category: document.getElementById("pa-category").value.trim(),
        system: document.getElementById("pa-system").value,
        template: document.getElementById("pa-template").value
    };
    if (!payload.id || !payload.template) { showToast("ID und Template sind erforderlich.", "error"); return; }
    try {
        const res = await fetch("/api/prompts", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            showToast("Vorlage gespeichert.", "success");
            await loadPrompts();
            renderPromptAdmin();
        } else { showToast(data.detail || "Fehler beim Speichern.", "error"); }
    } catch (e) { showToast("Netzwerkfehler.", "error"); }
}

async function deletePromptTemplate(id) {
    if (!confirm(`Vorlage "${id}" löschen? (Standardvorlagen werden ggf. wiederhergestellt.)`)) return;
    try {
        const res = await fetch("/api/prompts/delete", {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ id })
        });
        if (res.ok) { showToast("Vorlage gelöscht.", "success"); await loadPrompts(); renderPromptAdmin(); }
    } catch (e) { showToast("Netzwerkfehler.", "error"); }
}

// ===================== System-Übersicht (Info-Popup) =====================
async function openSystemModal() {
    const modal = document.getElementById("system-modal");
    const body = document.getElementById("system-modal-body");
    modal.style.display = "flex";
    body.innerHTML = "<p class='muted'>Lade Systemdaten…</p>";

    let status = {}, models = [];
    try {
        const [s, m] = await Promise.all([
            fetch("/api/status", { headers: authHeaders() }).then(r => r.ok ? r.json() : {}),
            fetch("/api/models", { headers: authHeaders() }).then(r => r.ok ? r.json() : { models: [] }),
        ]);
        status = s; models = m.models || [];
    } catch (e) { /* zeige trotzdem statische Infos */ }

    const yes = "<span class='sys-ok'>aktiv</span>";
    const no = "<span class='sys-off'>aus</span>";
    const flag = v => v ? yes : no;
    // Live-Indikator mit grünem/rotem Punkt (öffnet <li>, Template schließt es)
    const dot = v => `<li class="${v ? "on" : "off"}"><span class="sec-dot"></span>`;
    const modelRows = models.map(m =>
        `<tr><td>${esc(m.label)}</td><td>${esc(m.provider)}</td><td>${m.cloud ? "Cloud" : "Lokal"}</td></tr>`
    ).join("") || "<tr><td colspan='3' class='muted'>API-Key eingeben, um Modelle zu laden</td></tr>";

    body.innerHTML = `
    <section class="sys-card sys-diagram-card">
      <h3><i class="fa-solid fa-network-wired"></i> Datenströme</h3>
      <div class="region-badge" id="region-status"><span class="rdot pending"></span> AWS Frankfurt: prüfe…</div>
      ${dataFlowSVG()}
    </section>
    <div class="sys-grid">
      <section class="sys-card">
        <h3><i class="fa-solid fa-diagram-project"></i> Ablauf (Schritte)</h3>
        <ol class="sys-flow">
          <li><b>Lokal:</b> Dokument → Parsing → <b>artikel-bewusstes Chunking</b> → Embedding (bge-m3) → LanceDB</li>
          <li><b>Lokal:</b> Frage → Vektorsuche (Akte-gefiltert) → relevante Norm-Chunks</li>
          <li><b>Lokal:</b> Pseudonymisierung der PII vor Versand</li>
          <li><b>Cloud/Lokal:</b> LLM erzeugt Antwort (nur Platzhalter sichtbar)</li>
          <li><b>Lokal:</b> Re-Identifizierung → Anzeige mit Quellen & Norm</li>
        </ol>
      </section>

      <section class="sys-card">
        <h3><i class="fa-solid fa-shield-halved"></i> Datenschutz-Status</h3>
        <ul class="security-list">
          ${dot(status.local_processing)} Lokale Verarbeitung</li>
          ${dot(status.pseudonymize)} Pseudonymisierung</li>
          ${dot(status.ner_active)} Namens-Erkennung (NER)</li>
          ${dot(status.matter_separation)} Mandantentrennung</li>
          ${dot(status.transport_tls)} TLS-Transport</li>
          ${dot(status.audit_log)} Audit-Log</li>
          ${dot(status.encryption_at_rest)} Verschlüsselung at rest${status.encryption_label ? " · " + esc(status.encryption_label) : ""}</li>
          ${dot(status.transcription)} Lokale Transkription</li>
        </ul>
      </section>

      <section class="sys-card">
        <h3><i class="fa-solid fa-brain"></i> Modelle (Multi-Provider)</h3>
        <table class="sys-table"><thead><tr><th>Modell</th><th>Provider</th><th>Ort</th></tr></thead>
        <tbody>${modelRows}</tbody></table>
        <p class="muted">Region: ${esc(status.region || "—")} · Embedding: ${esc(status.embed_model || "—")}</p>
      </section>

      <section class="sys-card">
        <h3><i class="fa-solid fa-magnifying-glass"></i> RAG-Pipeline (präzise Referenzen)</h3>
        <ul class="sys-list">
          <li>Vektor-DB: <b>LanceDB</b> (lokal, serverlos)</li>
          <li>Embedding: <b>BAAI/bge-m3</b> (mehrsprachig, deutschtauglich)</li>
          <li>Reranker: <b>BAAI/bge-reranker-v2-m3</b> (Hybridsuche + Cross-Encoder)</li>
          <li><b>Artikel-/§-bewusstes Chunking</b>: jede Norm trägt ihre Überschrift</li>
          <li>Jede Antwort verweist auf <b>Quelle + exakte Norm</b> (Anti-Halluzination)</li>
          <li>Akte-gefilterte Suche (Trennungsgebot)</li>
        </ul>
      </section>

      <section class="sys-card">
        <h3><i class="fa-solid fa-list-check"></i> Funktionen</h3>
        <ul class="sys-list">
          <li>Multi-Provider-Chat mit Quellenangabe</li>
          <li>Prompt-Vorlagen (anlegbar)</li>
          <li>Diktat (Handy/PC) → lokale Transkription</li>
          <li>Übersetzung DE↔EN</li>
          <li>DSGVO-sichere Web-Recherche</li>
          <li>Löschung pro Dokument/Akte (Art. 17)</li>
        </ul>
      </section>

      <section class="sys-card">
        <h3><i class="fa-solid fa-microchip"></i> Betrieb / Zukunft</h3>
        <ul class="sys-list">
          <li>Cloud: AWS Bedrock (eu-central-1, Frankfurt) – alles EU</li>
          <li>On-Prem: GIGABYTE AI TOP ATOM (NVIDIA GB10 Blackwell, ARM64)</li>
          <li>Lokales LLM via OpenAI-kompatiblen Endpoint (ollama) einbindbar</li>
        </ul>
      </section>

      <section class="sys-card" style="grid-column:1/-1;">
        <h3><i class="fa-solid fa-coins"></i> Verbrauch & Budget (AWS Bedrock)</h3>
        <div id="usage-panel"><p class="muted">Lade Verbrauchsdaten…</p></div>
      </section>
    </div>`;

    // Live-Status der AWS-Region Frankfurt (Latenz als Last-Proxy)
    fetch("/api/region-status", { headers: authHeaders() })
        .then(r => r.ok ? r.json() : null)
        .then(s => {
            const el = document.getElementById("region-status");
            if (!el || !s) return;
            if (s.online) {
                el.innerHTML = `<span class="rdot online"></span> AWS Frankfurt (${esc(s.region)}): online · ${s.latency_ms} ms · Last ${esc(s.load)}`;
            } else if (s.mode === "local-only") {
                // Reiner On-Prem-Betrieb (ATOM): Cloud bewusst nicht konfiguriert – kein Fehler.
                el.innerHTML = `<span class="rdot pending"></span> Nur lokal (ATOM) · Cloud nicht konfiguriert`;
            } else {
                el.innerHTML = `<span class="rdot off"></span> AWS Frankfurt: nicht erreichbar`;
            }
            // Watcher: neue Claude-Modelle mit EU-Profil (DSGVO-tauglich erst mit eu.*)
            if (s.upcoming_eu_models && s.upcoming_eu_models.length) {
                const parts = s.upcoming_eu_models.map(m =>
                    `${esc(m.label)}: ${m.eu_profile ? "EU-Profil verfuegbar" : "noch kein EU-Profil (nur global)"}`);
                el.insertAdjacentHTML("afterend",
                    `<p class="muted" style="margin-top:6px;">Neue Modelle · ${parts.join(" · ")}</p>`);
            }
        }).catch(() => {});

    // Verbrauch & Budget laden (lokale Tokens/Kosten pro Modell + echtes AWS-Budget)
    fetch("/api/usage", { headers: authHeaders() })
        .then(r => r.ok ? r.json() : null)
        .then(d => renderUsagePanel(d))
        .catch(() => { const p = document.getElementById("usage-panel"); if (p) p.innerHTML = "<p class='muted'>Verbrauchsdaten nicht verfügbar.</p>"; });
}

function renderUsagePanel(d) {
    const p = document.getElementById("usage-panel");
    if (!p) return;
    if (!d || !d.usage) { p.innerHTML = "<p class='muted'>Verbrauchsdaten nicht verfügbar.</p>"; return; }
    const u = d.usage, b = d.budget || {};
    const fmtUsd = v => "$" + (Number(v) || 0).toFixed(4);
    const fmtTok = v => (Number(v) || 0).toLocaleString("de-DE");

    const rows = (u.month_models || []).map(m => `
        <tr><td>${esc(m.label)}</td><td>${m.requests}</td>
        <td>${fmtTok(m.in_tokens)}</td><td>${fmtTok(m.out_tokens)}</td>
        <td>${fmtUsd(m.est_cost_usd)}</td></tr>`).join("")
        || "<tr><td colspan='5' class='muted'>Noch keine Anfragen in diesem Monat.</td></tr>";

    // AWS-Budget (echt) – nur wenn in AWS Budgets gesetzt & IAM-Recht vorhanden
    let budgetHtml;
    if (b.available) {
        const pct = b.limit ? Math.min(100, Math.round((b.actual / b.limit) * 100)) : 0;
        budgetHtml = `
          <div class="kb-meter"><span>AWS-Budget „${esc(b.name || "")}" (${esc(b.time_unit || "MONTHLY")})</span>
            <b>${esc(b.currency)} ${(+b.actual).toFixed(2)} / ${(+b.limit).toFixed(2)}</b></div>
          <div class="kb-bar"><div style="width:${pct}%"></div></div>
          <div class="kb-meter"><span>Verbleibend</span><b>${esc(b.currency)} ${(+b.remaining).toFixed(2)}</b></div>
          ${b.forecast != null ? `<div class="kb-meter"><span>Prognose Monatsende</span><b>${esc(b.currency)} ${(+b.forecast).toFixed(2)}</b></div>` : ""}`;
    } else {
        budgetHtml = `<p class="muted">AWS-Budget: ${esc(b.reason || "nicht verfügbar")}
          <br>(In AWS Budgets ein Limit setzen bzw. IAM-Recht <code>budgets:ViewBudget</code> vergeben.)</p>`;
    }

    p.innerHTML = `
      <table class="sys-table">
        <thead><tr><th>Modell</th><th>Anfragen</th><th>Input-Tok.</th><th>Output-Tok.</th><th>Kosten (Schätzung)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="kb-meter"><span>Summe Monat (${esc(u.month)}) · geschätzt</span><b>${fmtUsd(u.month_cost_usd)}</b></div>
      <div class="kb-meter"><span>Summe gesamt · geschätzt</span><b>${fmtUsd(u.total_cost_usd)}</b></div>
      <hr class="modal-sep">
      ${budgetHtml}
      <p class="muted">${esc(u.prices_note)} Lokale Token-Zählung dieser App; AWS-Budget ist der echte Kontostand aus AWS Budgets.</p>
    `;
}

// SVG-Schaubild der Datenströme: Kanzlei (lokal) <-> AWS Bedrock Frankfurt
function dataFlowSVG() {
    return `
    <svg viewBox="0 0 760 230" class="flow-svg" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="arr" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
          <path d="M0,0 L7,3 L0,6 Z" fill="var(--accent-blue)"/>
        </marker>
      </defs>
      <!-- Lokaler Rechner -->
      <rect x="10" y="20" width="320" height="190" rx="12" fill="rgba(16,185,129,0.06)" stroke="var(--accent-green)" stroke-width="1.5"/>
      <text x="28" y="44" fill="var(--accent-green)" font-size="13" font-weight="700">Kanzlei-Rechner / ATOM (lokal)</text>
      ${['Dokument · Parsing','Artikel-Chunking','bge-m3-Embedding','LanceDB (Vektor+Volltext)','Reranker (bge-v2-m3)','Pseudonymisierung'].map((t,i)=>{
        const x=28+(i%2)*150, y=66+Math.floor(i/2)*40;
        return `<rect x="${x}" y="${y}" width="142" height="30" rx="6" fill="var(--bg-card)" stroke="var(--border-color)"/><text x="${x+8}" y="${y+19}" fill="var(--text-secondary)" font-size="10.5">${t}</text>`;
      }).join('')}
      <!-- AWS Frankfurt -->
      <rect x="470" y="50" width="280" height="130" rx="12" fill="rgba(59,130,246,0.06)" stroke="var(--accent-blue)" stroke-width="1.5"/>
      <text x="488" y="76" fill="var(--accent-blue)" font-size="13" font-weight="700">AWS Bedrock · Frankfurt</text>
      <text x="488" y="94" fill="var(--text-muted)" font-size="10.5">eu-central-1</text>
      ${['Claude Opus/Sonnet','Amazon Nova','Mistral Pixtral'].map((t,i)=>`<rect x="488" y="${104+i*22}" width="244" height="18" rx="5" fill="var(--bg-card)" stroke="var(--border-color)"/><text x="496" y="${117+i*22}" fill="var(--text-secondary)" font-size="10">${t}</text>`).join('')}
      <!-- Flüsse -->
      <line x1="330" y1="100" x2="468" y2="100" stroke="var(--accent-blue)" stroke-width="2" stroke-dasharray="6 5" marker-end="url(#arr)" class="flow-line"/>
      <text x="345" y="92" fill="var(--text-secondary)" font-size="10">nur Platzhalter · TLS →</text>
      <line x1="468" y1="140" x2="330" y2="140" stroke="var(--accent-green)" stroke-width="2" stroke-dasharray="6 5" marker-end="url(#arr)" class="flow-line back"/>
      <text x="345" y="158" fill="var(--text-secondary)" font-size="10">← Antwort (Re-ID lokal)</text>
    </svg>`;
}

// ===================== Scopes (Wissensquellen) =====================
function getActiveScopes() {
    const s = [];
    if ((document.getElementById("scope-allgemein") || {}).checked) s.push("allgemein");
    if ((document.getElementById("scope-rechtsprechung") || {}).checked) s.push("rechtsprechung");
    return s;
}

// ===================== #-Mentions (exakte Normen) =====================
window._mentionMap = window._mentionMap || {};

function collectMentionRefs(text) {
    const refs = [];
    const re = /#\[([^\]]+)\]/g;
    let m;
    while ((m = re.exec(text)) !== null) {
        const ref = window._mentionMap[m[1]];
        if (ref) refs.push(ref);
    }
    return [...new Set(refs)];
}

let _mentionStart = -1;
async function handleMentionInput() {
    const el = chatInput;
    const pos = el.selectionStart;
    const before = el.value.slice(0, pos);
    const m = before.match(/#([^#\n\[\]]{0,40})$/);
    const box = document.getElementById("mention-box");
    if (!m) { box.style.display = "none"; _mentionStart = -1; return; }
    _mentionStart = pos - m[0].length;
    const q = m[1].trim();
    try {
        const res = await fetch(`/api/sections?q=${encodeURIComponent(q)}&matter_id=${encodeURIComponent(getMatter())}`,
            { headers: authHeaders() });
        if (!res.ok) { box.style.display = "none"; return; }
        const data = await res.json();
        if (!data.sections.length) { box.style.display = "none"; return; }
        box.innerHTML = data.sections.map(s =>
            `<div class="mention-item" data-ref="${esc(s.ref)}" data-label="${esc(s.label)}">
                <i class="fa-solid fa-section"></i> ${esc(s.label)}
             </div>`).join("");
        box.querySelectorAll(".mention-item").forEach(it =>
            it.addEventListener("click", () => insertMention(it.dataset.label, it.dataset.ref)));
        box.style.display = "block";
    } catch (e) { box.style.display = "none"; }
}

function insertMention(label, ref) {
    window._mentionMap[label] = ref;
    const el = chatInput;
    const pos = el.selectionStart;
    const start = _mentionStart >= 0 ? _mentionStart : pos;
    el.value = el.value.slice(0, start) + `#[${label}] ` + el.value.slice(pos);
    document.getElementById("mention-box").style.display = "none";
    el.focus();
    el.dispatchEvent(new Event("input"));
}

// ===================== Knowledge-Base-Übersicht =====================
function fmtBytes(b) {
    if (!b) return "0 B";
    const u = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(b) / Math.log(1024));
    return (b / Math.pow(1024, i)).toFixed(i ? 1 : 0) + " " + u[i];
}

async function openKnowledgeModal() {
    const modal = document.getElementById("kb-modal");
    const body = document.getElementById("kb-modal-body");
    modal.style.display = "flex";
    body.innerHTML = "<p class='muted'>Lade Wissensbasis…</p>";
    try {
        const res = await fetch("/api/knowledge", { headers: authHeaders() });
        if (!res.ok) { body.innerHTML = "<p class='muted'>API-Key erforderlich.</p>"; return; }
        const { overview: ov, system: sys } = await res.json();

        const fileRows = arr => arr.length
            ? arr.map(f => `<li><i class="fa-solid fa-file-lines"></i> ${esc(f.source)}
                ${f.law_book ? `<span class="muted">· ${esc(f.law_book)}</span>` : ""}
                <span class="kb-chunks">${f.chunks} Chunks</span></li>`).join("")
            : "<li class='muted'>— keine —</li>";
        const mandate = Object.entries(ov.mandate || {});
        const mandateHtml = mandate.length
            ? mandate.map(([mt, files]) => `<div class="kb-matter"><b>Akte ${esc(mt)}</b><ul class="kb-list">${fileRows(files)}</ul></div>`).join("")
            : "<p class='muted'>— keine Mandantsakten —</p>";

        const ramUsedPct = sys.ram_bytes ? Math.min(100, Math.round(sys.kb_bytes / sys.ram_bytes * 100)) : 0;
        const diskUsed = sys.disk_total - sys.disk_free;
        const diskPct = sys.disk_total ? Math.round(diskUsed / sys.disk_total * 100) : 0;

        body.innerHTML = `
        <div class="sys-grid">
          <section class="sys-card">
            <h3><i class="fa-solid fa-scale-balanced"></i> Allgemein (Gesetze/Muster)</h3>
            <ul class="kb-list">${fileRows(ov.allgemein)}</ul>
          </section>
          <section class="sys-card">
            <h3><i class="fa-solid fa-gavel"></i> Rechtsprechung (Urteile)</h3>
            <ul class="kb-list">${fileRows(ov.rechtsprechung)}</ul>
          </section>
          <section class="sys-card" style="grid-column:1/-1;">
            <h3><i class="fa-solid fa-folder-tree"></i> Mandantsakten</h3>
            ${mandateHtml}
          </section>
          <section class="sys-card" style="grid-column:1/-1;">
            <h3><i class="fa-solid fa-server"></i> Speicher · ${esc(sys.host || "Server")}</h3>
            <div class="kb-meter"><span>Knowledge-Base belegt</span><b>${fmtBytes(sys.kb_bytes)}</b></div>
            <div class="kb-bar"><div style="width:${ramUsedPct}%"></div></div>
            ${sys.cpu ? `<div class="kb-meter"><span>CPU</span><b>${esc(sys.cpu)}</b></div>` : ""}
            <div class="kb-meter"><span>Arbeitsspeicher (RAM) gesamt</span><b>${fmtBytes(sys.ram_bytes)}</b></div>
            <div class="kb-meter"><span>Festplatte belegt / gesamt</span><b>${fmtBytes(diskUsed)} / ${fmtBytes(sys.disk_total)}</b></div>
            <div class="kb-bar"><div style="width:${diskPct}%"></div></div>
            <p class="muted">Gesamt-Chunks im Index: ${ov.total_chunks} · KB nutzt ${(sys.ram_bytes ? (sys.kb_bytes/sys.ram_bytes*100).toFixed(2) : 0)}% des RAM-Äquivalents</p>
          </section>
        </div>`;
    } catch (e) { body.innerHTML = "<p class='muted'>Fehler beim Laden.</p>"; }
}

// Theme-Button-Icon je nach aktivem Modus
function updateThemeButton() {
    const btn = document.getElementById("btn-theme");
    if (!btn) return;
    const mode = localStorage.getItem("anwalt_theme") || "system";
    const icon = { light: "fa-sun", dark: "fa-moon", system: "fa-circle-half-stroke" }[mode];
    btn.innerHTML = `<i class="fa-solid ${icon}"></i>`;
    btn.title = `Design: ${ {light:'Hell', dark:'Dunkel', system:'System'}[mode] } (klicken zum Wechseln)`;
}

// ===================== Chat-Attach: Dokument oder MP3 =====================
const AUDIO_EXT = ["mp3", "wav", "m4a", "ogg", "webm", "flac", "aac"];
const DOC_EXT = ["pdf", "docx", "txt", "md"];

// Filtert Dateien vor dem Upload nach bekannter Endung (greift auch bei Drag&Drop,
// wo das accept-Attribut nicht wirkt). Gibt nur die akzeptierten Dateien zurück
// und meldet aussortierte per Toast, statt den ganzen Batch serverseitig scheitern zu lassen.
function filterUploadable(files) {
    const ok = [], rejected = [];
    Array.from(files).forEach(f => {
        const ext = (f.name.split(".").pop() || "").toLowerCase();
        (AUDIO_EXT.includes(ext) || DOC_EXT.includes(ext) ? ok : rejected).push(f);
    });
    if (rejected.length) {
        showToast(`Übersprungen (Format nicht unterstützt): ${rejected.map(f => f.name).join(", ")}`, "error");
    }
    return ok;
}

async function handleChatFiles(fileList) {
    // Alles (Dokumente + MP3) zum Mandanten: Original wird roh abgelegt,
    // Dokumente geparst, MP3 lokal transkribiert -> beides in die Wissensbasis.
    const files = filterUploadable(fileList);
    if (!files.length) return;
    const fd = new FormData();
    files.forEach(f => fd.append("files", f));
    fd.append("matter_id", getMatter());
    const scopeEl = document.getElementById("upload-scope");
    fd.append("scope", (scopeEl && scopeEl.value) || "mandat");
    const lawEl = document.getElementById("upload-lawbook");
    fd.append("law_book", (lawEl && lawEl.value.trim()) || "");
    showToast("Dateien werden zum Mandanten abgelegt (roh + verarbeitet)…", "info");
    try {
        const res = await fetch("/api/ingest", { method: "POST", headers: authHeaders(), body: fd });
        const r = await res.json();
        if (!res.ok) { showToast(r.detail || "Fehler beim Einlesen.", "error"); return; }
        renderDocList(r.documents);
        // Transkripte (MP3) in die Eingabe übernehmen, damit man direkt weiterarbeiten kann
        const t = (r.transcripts || []).map(x => `Transkript ${x.source}:\n${x.text}`).join("\n\n");
        if (t) {
            const cur = chatInput.value.trim();
            chatInput.value = cur ? cur + "\n\n" + t : t;
            chatInput.dispatchEvent(new Event("input"));
        }
        showToast(r.message || "Abgelegt.", "success");
    } catch (e) { showToast("Netzwerkfehler beim Einlesen.", "error"); }
}

// Chat leeren (Neuer Chat / Broom) – baut den Welcome-Screen sicher neu auf
function clearChat() {
    if (!messageFeed) return;
    messageFeed.innerHTML = "";
    if (welcomeScreen) { messageFeed.appendChild(welcomeScreen); welcomeScreen.style.display = "block"; }
    if (contextPanel) contextPanel.style.display = "none";
    if (chatInput) { chatInput.value = ""; chatInput.style.height = "auto"; }
    showToast("Neuer Chat gestartet.", "info");
}

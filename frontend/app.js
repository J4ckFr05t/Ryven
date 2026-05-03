/**
 * Ryven Frontend — WebSocket chat client with conversation memory & MCP support
 */

// ── State ─────────────────────────────────────────────────────────────────
let ws = null;
let currentModel = 'openrouter:auto';
let currentConvId = null;

const LS_LLM_PROVIDER = 'ryven_llm_provider';
const LS_LLM_OPENAI = 'ryven_llm_openai_model';
const LS_LLM_GEMINI = 'ryven_llm_gemini_model';
const LS_LLM_OPENROUTER = 'ryven_llm_openrouter_model';

const OPENAI_MODEL_OPTIONS = [
    { id: 'gpt-4.1', label: 'GPT-4.1' },
    { id: 'gpt-4-turbo', label: 'GPT-4 Turbo' },
    { id: 'o3-mini', label: 'o3-mini' },
];

const GEMINI_MODEL_OPTIONS = [
    { id: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
    { id: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
    { id: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash' },
];

const OPENROUTER_MODEL_KEYS = [
    'auto',
    'gpt-oss',
    'nemotron',
    'gemma4',
    'minimax',
    'hy3',
    'laguna',
    'glm',
    'ling',
    'nano-9b',
];

let llmProvider = 'openrouter';
let llmOpenAIModel = 'gpt-4.1';
let llmGeminiModel = 'gemini-2.5-flash';
let llmOpenRouterKey = 'auto';
let llmHealth = { openai: false, gemini: false, openrouter: false };
let isProcessing = false;
let conversations = [];
let projects = [];
let currentProjectId = 'default';
let isAuthenticated = false;
let authConfigured = false;
let requiresSetup = false;
let displayName = '';

// ── DOM Elements ──────────────────────────────────────────────────────────
const messagesEl = document.getElementById('messages');
const messagesContainer = document.getElementById('messagesContainer');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const welcomeScreen = document.getElementById('welcomeScreen');
const statusDot = document.querySelector('.status-dot-live');
const statusText = document.querySelector('.status-text');
const newChatBtn = document.getElementById('newChatBtn');
const sidebar = document.getElementById('sidebar');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarExpand = document.getElementById('sidebarExpand');
const conversationList = document.getElementById('conversationList');
const githubToolIndicator = document.getElementById('githubToolIndicator');
const authOverlay = document.getElementById('authOverlay');
const authForm = document.getElementById('authForm');
const authDisplayNameInput = document.getElementById('authDisplayName');
const authPasswordInput = document.getElementById('authPassword');
const authPasswordConfirmInput = document.getElementById('authPasswordConfirm');
const authError = document.getElementById('authError');
const authTitle = document.getElementById('authTitle');
const authSubtitle = document.getElementById('authSubtitle');
const authSubmitBtn = document.getElementById('authSubmitBtn');
const settingsBtn = document.getElementById('settingsBtn');
const lockBtn = document.getElementById('lockBtn');
const settingsOverlay = document.getElementById('settingsOverlay');
const settingsPasswordForm = document.getElementById('settingsPasswordForm');
const settingsProviderRow = document.getElementById('settingsProviderRow');
const settingsOpenAIModel = document.getElementById('settingsOpenAIModel');
const settingsGeminiModel = document.getElementById('settingsGeminiModel');
const settingsOpenRouterModel = document.getElementById('settingsOpenRouterModel');
const settingsModelBlockOpenAI = document.getElementById('settingsModelBlockOpenAI');
const settingsModelBlockGemini = document.getElementById('settingsModelBlockGemini');
const settingsModelBlockOpenRouter = document.getElementById('settingsModelBlockOpenRouter');
const settingsApiHint = document.getElementById('settingsApiHint');
const settingsCurrentPassword = document.getElementById('settingsCurrentPassword');
const settingsNewPassword = document.getElementById('settingsNewPassword');
const settingsConfirmPassword = document.getElementById('settingsConfirmPassword');
const settingsCancelBtn = document.getElementById('settingsCancelBtn');
const settingsError = document.getElementById('settingsError');
const welcomeTitle = document.getElementById('welcomeTitle');
const projectSelect = document.getElementById('projectSelect');
const newProjectBtn = document.getElementById('newProjectBtn');
const knowledgeBtn = document.getElementById('knowledgeBtn');
const kbOverlay = document.getElementById('kbOverlay');
const kbCloseBtn = document.getElementById('kbCloseBtn');
const kbError = document.getElementById('kbError');
const kbRepoList = document.getElementById('kbRepoList');
const kbItemsList = document.getElementById('kbItemsList');
const kbSaveNoteBtn = document.getElementById('kbSaveNoteBtn');
const kbSaveSnippetBtn = document.getElementById('kbSaveSnippetBtn');
const kbSaveRepoBtn = document.getElementById('kbSaveRepoBtn');
const kbUploadBtn = document.getElementById('kbUploadBtn');
const kbFileInput = document.getElementById('kbFileInput');
const kbFileLabel = document.getElementById('kbFileLabel');
const kbRepoSelect = document.getElementById('kbRepoSelect');
const kbBranchInput = document.getElementById('kbBranchInput');
const kbBranchDatalist = document.getElementById('kbBranchDatalist');
const kbBranchLoadMeta = document.getElementById('kbBranchLoadMeta');
const kbGithubHint = document.getElementById('kbGithubHint');
const kbLoadMoreRepos = document.getElementById('kbLoadMoreRepos');
const kbNoteEditHint = document.getElementById('kbNoteEditHint');
const kbSnippetEditHint = document.getElementById('kbSnippetEditHint');
const kbRepoEditBanner = document.getElementById('kbRepoEditBanner');
const kbRepoEditBannerText = document.getElementById('kbRepoEditBannerText');
const kbCancelNoteEdit = document.getElementById('kbCancelNoteEdit');
const kbCancelSnippetEdit = document.getElementById('kbCancelSnippetEdit');
const kbCancelRepoEdit = document.getElementById('kbCancelRepoEdit');
const newProjectOverlay = document.getElementById('newProjectOverlay');
const newProjectForm = document.getElementById('newProjectForm');
const newProjectCancelBtn = document.getElementById('newProjectCancelBtn');
const newProjectError = document.getElementById('newProjectError');

// ── Configure marked.js ───────────────────────────────────────────────────
marked.setOptions({
    highlight: function(code, lang) {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
    },
    breaks: true,
    gfm: true
});

// ── WebSocket Connection ──────────────────────────────────────────────────
function connectWebSocket() {
    if (!isAuthenticated) return;
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        console.log('Connected to Ryven');
        statusDot?.classList.add('connected');
        if (statusText) statusText.textContent = 'Connected';
        checkHealth();
    };

    ws.onclose = () => {
        console.log('Disconnected');
        statusDot?.classList.remove('connected');
        if (statusText) statusText.textContent = 'Disconnected';
        if (isAuthenticated) {
            setTimeout(connectWebSocket, 3000);
        }
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleServerEvent(data);
    };
}

// ── Health Check (discover MCP tools) ─────────────────────────────────────
async function checkHealth() {
    try {
        const resp = await fetch('/health');
        const data = await resp.json();
        if (data.mcp_servers && data.mcp_servers.includes('github')) {
            githubToolIndicator.style.display = 'flex';
        }
    } catch (e) {
        console.warn('Health check failed:', e);
    }
}

async function checkAuthStatus() {
    try {
        const resp = await fetch('/api/auth/status');
        const data = await resp.json();
        authConfigured = Boolean(data.auth_configured);
        requiresSetup = Boolean(data.requires_setup);
        isAuthenticated = Boolean(data.authenticated);
        displayName = (data.display_name || '').trim();
    } catch (e) {
        console.warn('Auth status check failed:', e);
        authConfigured = false;
        requiresSetup = true;
        isAuthenticated = false;
        displayName = '';
    }
}

function renderWelcomeName() {
    if (!welcomeTitle) return;
    welcomeTitle.textContent = displayName ? `Hello, ${displayName}` : 'Hello';
}

function renderAuthGate() {
    if (!authOverlay) return;
    const shouldShow = !isAuthenticated;
    authOverlay.classList.toggle('hidden', !shouldShow);
    if (!shouldShow) return;

    if (requiresSetup) {
        authTitle.textContent = 'Set up your password';
        authSubtitle.textContent = 'Tell me your name and create a password.';
        authSubmitBtn.textContent = 'Create Password';
        authDisplayNameInput.classList.remove('hidden');
        authPasswordInput.placeholder = 'New password';
        authPasswordInput.setAttribute('autocomplete', 'new-password');
        authPasswordConfirmInput.classList.remove('hidden');
    } else {
        authTitle.textContent = 'Ryven is locked';
        authSubtitle.textContent = 'Enter your password to continue.';
        authSubmitBtn.textContent = 'Unlock';
        authDisplayNameInput.classList.add('hidden');
        authDisplayNameInput.value = '';
        authPasswordInput.placeholder = 'Application password';
        authPasswordInput.setAttribute('autocomplete', 'current-password');
        authPasswordConfirmInput.classList.add('hidden');
        authPasswordConfirmInput.value = '';
    }
    authError.textContent = '';
    authPasswordInput?.focus();
}

function renderSettingsAccess() {
    if (!settingsBtn) return;
    settingsBtn.classList.toggle('hidden', !isAuthenticated || requiresSetup || !authConfigured);
    if (lockBtn) {
        lockBtn.classList.toggle('hidden', !isAuthenticated || requiresSetup || !authConfigured);
    }
}

async function setupPassword(password, userDisplayName) {
    const resp = await fetch('/api/auth/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password, display_name: userDisplayName })
    });
    return resp.json();
}

async function changePassword(currentPassword, newPassword) {
    const resp = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword
        })
    });
    return resp.json();
}

async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    isAuthenticated = false;
    isProcessing = false;
    currentConvId = null;
    if (messageInput) {
        messageInput.disabled = false;
        messageInput.value = '';
    }
    if (sendBtn) sendBtn.disabled = true;
    if (messagesEl) messagesEl.innerHTML = '';
    if (welcomeScreen) {
        messagesEl.appendChild(welcomeScreen);
        welcomeScreen.style.display = 'flex';
    }
    conversations = [];
    renderConversationList();
    removeThinking();
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close();
    }
}

async function login(password) {
    const resp = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
    });
    return resp.json();
}

// ── Conversation Management ───────────────────────────────────────────────
async function loadProjects() {
    try {
        const resp = await fetch('/api/projects');
        const data = await resp.json();
        projects = data.projects || [];
        if (!projects.some((p) => p.id === currentProjectId)) {
            currentProjectId = projects[0]?.id || 'default';
        }
        renderProjectSelect();
    } catch (e) {
        console.warn('Failed to load projects:', e);
    }
}

function renderProjectSelect() {
    if (!projectSelect) return;
    projectSelect.innerHTML = projects
        .map(
            (p) =>
                `<option value="${escapeHtml(p.id)}" ${p.id === currentProjectId ? 'selected' : ''}>${escapeHtml(p.name)}</option>`
        )
        .join('');
}

async function loadConversations() {
    try {
        const q = currentProjectId ? `?project_id=${encodeURIComponent(currentProjectId)}` : '';
        const resp = await fetch(`/api/conversations${q}`);
        const data = await resp.json();
        conversations = data.conversations || [];
        renderConversationList();
    } catch (e) {
        console.warn('Failed to load conversations:', e);
    }
}

function renderConversationList() {
    if (conversations.length === 0) {
        conversationList.innerHTML = '<div class="conv-empty">No conversations yet</div>';
        return;
    }
    conversationList.innerHTML = conversations.map(c => `
        <div class="conv-item ${c.id === currentConvId ? 'active' : ''}" 
             data-id="${c.id}" onclick="loadConversation('${c.id}')">
            <span class="conv-item-icon">💬</span>
            <span class="conv-item-title">${escapeHtml(c.title)}</span>
            <button class="conv-item-delete" onclick="event.stopPropagation(); deleteConversation('${c.id}')" title="Delete">
                ✕
            </button>
        </div>
    `).join('');
}

async function loadConversation(convId) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    currentConvId = convId;
    renderConversationList();

    // Clear messages and hide welcome
    messagesEl.innerHTML = '';
    if (welcomeScreen) welcomeScreen.style.display = 'none';

    // Tell server to load this conversation
    ws.send(JSON.stringify({ type: 'load_conversation', conversation_id: convId }));
    sidebar.classList.remove('open');
}

async function deleteConversation(convId) {
    try {
        await fetch(`/api/conversations/${convId}`, { method: 'DELETE' });
        if (currentConvId === convId) {
            currentConvId = null;
            startNewChat();
        }
        await loadConversations();
    } catch (e) {
        console.error('Delete failed:', e);
    }
}

function startNewChat() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    currentConvId = null;
    messagesEl.innerHTML = '';
    if (welcomeScreen) {
        messagesEl.appendChild(welcomeScreen);
        welcomeScreen.style.display = 'flex';
    }
    renderConversationList();
    ws.send(JSON.stringify({ type: 'new_conversation', project_id: currentProjectId }));
    sidebar.classList.remove('open');
}

// ── Handle Server Events ──────────────────────────────────────────────────
let thinkingEl = null;

function handleServerEvent(data) {
    switch (data.type) {
        case 'status':
            showThinking(data.status);
            break;
        case 'content':
            removeThinking();
            appendToLastAssistant(data.text);
            break;
        case 'tool_call':
            removeThinking();
            addToolCallCard(data);
            break;
        case 'tool_result':
            updateToolCallCard(data);
            break;
        case 'response':
            removeThinking();
            addAssistantMessage(data.content);
            finishProcessing();
            break;
        case 'error':
            removeThinking();
            addErrorMessage(data.message);
            finishProcessing();
            break;
        case 'conversation_created':
            currentConvId = data.conversation_id;
            if (data.project_id) {
                currentProjectId = data.project_id;
                renderProjectSelect();
            }
            loadConversations();
            break;
        case 'conversation_loaded':
            if (data.project_id) {
                currentProjectId = data.project_id;
                renderProjectSelect();
            }
            renderLoadedMessages(data.messages);
            break;
        case 'conversation_cleared':
            if (data.project_id) {
                currentProjectId = data.project_id;
                renderProjectSelect();
            }
            break;
    }
}

// ── Render loaded conversation ────────────────────────────────────────────
function renderLoadedMessages(messages) {
    messagesEl.innerHTML = '';
    for (const msg of messages) {
        if (msg.role === 'user' && msg.content) {
            addUserMessage(msg.content, false);
        } else if (msg.role === 'assistant' && msg.content) {
            addAssistantMessage(msg.content, false);
        }
    }
    scrollToBottom();
}

// ── Thinking Indicator ────────────────────────────────────────────────────
function showThinking(status) {
    removeThinking();
    thinkingEl = document.createElement('div');
    thinkingEl.className = 'thinking-indicator';
    thinkingEl.innerHTML = `
        <div class="thinking-dots"><span></span><span></span><span></span></div>
        <span class="thinking-label">${status === 'thinking' ? 'Thinking...' : status}</span>
    `;
    messagesEl.appendChild(thinkingEl);
    scrollToBottom();
}

function removeThinking() {
    if (thinkingEl) {
        thinkingEl.remove();
        thinkingEl = null;
    }
}

// ── Message Rendering ─────────────────────────────────────────────────────
function addUserMessage(text, animate = true) {
    if (welcomeScreen) welcomeScreen.style.display = 'none';
    const el = document.createElement('div');
    el.className = 'message user';
    if (!animate) el.style.animation = 'none';
    el.innerHTML = `
        <div class="message-avatar">◌</div>
        <div class="message-content">${escapeHtml(text)}</div>
    `;
    messagesEl.appendChild(el);
    scrollToBottom();
}

function addAssistantMessage(content, animate = true) {
    const el = document.createElement('div');
    el.className = 'message assistant';
    if (!animate) el.style.animation = 'none';
    const rendered = marked.parse(content);
    el.innerHTML = `
        <div class="message-avatar">◈</div>
        <div class="message-content">${rendered}</div>
    `;
    messagesEl.appendChild(el);
    el.querySelectorAll('pre code').forEach(block => {
        hljs.highlightElement(block);
    });
    scrollToBottom();
}

function appendToLastAssistant(text) {
    const existing = messagesEl.querySelectorAll('.message.assistant');
    if (existing.length > 0) {
        const last = existing[existing.length - 1];
        const content = last.querySelector('.message-content');
        content.innerHTML += marked.parse(text);
    }
}

function addErrorMessage(message) {
    const el = document.createElement('div');
    el.className = 'message assistant';
    el.innerHTML = `
        <div class="message-avatar">⚠️</div>
        <div class="message-content" style="border-color: rgba(255,92,92,0.3)">
            <strong style="color: var(--red)">Error:</strong> ${escapeHtml(message)}
        </div>
    `;
    messagesEl.appendChild(el);
    scrollToBottom();
}

// ── Tool Call Cards ───────────────────────────────────────────────────────
const toolIcons = {
    read_file: '📄', list_directory: '📂', search_files: '🔎',
    count_files: '🔢', list_files: '🧾',
    get_file_info: 'ℹ️', web_search: '🔍', tavily_search: '🌐',
    search_project_knowledge: '📚'
};

function getToolIcon(name) {
    // Check local tools first
    if (toolIcons[name]) return toolIcons[name];
    // MCP tools (prefixed with server name)
    if (name.startsWith('github__')) return '🐙';
    return '🔧';
}

function addToolCallCard(data) {
    const el = document.createElement('div');
    el.className = 'tool-call-card';
    el.id = `tool-${data.id}`;

    const icon = getToolIcon(data.name);
    const argsStr = JSON.stringify(data.args, null, 2);
    const displayName = data.name.includes('__')
        ? data.name.split('__').pop().replace(/_/g, ' ')
        : data.name.replace(/_/g, ' ');

    el.innerHTML = `
        <div class="tool-call-header" onclick="toggleToolBody('${data.id}')">
            <div class="tool-call-icon calling">${icon}</div>
            <span class="tool-call-name">${formatToolName(displayName)}</span>
            ${data.name.includes('__') ? '<span class="mcp-badge" style="margin-left:4px">MCP</span>' : ''}
            <span class="tool-call-status">
                <div class="tool-call-spinner"></div>
                <span>Running</span>
            </span>
        </div>
        <div class="tool-call-body" id="tool-body-${data.id}">
            <div class="tool-call-args"><pre>${escapeHtml(argsStr)}</pre></div>
            <div class="tool-call-result" id="tool-result-${data.id}">Waiting for result...</div>
        </div>
    `;
    messagesEl.appendChild(el);
    scrollToBottom();
}

function updateToolCallCard(data) {
    const card = document.getElementById(`tool-${data.id}`);
    if (!card) return;

    const iconEl = card.querySelector('.tool-call-icon');
    const statusEl = card.querySelector('.tool-call-status');
    const resultEl = document.getElementById(`tool-result-${data.id}`);

    iconEl.className = `tool-call-icon ${data.success ? 'done' : 'error'}`;
    statusEl.innerHTML = data.success
        ? '<span class="tool-call-check">✓</span><span>Done</span>'
        : '<span style="color:var(--red)">✗</span><span>Error</span>';

    if (resultEl) {
        const result = data.result || '';
        resultEl.innerHTML = `<pre>${escapeHtml(result)}</pre>`;
    }
}

function toggleToolBody(id) {
    const body = document.getElementById(`tool-body-${id}`);
    if (body) body.classList.toggle('visible');
}

function formatToolName(name) {
    return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// ── Send Message ──────────────────────────────────────────────────────────
function sendMessage() {
    const text = messageInput.value.trim();
    if (!isAuthenticated || !text || isProcessing || !ws || ws.readyState !== WebSocket.OPEN) return;

    isProcessing = true;
    sendBtn.disabled = true;
    messageInput.disabled = true;

    addUserMessage(text);
    messageInput.value = '';
    messageInput.style.height = 'auto';

    ws.send(JSON.stringify({
        type: 'chat',
        message: text,
        model: currentModel,
        project_id: currentProjectId
    }));
}

function finishProcessing() {
    isProcessing = false;
    messageInput.disabled = false;
    messageInput.focus();
    updateSendBtn();
}

// ── Utilities ─────────────────────────────────────────────────────────────
function scrollToBottom() {
    requestAnimationFrame(() => {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    });
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function populateLlmDropdowns() {
    if (settingsOpenAIModel && !settingsOpenAIModel.dataset.populated) {
        settingsOpenAIModel.innerHTML = OPENAI_MODEL_OPTIONS.map(
            (m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}</option>`
        ).join('');
        settingsOpenAIModel.dataset.populated = '1';
    }
    if (settingsGeminiModel && !settingsGeminiModel.dataset.populated) {
        settingsGeminiModel.innerHTML = GEMINI_MODEL_OPTIONS.map(
            (m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}</option>`
        ).join('');
        settingsGeminiModel.dataset.populated = '1';
    }
}

function loadLlmFromStorage() {
    try {
        const p = localStorage.getItem(LS_LLM_PROVIDER);
        if (p === 'openai' || p === 'gemini' || p === 'openrouter') llmProvider = p;
        const oa = localStorage.getItem(LS_LLM_OPENAI);
        if (oa && OPENAI_MODEL_OPTIONS.some((x) => x.id === oa)) llmOpenAIModel = oa;
        const gm = localStorage.getItem(LS_LLM_GEMINI);
        if (gm && GEMINI_MODEL_OPTIONS.some((x) => x.id === gm)) llmGeminiModel = gm;
        const or = localStorage.getItem(LS_LLM_OPENROUTER);
        if (or && OPENROUTER_MODEL_KEYS.includes(or)) llmOpenRouterKey = or;
    } catch (_) {
        /* ignore */
    }
}

function persistLlmToStorage() {
    try {
        localStorage.setItem(LS_LLM_PROVIDER, llmProvider);
        localStorage.setItem(LS_LLM_OPENAI, llmOpenAIModel);
        localStorage.setItem(LS_LLM_GEMINI, llmGeminiModel);
        localStorage.setItem(LS_LLM_OPENROUTER, llmOpenRouterKey);
    } catch (_) {
        /* ignore */
    }
}

function syncCurrentModelFromLlmState() {
    if (llmProvider === 'openrouter') {
        currentModel = `openrouter:${llmOpenRouterKey}`;
    } else if (llmProvider === 'openai') {
        currentModel = `openai:${llmOpenAIModel}`;
    } else {
        currentModel = `gemini:${llmGeminiModel}`;
    }
}

function readLlmSelectionsFromDom() {
    if (settingsOpenAIModel?.value) llmOpenAIModel = settingsOpenAIModel.value;
    if (settingsGeminiModel?.value) llmGeminiModel = settingsGeminiModel.value;
    if (settingsOpenRouterModel?.value) llmOpenRouterKey = settingsOpenRouterModel.value;
}

function applyLlmSelectionsToDom() {
    if (settingsOpenAIModel) settingsOpenAIModel.value = llmOpenAIModel;
    if (settingsGeminiModel) settingsGeminiModel.value = llmGeminiModel;
    if (settingsOpenRouterModel) settingsOpenRouterModel.value = llmOpenRouterKey;
    document.querySelectorAll('.settings-provider-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.provider === llmProvider);
    });
    if (settingsModelBlockOpenAI) {
        settingsModelBlockOpenAI.classList.toggle('hidden', llmProvider !== 'openai');
    }
    if (settingsModelBlockGemini) {
        settingsModelBlockGemini.classList.toggle('hidden', llmProvider !== 'gemini');
    }
    if (settingsModelBlockOpenRouter) {
        settingsModelBlockOpenRouter.classList.toggle('hidden', llmProvider !== 'openrouter');
    }
}

async function refreshSettingsApiHealth() {
    try {
        const resp = await fetch('/health');
        const data = await resp.json();
        llmHealth.openai = Boolean(data.openai);
        llmHealth.gemini = Boolean(data.gemini);
        llmHealth.openrouter = Boolean(data.openrouter);
    } catch (_) {
        llmHealth = { openai: false, gemini: false, openrouter: false };
    }
}

function renderSettingsApiHint() {
    if (!settingsApiHint) return;
    const dot = (ok) => (ok ? '<span class="hint-ok">configured</span>' : '<span class="hint-warn">no key</span>');
    settingsApiHint.innerHTML = `API keys in your environment: OpenAI ${dot(llmHealth.openai)} · Gemini ${dot(
        llmHealth.gemini
    )} · OpenRouter ${dot(llmHealth.openrouter)}`;
}

function updateSendBtn() {
    sendBtn.disabled = !messageInput.value.trim() || isProcessing;
}

// ── Event Listeners ───────────────────────────────────────────────────────
messageInput.addEventListener('input', () => {
    updateSendBtn();
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + 'px';
});

messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

sendBtn.addEventListener('click', sendMessage);

function wireLlmSettingsControls() {
    populateLlmDropdowns();
    if (settingsProviderRow) {
        settingsProviderRow.querySelectorAll('.settings-provider-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const p = btn.dataset.provider;
                if (p !== 'openai' && p !== 'gemini' && p !== 'openrouter') return;
                llmProvider = p;
                persistLlmToStorage();
                applyLlmSelectionsToDom();
                syncCurrentModelFromLlmState();
            });
        });
    }
    const onModelSelectChange = () => {
        readLlmSelectionsFromDom();
        persistLlmToStorage();
        syncCurrentModelFromLlmState();
    };
    settingsOpenAIModel?.addEventListener('change', onModelSelectChange);
    settingsGeminiModel?.addEventListener('change', onModelSelectChange);
    settingsOpenRouterModel?.addEventListener('change', onModelSelectChange);
}

wireLlmSettingsControls();

newChatBtn.addEventListener('click', startNewChat);

if (sidebarToggle) {
    sidebarToggle.addEventListener('click', () => {
        if (window.innerWidth > 768) {
            sidebar.classList.toggle('collapsed');
        } else {
            sidebar.classList.toggle('open');
        }
    });
}

if (sidebarExpand) {
    sidebarExpand.addEventListener('click', () => {
        sidebar.classList.remove('collapsed');
    });
}

document.querySelectorAll('.cap-card').forEach(card => {
    card.addEventListener('click', () => {
        const prompt = card.dataset.prompt;
        if (prompt) {
            messageInput.value = prompt;
            updateSendBtn();
            sendMessage();
        }
    });
});

if (authForm) {
    authForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        authError.textContent = '';
        const password = authPasswordInput.value;
        if (!password) return;

        if (requiresSetup) {
            const providedName = authDisplayNameInput.value.trim();
            if (providedName.length < 2) {
                authError.textContent = 'Please enter a name with at least 2 characters';
                return;
            }
            if (password.length < 6) {
                authError.textContent = 'Password must be at least 6 characters';
                return;
            }
            if (password !== authPasswordConfirmInput.value) {
                authError.textContent = 'Passwords do not match';
                return;
            }
            displayName = providedName;
        }

        const result = requiresSetup
            ? await setupPassword(password, displayName)
            : await login(password);
        if (!result.ok) {
            authError.textContent = result.message || 'Authentication failed';
            authPasswordInput.focus();
            authPasswordInput.select();
            return;
        }

        isAuthenticated = true;
        requiresSetup = false;
        authConfigured = true;
        renderWelcomeName();
        authPasswordInput.value = '';
        authDisplayNameInput.value = '';
        authPasswordConfirmInput.value = '';
        renderAuthGate();
        renderSettingsAccess();
        connectWebSocket();
        await loadProjects();
        await loadConversations();
        messageInput.focus();
    });
}

if (settingsBtn) {
    settingsBtn.addEventListener('click', async () => {
        settingsError.textContent = '';
        populateLlmDropdowns();
        loadLlmFromStorage();
        applyLlmSelectionsToDom();
        syncCurrentModelFromLlmState();
        await refreshSettingsApiHealth();
        renderSettingsApiHint();
        settingsOverlay.classList.remove('hidden');
        settingsCurrentPassword.value = '';
        settingsNewPassword.value = '';
        settingsConfirmPassword.value = '';
        settingsCurrentPassword.focus();
    });
}

if (lockBtn) {
    lockBtn.addEventListener('click', async () => {
        await logout();
        await checkAuthStatus();
        renderAuthGate();
        renderSettingsAccess();
    });
}

if (settingsCancelBtn) {
    settingsCancelBtn.addEventListener('click', () => {
        settingsOverlay.classList.add('hidden');
    });
}

if (settingsOverlay) {
    settingsOverlay.addEventListener('click', (e) => {
        if (e.target === settingsOverlay) settingsOverlay.classList.add('hidden');
    });
}

if (settingsPasswordForm) {
    settingsPasswordForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        settingsError.textContent = '';
        const currentPassword = settingsCurrentPassword.value;
        const newPassword = settingsNewPassword.value;
        const confirmPassword = settingsConfirmPassword.value;
        if (newPassword.length < 6) {
            settingsError.textContent = 'New password must be at least 6 characters';
            return;
        }
        if (newPassword !== confirmPassword) {
            settingsError.textContent = 'New passwords do not match';
            return;
        }

        const result = await changePassword(currentPassword, newPassword);
        if (!result.ok) {
            settingsError.textContent = result.message || 'Failed to change password';
            return;
        }

        settingsOverlay.classList.add('hidden');
        await logout();
        await checkAuthStatus();
        renderAuthGate();
        renderSettingsAccess();
    });
}

// ── Initialize ────────────────────────────────────────────────────────────
async function initApp() {
    populateLlmDropdowns();
    loadLlmFromStorage();
    applyLlmSelectionsToDom();
    syncCurrentModelFromLlmState();

    await checkHealth();
    await checkAuthStatus();
    renderWelcomeName();
    renderAuthGate();
    renderSettingsAccess();

    if (isAuthenticated) {
        await loadProjects();
        connectWebSocket();
        await loadConversations();
        messageInput.focus();
    }
}

// ── Projects & Knowledge ──────────────────────────────────────────────────
if (projectSelect) {
    projectSelect.addEventListener('change', () => {
        currentProjectId = projectSelect.value;
        loadConversations();
        if (currentConvId) {
            startNewChat();
        }
    });
}

function openKbPanel() {
    if (!kbOverlay) return;
    kbError.textContent = '';
    resetKbEditors();
    kbOverlay.classList.remove('hidden');
    loadKbPanelData();
}

function closeKbPanel() {
    if (kbOverlay) kbOverlay.classList.add('hidden');
    resetKbEditors();
}

async function loadKbPanelData() {
    if (!currentProjectId || !kbItemsList) return;
    kbError.textContent = '';
    try {
        const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb`);
        const data = await resp.json();
        const items = data.items || [];
        const gh = data.github_repos || [];
        kbRepoList.innerHTML = gh.length
            ? gh
                  .map((r) => {
                      const br = r.branch || 'main';
                      return `
            <li>
                <span><strong>${escapeHtml(r.owner)}/${escapeHtml(r.repo)}</strong> <span class="kb-item-meta">@${escapeHtml(br)}</span></span>
                <div class="kb-item-actions">
                    <button type="button" class="kb-item-edit" data-owner="${escapeHtml(r.owner)}" data-repo="${escapeHtml(r.repo)}" data-branch="${escapeHtml(br)}">Edit</button>
                    <button type="button" class="kb-item-del" data-owner="${escapeHtml(r.owner)}" data-repo="${escapeHtml(r.repo)}" data-branch="${escapeHtml(br)}">Remove</button>
                </div>
            </li>`;
                  })
                  .join('')
            : '<li class="kb-item-meta">No linked repositories</li>';
        kbItemsList.innerHTML = items.length
            ? items
                  .map((it) => {
                      const canEdit =
                          it.kind === 'note' || it.kind === 'snippet' || it.kind === 'github_repo';
                      const editBtn = canEdit
                          ? `<button type="button" class="kb-item-edit" data-kb-id="${escapeHtml(it.id)}" data-kind="${escapeHtml(it.kind)}">Edit</button>`
                          : '';
                      return `
            <li>
                <span>
                    <span class="kb-item-meta">${escapeHtml(it.kind)}</span>
                    ${escapeHtml(it.title)}
                </span>
                <div class="kb-item-actions">
                    ${editBtn}
                    <button type="button" class="kb-item-del" data-kb-id="${escapeHtml(it.id)}">Delete</button>
                </div>
            </li>`;
                  })
                  .join('')
            : '<li class="kb-item-meta">No items yet</li>';

        kbRepoList.querySelectorAll('.kb-item-edit[data-owner]').forEach((btn) => {
            btn.addEventListener('click', async (ev) => {
                ev.preventDefault();
                kbError.textContent = '';
                const owner = btn.getAttribute('data-owner');
                const repo = btn.getAttribute('data-repo');
                const branch = btn.getAttribute('data-branch') || 'main';
                await startKbRepoEdit(owner, repo, branch);
            });
        });
        kbRepoList.querySelectorAll('.kb-item-del[data-owner]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const owner = btn.getAttribute('data-owner');
                const repo = btn.getAttribute('data-repo');
                const branch = btn.getAttribute('data-branch') || 'main';
                await fetch(
                    `/api/projects/${encodeURIComponent(currentProjectId)}/kb/repo?owner=${encodeURIComponent(owner)}&repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`,
                    { method: 'DELETE' }
                );
                loadKbPanelData();
            });
        });
        kbItemsList.querySelectorAll('.kb-item-edit[data-kb-id]').forEach((btn) => {
            btn.addEventListener('click', async (ev) => {
                ev.preventDefault();
                kbError.textContent = '';
                const id = btn.getAttribute('data-kb-id');
                const kind = btn.getAttribute('data-kind');
                if (kind === 'note') await startKbNoteEdit(id);
                else if (kind === 'snippet') await startKbSnippetEdit(id);
                else if (kind === 'github_repo') await startKbGithubItemEdit(id);
            });
        });
        kbItemsList.querySelectorAll('.kb-item-del[data-kb-id]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const id = btn.getAttribute('data-kb-id');
                await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb/${encodeURIComponent(id)}`, {
                    method: 'DELETE'
                });
                loadKbPanelData();
            });
        });
    } catch (e) {
        kbError.textContent = 'Could not load knowledge base.';
        console.warn(e);
    }
}

let githubReposNextPage = 1;

let kbEditingNoteId = null;
let kbEditingSnippetId = null;
let kbRepoEditState = null;

function resetKbEditors() {
    kbEditingNoteId = null;
    kbEditingSnippetId = null;
    kbRepoEditState = null;
    if (kbNoteEditHint) kbNoteEditHint.classList.add('hidden');
    if (kbSnippetEditHint) kbSnippetEditHint.classList.add('hidden');
    if (kbSaveNoteBtn) kbSaveNoteBtn.textContent = 'Save note';
    if (kbSaveSnippetBtn) kbSaveSnippetBtn.textContent = 'Save snippet';
    if (kbRepoEditBanner) kbRepoEditBanner.classList.add('hidden');
    if (kbRepoSelect) {
        kbRepoSelect.disabled = false;
        kbRepoSelect.querySelectorAll('option[data-kb-synthetic]').forEach((o) => o.remove());
    }
    if (kbSaveRepoBtn) kbSaveRepoBtn.textContent = 'Link repository';
    const nt = document.getElementById('kbNoteTitle');
    const nb = document.getElementById('kbNoteBody');
    const st = document.getElementById('kbSnippetTitle');
    const sc = document.getElementById('kbSnippetCode');
    if (nt) nt.value = '';
    if (nb) nb.value = '';
    if (st) st.value = '';
    if (sc) sc.value = '';
}

async function fetchKbItem(itemId) {
    const resp = await fetch(
        `/api/projects/${encodeURIComponent(currentProjectId)}/kb/items/${encodeURIComponent(itemId)}`
    );
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.item || null;
}

async function startKbNoteEdit(itemId) {
    kbEditingSnippetId = null;
    kbSnippetEditHint?.classList.add('hidden');
    if (kbSaveSnippetBtn) kbSaveSnippetBtn.textContent = 'Save snippet';
    cancelKbRepoEdit();
    const item = await fetchKbItem(itemId);
    if (!item || item.kind !== 'note') {
        kbError.textContent = 'Could not load that note.';
        return;
    }
    kbEditingNoteId = itemId;
    const nt = document.getElementById('kbNoteTitle');
    const nb = document.getElementById('kbNoteBody');
    if (nt) nt.value = item.title || '';
    if (nb) nb.value = item.body_text || '';
    kbNoteEditHint?.classList.remove('hidden');
    if (kbSaveNoteBtn) kbSaveNoteBtn.textContent = 'Update note';
    document.querySelectorAll('.kb-tab').forEach((t) => t.classList.remove('active'));
    document.querySelectorAll('.kb-panel').forEach((p) => p.classList.remove('active'));
    document.querySelector('.kb-tab[data-tab="note"]')?.classList.add('active');
    document.getElementById('kbPanelNote')?.classList.add('active');
}

async function startKbSnippetEdit(itemId) {
    cancelKbNoteEdit();
    cancelKbRepoEdit();
    const item = await fetchKbItem(itemId);
    if (!item || item.kind !== 'snippet') {
        kbError.textContent = 'Could not load that snippet.';
        return;
    }
    kbEditingSnippetId = itemId;
    const st = document.getElementById('kbSnippetTitle');
    const sc = document.getElementById('kbSnippetCode');
    if (st) st.value = item.title || '';
    if (sc) sc.value = item.body_text || '';
    kbSnippetEditHint?.classList.remove('hidden');
    if (kbSaveSnippetBtn) kbSaveSnippetBtn.textContent = 'Update snippet';
    document.querySelectorAll('.kb-tab').forEach((t) => t.classList.remove('active'));
    document.querySelectorAll('.kb-panel').forEach((p) => p.classList.remove('active'));
    document.querySelector('.kb-tab[data-tab="snippet"]')?.classList.add('active');
    document.getElementById('kbPanelSnippet')?.classList.add('active');
}

async function applyKbRepoEditSelection() {
    if (!kbRepoEditState || !kbRepoSelect || !kbBranchInput) return;
    const { owner, repo, branch } = kbRepoEditState;
    const val = `${owner}\t${repo}`;
    const has = Array.from(kbRepoSelect.options).some((o) => o.value === val);
    if (!has) {
        const opt = document.createElement('option');
        opt.value = val;
        opt.textContent = `${owner}/${repo}`;
        opt.dataset.defaultBranch = branch || 'main';
        opt.dataset.kbSynthetic = '1';
        kbRepoSelect.appendChild(opt);
    }
    kbRepoSelect.value = val;
    kbRepoSelect.disabled = true;
    if (kbSaveRepoBtn) kbSaveRepoBtn.textContent = 'Update branch';
    await loadKbBranchesForSelection();
    kbBranchInput.value = branch;
    kbBranchInput.disabled = false;
}

async function startKbRepoEdit(owner, repo, branch) {
    cancelKbNoteEdit();
    cancelKbSnippetEdit();
    kbRepoEditState = { owner, repo, branch: branch || 'main' };
    if (kbRepoEditBannerText) {
        kbRepoEditBannerText.textContent = `Updating branch for ${owner}/${repo} (current @${kbRepoEditState.branch})`;
    }
    kbRepoEditBanner?.classList.remove('hidden');
    document.querySelectorAll('.kb-tab').forEach((t) => t.classList.remove('active'));
    document.querySelectorAll('.kb-panel').forEach((p) => p.classList.remove('active'));
    document.querySelector('.kb-tab[data-tab="repo"]')?.classList.add('active');
    document.getElementById('kbPanelRepo')?.classList.add('active');
    await ensureGithubReposLoaded(true);
    await applyKbRepoEditSelection();
}

async function startKbGithubItemEdit(itemId) {
    const item = await fetchKbItem(itemId);
    if (!item || item.kind !== 'github_repo') {
        kbError.textContent = 'Could not load that repository item.';
        return;
    }
    const meta = item.metadata || {};
    const owner = meta.owner;
    const repo = meta.repo;
    const br = meta.branch || 'main';
    if (!owner || !repo) {
        kbError.textContent = 'Invalid repository metadata.';
        return;
    }
    await startKbRepoEdit(owner, repo, br);
}

function cancelKbNoteEdit() {
    kbEditingNoteId = null;
    const nt = document.getElementById('kbNoteTitle');
    const nb = document.getElementById('kbNoteBody');
    if (nt) nt.value = '';
    if (nb) nb.value = '';
    kbNoteEditHint?.classList.add('hidden');
    if (kbSaveNoteBtn) kbSaveNoteBtn.textContent = 'Save note';
}

function cancelKbSnippetEdit() {
    kbEditingSnippetId = null;
    const st = document.getElementById('kbSnippetTitle');
    const sc = document.getElementById('kbSnippetCode');
    if (st) st.value = '';
    if (sc) sc.value = '';
    kbSnippetEditHint?.classList.add('hidden');
    if (kbSaveSnippetBtn) kbSaveSnippetBtn.textContent = 'Save snippet';
}

function cancelKbRepoEdit() {
    kbRepoEditState = null;
    kbRepoEditBanner?.classList.add('hidden');
    if (kbRepoSelect) {
        kbRepoSelect.disabled = false;
        kbRepoSelect.querySelectorAll('option[data-kb-synthetic]').forEach((o) => o.remove());
    }
    if (kbSaveRepoBtn) kbSaveRepoBtn.textContent = 'Link repository';
}

async function ensureGithubReposLoaded(reset = true) {
    if (!kbRepoSelect || !kbGithubHint) return;
    if (reset) {
        githubReposNextPage = 1;
        kbRepoSelect.innerHTML = '<option value="">Loading repositories…</option>';
        kbRepoSelect.disabled = true;
        if (kbBranchInput) {
            kbBranchInput.value = '';
            kbBranchInput.disabled = true;
            kbBranchInput.placeholder = 'Select a repository first';
        }
        if (kbBranchDatalist) kbBranchDatalist.innerHTML = '';
        if (kbBranchLoadMeta) kbBranchLoadMeta.textContent = '';
        kbGithubHint.textContent = '';
        kbGithubHint.classList.remove('kb-github-warn');
        if (kbLoadMoreRepos) kbLoadMoreRepos.style.display = 'none';
    }
    try {
        const resp = await fetch(`/api/github/repos?page=${githubReposNextPage}`);
        const data = await resp.json();
        if (!data.configured) {
            kbGithubHint.textContent =
                data.error ||
                'Add GITHUB_PERSONAL_ACCESS_TOKEN to your environment to list repositories.';
            kbGithubHint.classList.add('kb-github-warn');
            kbRepoSelect.innerHTML = '<option value="">—</option>';
            kbRepoSelect.disabled = true;
            return;
        }
        if (data.error && (!data.repos || data.repos.length === 0)) {
            kbGithubHint.textContent = data.error;
            kbGithubHint.classList.add('kb-github-warn');
        } else {
            kbGithubHint.textContent =
                'Choose a repository you have access to, then pick a branch.';
            kbGithubHint.classList.remove('kb-github-warn');
        }
        const repos = data.repos || [];
        if (reset) {
            kbRepoSelect.innerHTML = '<option value="">Select a repository…</option>';
            repos.forEach((r) => {
                const opt = document.createElement('option');
                opt.value = `${r.owner}\t${r.name}`;
                opt.textContent = `${r.full_name}${r.private ? ' · private' : ''}`;
                opt.dataset.defaultBranch = r.default_branch || 'main';
                kbRepoSelect.appendChild(opt);
            });
        } else {
            repos.forEach((r) => {
                const opt = document.createElement('option');
                opt.value = `${r.owner}\t${r.name}`;
                opt.textContent = `${r.full_name}${r.private ? ' · private' : ''}`;
                opt.dataset.defaultBranch = r.default_branch || 'main';
                kbRepoSelect.appendChild(opt);
            });
        }
        kbRepoSelect.disabled = false;
        if (kbLoadMoreRepos) {
            kbLoadMoreRepos.style.display = data.has_more ? 'inline-flex' : 'none';
        }
        if (data.has_more) githubReposNextPage += 1;
    } catch (e) {
        kbGithubHint.textContent = 'Could not load repositories from GitHub.';
        kbGithubHint.classList.add('kb-github-warn');
        kbRepoSelect.innerHTML = '<option value="">—</option>';
        kbRepoSelect.disabled = true;
        console.warn(e);
    }
}

async function fetchAllKbBranches(owner, repo) {
    const seen = new Set();
    const branches = [];
    const maxPages = 50;
    let lastError = null;

    const finish = (truncated) => {
        branches.sort((a, b) => a.name.localeCompare(b.name));
        return { branches, error: lastError, truncated };
    };

    for (let page = 1; page <= maxPages; page++) {
        const resp = await fetch(
            `/api/github/branches?owner=${encodeURIComponent(owner)}&repo=${encodeURIComponent(repo)}&page=${page}`
        );
        const data = await resp.json();
        if (data.error) lastError = data.error;
        if (!data.configured) {
            return { branches: [], error: data.error || lastError, truncated: false };
        }
        const batch = data.branches || [];
        for (const b of batch) {
            if (b.name && !seen.has(b.name)) {
                seen.add(b.name);
                branches.push(b);
            }
        }
        if (!data.has_more || batch.length === 0) {
            return finish(false);
        }
    }
    return finish(true);
}

function populateBranchDatalist(branchObjs) {
    if (!kbBranchDatalist) return;
    kbBranchDatalist.innerHTML = '';
    branchObjs.forEach((b) => {
        const opt = document.createElement('option');
        opt.value = b.name;
        kbBranchDatalist.appendChild(opt);
    });
}

async function loadKbBranchesForSelection() {
    if (!kbRepoSelect || !kbBranchInput) return;
    const raw = kbRepoSelect.value;
    if (!raw) {
        kbBranchInput.value = '';
        kbBranchInput.disabled = true;
        kbBranchInput.placeholder = 'Select a repository first';
        if (kbBranchDatalist) kbBranchDatalist.innerHTML = '';
        if (kbBranchLoadMeta) kbBranchLoadMeta.textContent = '';
        return;
    }
    const tab = raw.indexOf('\t');
    const owner = raw.slice(0, tab);
    const repo = raw.slice(tab + 1);
    const opt = kbRepoSelect.selectedOptions[0];
    const defaultBranch = opt?.dataset?.defaultBranch || 'main';

    kbBranchInput.disabled = true;
    kbBranchInput.value = '';
    kbBranchInput.placeholder = 'Loading branches…';
    if (kbBranchDatalist) kbBranchDatalist.innerHTML = '';
    if (kbBranchLoadMeta) kbBranchLoadMeta.textContent = 'Loading branches from GitHub…';

    try {
        const { branches, error, truncated } = await fetchAllKbBranches(owner, repo);

        if (branches.length === 0 && error) {
            kbBranchInput.placeholder = 'Type branch name';
            kbBranchInput.value = defaultBranch;
            kbBranchInput.disabled = false;
            populateBranchDatalist([{ name: defaultBranch, protected: false }]);
            if (kbGithubHint) kbGithubHint.textContent = error;
            if (kbBranchLoadMeta) kbBranchLoadMeta.textContent = 'Could not list branches — you can still type one.';
            return;
        }

        populateBranchDatalist(branches);
        kbBranchInput.placeholder = 'Search suggestions or type any branch name';
        kbBranchInput.disabled = false;

        const names = new Set(branches.map((b) => b.name));
        if (names.has(defaultBranch)) {
            kbBranchInput.value = defaultBranch;
        } else if (branches.length > 0) {
            kbBranchInput.value = branches[0].name;
        } else {
            kbBranchInput.value = defaultBranch;
        }

        let meta = `Loaded ${branches.length} branch${branches.length === 1 ? '' : 'es'}.`;
        if (truncated) {
            meta += ' List capped at 5,000 — type a branch name if yours is missing.';
        }
        meta += ' Use the field to filter suggestions or enter any branch.';
        if (kbBranchLoadMeta) kbBranchLoadMeta.textContent = meta;
    } catch (e) {
        kbBranchInput.placeholder = 'Type branch name';
        kbBranchInput.value = defaultBranch;
        kbBranchInput.disabled = false;
        populateBranchDatalist([{ name: defaultBranch, protected: false }]);
        if (kbBranchLoadMeta) kbBranchLoadMeta.textContent = 'Could not load branches — enter the name manually.';
        console.warn(e);
    }
}

document.querySelectorAll('.kb-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.kb-tab').forEach((t) => t.classList.remove('active'));
        document.querySelectorAll('.kb-panel').forEach((p) => p.classList.remove('active'));
        tab.classList.add('active');
        const name = tab.dataset.tab;
        const panel = document.getElementById(`kbPanel${name.charAt(0).toUpperCase() + name.slice(1)}`);
        if (panel) panel.classList.add('active');
        if (name === 'repo') {
            (async () => {
                await ensureGithubReposLoaded(true);
                if (kbRepoEditState) {
                    await applyKbRepoEditSelection();
                }
            })();
        }
    });
});

if (kbRepoSelect) {
    kbRepoSelect.addEventListener('change', () => {
        loadKbBranchesForSelection();
    });
}

if (kbLoadMoreRepos) {
    kbLoadMoreRepos.addEventListener('click', () => {
        ensureGithubReposLoaded(false);
    });
}

if (knowledgeBtn) {
    knowledgeBtn.addEventListener('click', () => {
        if (!isAuthenticated) return;
        openKbPanel();
    });
}
if (kbCloseBtn) kbCloseBtn.addEventListener('click', closeKbPanel);
if (kbOverlay) {
    kbOverlay.addEventListener('click', (e) => {
        if (e.target === kbOverlay) closeKbPanel();
    });
}

async function kbDetailFromResponse(resp) {
    try {
        const j = await resp.json();
        const d = j.detail;
        if (typeof d === 'string') return d;
        if (Array.isArray(d)) return d.map((x) => (typeof x === 'string' ? x : x.msg || JSON.stringify(x))).join('; ');
    } catch (_) {
        /* ignore */
    }
    return null;
}

if (kbSaveNoteBtn) {
    kbSaveNoteBtn.addEventListener('click', async () => {
        kbError.textContent = '';
        const title = document.getElementById('kbNoteTitle')?.value?.trim() || 'Note';
        const body = document.getElementById('kbNoteBody')?.value || '';
        try {
            if (kbEditingNoteId) {
                const resp = await fetch(
                    `/api/projects/${encodeURIComponent(currentProjectId)}/kb/items/${encodeURIComponent(kbEditingNoteId)}`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title, body })
                    }
                );
                if (!resp.ok) {
                    kbError.textContent = (await kbDetailFromResponse(resp)) || 'Could not update note.';
                    return;
                }
                cancelKbNoteEdit();
            } else {
                const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb/note`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, body })
                });
                const data = await resp.json();
                if (!data.ok) throw new Error('Save failed');
                const nb = document.getElementById('kbNoteBody');
                if (nb) nb.value = '';
            }
            loadKbPanelData();
        } catch (e) {
            kbError.textContent = 'Could not save note.';
        }
    });
}

if (kbSaveSnippetBtn) {
    kbSaveSnippetBtn.addEventListener('click', async () => {
        kbError.textContent = '';
        const title = document.getElementById('kbSnippetTitle')?.value?.trim() || 'Snippet';
        const code = document.getElementById('kbSnippetCode')?.value || '';
        try {
            if (kbEditingSnippetId) {
                const resp = await fetch(
                    `/api/projects/${encodeURIComponent(currentProjectId)}/kb/items/${encodeURIComponent(kbEditingSnippetId)}`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title, code })
                    }
                );
                if (!resp.ok) {
                    kbError.textContent = (await kbDetailFromResponse(resp)) || 'Could not update snippet.';
                    return;
                }
                cancelKbSnippetEdit();
            } else {
                const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb/snippet`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, code })
                });
                const data = await resp.json();
                if (!data.ok) throw new Error('Save failed');
                const sc = document.getElementById('kbSnippetCode');
                if (sc) sc.value = '';
            }
            loadKbPanelData();
        } catch (e) {
            kbError.textContent = 'Could not save snippet.';
        }
    });
}

if (kbSaveRepoBtn) {
    kbSaveRepoBtn.addEventListener('click', async () => {
        const wasRepoEdit = Boolean(kbRepoEditState);
        kbError.textContent = '';
        const branch = kbBranchInput?.value?.trim();
        if (!branch) {
            kbError.textContent = 'Enter a branch name.';
            return;
        }
        try {
            if (kbRepoEditState) {
                const { owner, repo, branch: oldBranch } = kbRepoEditState;
                const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb/repo`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        owner,
                        repo,
                        branch: oldBranch,
                        new_branch: branch
                    })
                });
                if (!resp.ok) {
                    kbError.textContent = (await kbDetailFromResponse(resp)) || 'Could not update branch.';
                    return;
                }
                cancelKbRepoEdit();
                loadKbPanelData();
                return;
            }

            const raw = kbRepoSelect?.value || '';
            if (!raw) {
                kbError.textContent = 'Select a repository.';
                return;
            }
            const i = raw.indexOf('\t');
            const owner = raw.slice(0, i);
            const repo = raw.slice(i + 1);
            if (!owner || !repo) {
                kbError.textContent = 'Select a repository.';
                return;
            }
            const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb/repo`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ owner, repo, branch })
            });
            const data = await resp.json();
            if (!data.ok) throw new Error('Link failed');
            loadKbPanelData();
        } catch (e) {
            kbError.textContent = wasRepoEdit ? 'Could not update branch.' : 'Could not link repository.';
        }
    });
}

if (kbCancelNoteEdit) {
    kbCancelNoteEdit.addEventListener('click', () => {
        kbError.textContent = '';
        cancelKbNoteEdit();
    });
}
if (kbCancelSnippetEdit) {
    kbCancelSnippetEdit.addEventListener('click', () => {
        kbError.textContent = '';
        cancelKbSnippetEdit();
    });
}
if (kbCancelRepoEdit) {
    kbCancelRepoEdit.addEventListener('click', () => {
        kbError.textContent = '';
        cancelKbRepoEdit();
    });
}

if (kbFileInput && kbFileLabel) {
    kbFileInput.addEventListener('change', () => {
        const f = kbFileInput.files?.[0];
        kbFileLabel.textContent = f ? f.name : 'No file selected';
    });
}

if (kbUploadBtn && kbFileInput) {
    kbUploadBtn.addEventListener('click', async () => {
        kbError.textContent = '';
        const file = kbFileInput.files?.[0];
        if (!file) {
            kbError.textContent = 'Choose a file first.';
            return;
        }
        const fd = new FormData();
        fd.append('file', file);
        try {
            const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb/upload`, {
                method: 'POST',
                body: fd
            });
            const data = await resp.json();
            if (!data.ok) throw new Error('Upload failed');
            kbFileInput.value = '';
            kbFileLabel.textContent = 'No file selected';
            loadKbPanelData();
        } catch (e) {
            kbError.textContent = 'Upload failed.';
        }
    });
}

if (newProjectBtn && newProjectOverlay) {
    newProjectBtn.addEventListener('click', () => {
        newProjectError.textContent = '';
        newProjectOverlay.classList.remove('hidden');
        document.getElementById('newProjectName')?.focus();
    });
}
if (newProjectCancelBtn) {
    newProjectCancelBtn.addEventListener('click', () => {
        newProjectOverlay.classList.add('hidden');
    });
}
if (newProjectForm) {
    newProjectForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        newProjectError.textContent = '';
        const name = document.getElementById('newProjectName')?.value?.trim();
        const description = document.getElementById('newProjectDesc')?.value?.trim() || '';
        if (!name) return;
        try {
            const resp = await fetch('/api/projects', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, description })
            });
            const data = await resp.json();
            if (!data.project) throw new Error('failed');
            currentProjectId = data.project.id;
            newProjectOverlay.classList.add('hidden');
            newProjectForm.reset();
            await loadProjects();
            await loadConversations();
            startNewChat();
        } catch (err) {
            newProjectError.textContent = 'Could not create project.';
        }
    });
}

initApp();

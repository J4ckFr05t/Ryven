/**
 * Ryven Frontend — WebSocket chat client with conversation memory & MCP support
 */

// ── State ─────────────────────────────────────────────────────────────────
let ws = null;
let currentModel = 'openai';
let currentConvId = null;
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
const modelBtns = document.querySelectorAll('.model-btn');
const statusDot = document.querySelector('.status-dot-live');
const statusText = document.querySelector('.status-text');
const newChatBtn = document.getElementById('newChatBtn');
const sidebar = document.getElementById('sidebar');
const sidebarToggle = document.getElementById('sidebarToggle');
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
const settingsForm = document.getElementById('settingsForm');
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
        <div class="message-avatar">👤</div>
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
        <div class="message-avatar">⚡</div>
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

modelBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        modelBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentModel = btn.dataset.model;
    });
});

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
    settingsBtn.addEventListener('click', () => {
        settingsError.textContent = '';
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

if (settingsForm) {
    settingsForm.addEventListener('submit', async (e) => {
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
    kbOverlay.classList.remove('hidden');
    loadKbPanelData();
}

function closeKbPanel() {
    if (kbOverlay) kbOverlay.classList.add('hidden');
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
                <button type="button" class="kb-item-del" data-owner="${escapeHtml(r.owner)}" data-repo="${escapeHtml(r.repo)}" data-branch="${escapeHtml(br)}">Remove</button>
            </li>`;
                  })
                  .join('')
            : '<li class="kb-item-meta">No linked repositories</li>';
        kbItemsList.innerHTML = items.length
            ? items
                  .map(
                      (it) => `
            <li>
                <span>
                    <span class="kb-item-meta">${escapeHtml(it.kind)}</span>
                    ${escapeHtml(it.title)}
                </span>
                <button type="button" class="kb-item-del" data-kb-id="${escapeHtml(it.id)}">Delete</button>
            </li>`
                  )
                  .join('')
            : '<li class="kb-item-meta">No items yet</li>';

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
            ensureGithubReposLoaded(true);
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

if (kbSaveNoteBtn) {
    kbSaveNoteBtn.addEventListener('click', async () => {
        kbError.textContent = '';
        const title = document.getElementById('kbNoteTitle')?.value?.trim() || 'Note';
        const body = document.getElementById('kbNoteBody')?.value || '';
        try {
            const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb/note`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, body })
            });
            const data = await resp.json();
            if (!data.ok) throw new Error('Save failed');
            document.getElementById('kbNoteBody').value = '';
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
            const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb/snippet`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, code })
            });
            const data = await resp.json();
            if (!data.ok) throw new Error('Save failed');
            document.getElementById('kbSnippetCode').value = '';
            loadKbPanelData();
        } catch (e) {
            kbError.textContent = 'Could not save snippet.';
        }
    });
}

if (kbSaveRepoBtn) {
    kbSaveRepoBtn.addEventListener('click', async () => {
        kbError.textContent = '';
        const raw = kbRepoSelect?.value || '';
        const branch = kbBranchInput?.value?.trim();
        if (!raw || !branch) {
            kbError.textContent = 'Select a repository and a branch.';
            return;
        }
        const i = raw.indexOf('\t');
        const owner = raw.slice(0, i);
        const repo = raw.slice(i + 1);
        if (!owner || !repo) {
            kbError.textContent = 'Select a repository.';
            return;
        }
        try {
            const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/kb/repo`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ owner, repo, branch })
            });
            const data = await resp.json();
            if (!data.ok) throw new Error('Link failed');
            loadKbPanelData();
        } catch (e) {
            kbError.textContent = 'Could not link repository.';
        }
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

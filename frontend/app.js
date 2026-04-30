/**
 * Jarvis Frontend — WebSocket chat client with conversation memory & MCP support
 */

// ── State ─────────────────────────────────────────────────────────────────
let ws = null;
let currentModel = 'openai';
let currentConvId = null;
let isProcessing = false;
let conversations = [];

// ── DOM Elements ──────────────────────────────────────────────────────────
const messagesEl = document.getElementById('messages');
const messagesContainer = document.getElementById('messagesContainer');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const welcomeScreen = document.getElementById('welcomeScreen');
const modelBtns = document.querySelectorAll('.model-btn');
const headerModel = document.getElementById('headerModel');
const statusDot = document.querySelector('.status-dot-live');
const statusText = document.querySelector('.status-text');
const newChatBtn = document.getElementById('newChatBtn');
const clearChatBtn = document.getElementById('clearChatBtn');
const sidebar = document.getElementById('sidebar');
const mobileMenuBtn = document.getElementById('mobileMenuBtn');
const sidebarToggle = document.getElementById('sidebarToggle');
const conversationList = document.getElementById('conversationList');
const githubToolIndicator = document.getElementById('githubToolIndicator');

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
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        console.log('Connected to Jarvis');
        statusDot.classList.add('connected');
        statusText.textContent = 'Connected';
        checkHealth();
    };

    ws.onclose = () => {
        console.log('Disconnected');
        statusDot.classList.remove('connected');
        statusText.textContent = 'Disconnected';
        setTimeout(connectWebSocket, 3000);
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

// ── Conversation Management ───────────────────────────────────────────────
async function loadConversations() {
    try {
        const resp = await fetch('/api/conversations');
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
    currentConvId = null;
    messagesEl.innerHTML = '';
    if (welcomeScreen) {
        messagesEl.appendChild(welcomeScreen);
        welcomeScreen.style.display = 'flex';
    }
    renderConversationList();
    ws.send(JSON.stringify({ type: 'new_conversation' }));
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
            loadConversations();
            break;
        case 'conversation_loaded':
            renderLoadedMessages(data.messages);
            break;
        case 'conversation_cleared':
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
    get_file_info: 'ℹ️', web_search: '🔍', tavily_search: '🌐'
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
        const truncated = result.length > 500 ? result.substring(0, 500) + '...' : result;
        resultEl.innerHTML = `<pre>${escapeHtml(truncated)}</pre>`;
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
    if (!text || isProcessing || !ws || ws.readyState !== WebSocket.OPEN) return;

    isProcessing = true;
    sendBtn.disabled = true;
    messageInput.disabled = true;

    addUserMessage(text);
    messageInput.value = '';
    messageInput.style.height = 'auto';

    ws.send(JSON.stringify({
        type: 'chat',
        message: text,
        model: currentModel
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
        headerModel.textContent = currentModel === 'openai' ? 'GPT-4o' : 'Gemini';
    });
});

newChatBtn.addEventListener('click', startNewChat);
clearChatBtn.addEventListener('click', startNewChat);

mobileMenuBtn.addEventListener('click', () => sidebar.classList.toggle('open'));
sidebarToggle.addEventListener('click', () => sidebar.classList.toggle('open'));

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

// ── Initialize ────────────────────────────────────────────────────────────
connectWebSocket();
loadConversations();
messageInput.focus();

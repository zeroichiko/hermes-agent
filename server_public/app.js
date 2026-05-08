// ChatGPT-CDP Bridge Web UI
// Based on llama.cpp server UI design

class ChatApp {
    constructor() {
        this.chats = JSON.parse(localStorage.getItem('chats') || '[]');
        this.currentChatId = null;
        this.isStreaming = false;
        this.apiSettings = JSON.parse(localStorage.getItem('apiSettings') || '{}');
        
        this.init();
    }

    init() {
        // Load settings
        this.loadSettings();
        
        // Create initial chat if none exists
        if (this.chats.length === 0) {
            this.createNewChat();
        } else {
            this.loadChat(this.chats[0].id);
        }
        
        // Setup event listeners
        this.setupEventListeners();
        
        // Update status
        this.updateStatus('online');
    }

    loadSettings() {
        const defaults = {
            apiUrl: 'http://localhost:8080',
            model: 'chatgpt-cdp',
            temperature: 0.7,
            maxTokens: 2048
        };
        
        Object.assign(this.apiSettings, defaults, this.apiSettings);
        
        // Update form fields
        document.getElementById('apiUrl').value = this.apiSettings.apiUrl;
        document.getElementById('model').value = this.apiSettings.model;
        document.getElementById('temperature').value = this.apiSettings.temperature;
        document.getElementById('maxTokens').value = this.apiSettings.maxTokens;
    }

    saveSettings() {
        this.apiSettings.apiUrl = document.getElementById('apiUrl').value;
        this.apiSettings.model = document.getElementById('model').value;
        this.apiSettings.temperature = parseFloat(document.getElementById('temperature').value) || 0.7;
        this.apiSettings.maxTokens = parseInt(document.getElementById('maxTokens').value) || 2048;
        
        localStorage.setItem('apiSettings', JSON.stringify(this.apiSettings));
        this.toggleSettings();
    }

    setupEventListeners() {
        // Auto-resize textarea
        const textarea = document.getElementById('userInput');
        textarea.addEventListener('input', () => {
            textarea.style.height = 'auto';
            textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
        });

        // Handle Enter to send (Shift+Enter for newline)
        textarea.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
    }

    createNewChat() {
        const chat = {
            id: Date.now().toString(),
            title: 'New chat',
            messages: []
        };
        
        this.chats.unshift(chat);
        this.saveChats();
        this.loadChat(chat.id);
    }

    loadChat(chatId) {
        this.currentChatId = chatId;
        const chat = this.chats.find(c => c.id === chatId);
        if (!chat) return;

        // Update UI
        document.getElementById('chatList').innerHTML = this.chats.map(c => 
            `<div class="chat-item ${c.id === chatId ? 'active' : ''}" onclick="app.loadChat('${c.id}')">${this.escapeHtml(c.title)}</div>`
        ).join('');

        // Render messages
        const messagesContainer = document.getElementById('chatMessages');
        if (chat.messages.length === 0) {
            messagesContainer.innerHTML = `
                <div class="welcome-message">
                    <h2>Welcome to ChatGPT-CDP Bridge</h2>
                    <p>Start a conversation by typing a message below.</p>
                </div>
            `;
        } else {
            messagesContainer.innerHTML = chat.messages.map(msg => 
                this.createMessageElement(msg.role, msg.content)
            ).join('');
        }

        // Scroll to bottom
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    createMessageElement(role, content) {
        const avatar = role === 'user' ? '👤' : '🤖';
        const roleClass = role === 'user' ? 'user' : 'assistant';
        
        // Convert markdown-like formatting
        const formattedContent = this.formatMessage(content);
        
        return `
            <div class="message ${roleClass}">
                <div class="message-avatar">${avatar}</div>
                <div class="message-content">${formattedContent}</div>
            </div>
        `;
    }

    formatMessage(content) {
        // Basic markdown-like formatting
        return content
            .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*]+)\*/g, '<em>$1</em>')
            .replace(/\n/g, '<br>');
    }

    async sendMessage() {
        const input = document.getElementById('userInput');
        const message = input.value.trim();
        if (!message || this.isStreaming) return;

        // Clear input
        input.value = '';
        input.style.height = 'auto';

        // Get current chat
        const chat = this.chats.find(c => c.id === this.currentChatId);
        if (!chat) return;

        // Add user message
        chat.messages.push({ role: 'user', content: message });
        
        // Update title if first message
        if (chat.messages.length === 1) {
            chat.title = message.substring(0, 30) + (message.length > 30 ? '...' : '');
        }

        this.saveChats();
        this.loadChat(chat.id);

        // Show loading
        this.isStreaming = true;
        document.getElementById('sendBtn').disabled = true;
        document.getElementById('userInput').disabled = true;

        // Add assistant message placeholder
        const messagesContainer = document.getElementById('chatMessages');
        const assistantMsgId = 'assistant-msg-' + Date.now();
        messagesContainer.innerHTML += `
            <div class="message assistant" id="${assistantMsgId}">
                <div class="message-avatar">🤖</div>
                <div class="message-content">
                    <div class="typing-indicator">
                        <span></span>
                        <span></span>
                        <span></span>
                    </div>
                </div>
            </div>
        `;
        messagesContainer.scrollTop = messagesContainer.scrollHeight;

        try {
            // Build messages array for API
            const apiMessages = chat.messages.map(msg => ({
                role: msg.role,
                content: msg.content
            }));

            // Call API with streaming
            await this.callApi(apiMessages, assistantMsgId);
        } catch (error) {
            // Show error
            const msgEl = document.getElementById(assistantMsgId);
            if (msgEl) {
                msgEl.querySelector('.message-content').innerHTML = 
                    `<span style="color: #f44336;">Error: ${this.escapeHtml(error.message)}</span>`;
            }
            this.updateStatus('error');
        } finally {
            this.isStreaming = false;
            document.getElementById('sendBtn').disabled = false;
            document.getElementById('userInput').disabled = false;
            document.getElementById('userInput').focus();
        }
    }

    async callApi(messages, assistantMsgId) {
        const apiUrl = this.apiSettings.apiUrl;
        const model = this.apiSettings.model;
        const temperature = this.apiSettings.temperature;
        const maxTokens = this.apiSettings.maxTokens;

        const response = await fetch(`${apiUrl}/v1/chat/completions`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                model: model,
                messages: messages,
                stream: true,
                temperature: temperature,
                max_tokens: maxTokens
            })
        });

        if (!response.ok) {
            throw new Error(`API error: ${response.status} ${response.statusText}`);
        }

        // Handle streaming response
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let assistantContent = '';
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            
            // Process SSE lines
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = line.slice(6);
                    if (data === '[DONE]') continue;

                    try {
                        const parsed = JSON.parse(data);
                        const delta = parsed.choices?.[0]?.delta?.content;
                        if (delta) {
                            assistantContent += delta;
                            
                            // Update message content
                            const msgEl = document.getElementById(assistantMsgId);
                            if (msgEl) {
                                const contentEl = msgEl.querySelector('.message-content');
                                contentEl.innerHTML = this.formatMessage(assistantContent);
                                
                                // Auto scroll
                                const messagesContainer = document.getElementById('chatMessages');
                                messagesContainer.scrollTop = messagesContainer.scrollHeight;
                            }
                        }
                    } catch (e) {
                        // Skip invalid JSON
                    }
                }
            }
        }

        // Save assistant message
        const chat = this.chats.find(c => c.id === this.currentChatId);
        if (chat) {
            chat.messages.push({ role: 'assistant', content: assistantContent });
            this.saveChats();
        }
    }

    updateStatus(status) {
        const indicator = document.getElementById('statusIndicator');
        indicator.className = 'status-indicator' + (status === 'error' ? ' error' : '');
    }

    saveChats() {
        localStorage.setItem('chats', JSON.stringify(this.chats));
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Global functions for onclick handlers
    createNewChat() {
        app.createNewChat();
    }

    loadChat(chatId) {
        app.loadChat(chatId);
    }

    sendMessage() {
        app.sendMessage();
    }

    toggleSettings() {
        const panel = document.getElementById('settingsPanel');
        panel.classList.toggle('open');
    }

    toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        sidebar.classList.toggle('hidden');
    }

    saveSettings() {
        app.saveSettings();
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new ChatApp();
});

// Make functions globally available
function handleInputKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        window.app.sendMessage();
    }
}

function sendMessage() {
    window.app.sendMessage();
}

function createNewChat() {
    window.app.createNewChat();
}

function toggleSettings() {
    window.app.toggleSettings();
}

function toggleSidebar() {
    window.app.toggleSidebar();
}

function saveSettings() {
    window.app.saveSettings();
}

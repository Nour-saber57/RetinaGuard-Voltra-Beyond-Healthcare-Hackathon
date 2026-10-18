const recordChatStyle = document.createElement('link');
recordChatStyle.rel = 'stylesheet';
recordChatStyle.href = 'chat.css';
document.head.append(recordChatStyle);

const recordChatRoot = document.createElement('div');
recordChatRoot.innerHTML = `
  <button class="record-chat-launcher" id="recordChatLauncher" type="button" aria-expanded="false" aria-controls="recordChatPanel" onclick="toggleRecordChat()" hidden>✧ Ask about my records</button>
  <section class="record-chat-panel" id="recordChatPanel" role="dialog" aria-label="Chat about my patient records" hidden>
    <header class="record-chat-header">
      <div><h2>RetinaGuard assistant</h2><p>Questions about your saved account records</p></div>
      <div class="record-chat-actions"><button type="button" onclick="resetRecordChat()">New chat</button><button type="button" aria-label="Close chat" onclick="toggleRecordChat()">×</button></div>
    </header>
    <section class="record-chat-consent" id="recordChatConsent">
      <h3>Before you start</h3>
      <p>Your question, recent chat, and a summary of your diabetes type, measurements, appointments, and screening records will be sent to Google Gemini to prepare an answer. RetinaGuard does not save chat history. Google processes the request under its Gemini API terms. Do not use this assistant for urgent care.</p>
      <label><input id="recordChatConsentCheck" type="checkbox" onchange="updateRecordChatConsent()"><span>I agree to send my question and the listed record summary to Google Gemini.</span></label>
      <button id="recordChatStart" type="button" onclick="startRecordChat()" disabled>Continue to chat</button>
    </section>
    <div class="record-chat-thread" id="recordChatThread" aria-live="polite" hidden></div>
    <p class="record-chat-status" id="recordChatStatus" role="status"></p>
    <form class="record-chat-form" id="recordChatForm" onsubmit="sendRecordChat(event)" hidden>
      <textarea id="recordChatInput" maxlength="1200" rows="2" placeholder="Ask about your saved records…" aria-label="Your question" required></textarea>
      <button id="recordChatSend" type="submit">Send</button>
    </form>
  </section>`;
document.body.append(recordChatRoot);

const recordChatPageStyle = document.createElement('style');
recordChatPageStyle.textContent = `
  .record-chat-panel.record-chat-page{position:relative;inset:auto;z-index:auto;width:100%;height:min(640px,calc(100vh - 330px));min-height:420px;max-width:960px;margin:18px auto 0;box-shadow:0 8px 24px #132d4018}
  .record-chat-panel.record-chat-page .record-chat-actions button[aria-label="Close chat"]{display:none}
  .record-chat-page-heading{margin:8px 0 16px}
  .record-chat-page-heading h1{font-size:30px;margin:0 0 8px}
  .record-chat-page-heading p,.record-chat-login p{color:#718796;font-size:13px;line-height:1.7;margin:0}
  .record-chat-login{max-width:650px;margin:28px auto;padding:24px;background:#fff;border:1px solid #dce7e4;border-radius:10px}
  .record-chat-login h2{margin:0 0 10px;font-size:20px}
  .record-chat-login .primary{margin-top:16px}
  @media(max-width:600px){.record-chat-panel.record-chat-page{height:calc(100vh - 300px);min-height:380px;margin-top:12px}.record-chat-page-heading h1{font-size:24px}}
`;
document.head.append(recordChatPageStyle);

let recordChatOpen = false;
let recordChatConsentGiven = false;
let recordChatBusy = false;
let recordChatTurns = [];
let recordChatOwner = null;
let recordChatSessionVersion = 0;

function clearRecordChatSession() {
  recordChatSessionVersion++;
  recordChatOpen = false;
  recordChatConsentGiven = false;
  recordChatBusy = false;
  recordChatTurns = [];
  document.getElementById('recordChatSend').disabled = false;
  document.getElementById('recordChatConsentCheck').checked = false;
  document.getElementById('recordChatStart').disabled = true;
  document.getElementById('recordChatConsent').hidden = false;
  document.getElementById('recordChatThread').replaceChildren();
  document.getElementById('recordChatThread').hidden = true;
  document.getElementById('recordChatForm').hidden = true;
  document.getElementById('recordChatStatus').textContent = '';
  document.getElementById('recordChatInput').value = '';
}

function recordChatSyncVisibility() {
  const available = mode === 'patient' && Boolean(apiToken && activeAccount);
  const owner = available ? String(activeAccount.id ?? activeAccount.email) : null;
  if (owner !== recordChatOwner) {
    clearRecordChatSession();
    recordChatOwner = owner;
  }
  const launcher = document.getElementById('recordChatLauncher');
  if (!launcher) return;
  const panel = document.getElementById('recordChatPanel');
  const onChatPage = mode === 'patient' && page === 'Chat assistant';
  if (onChatPage) {
    launcher.hidden = true;
    if (available) {
      const app = document.getElementById('app');
      app.innerHTML = `${patientPagePicker()}<header class="record-chat-page-heading"><h1>Chat with RetinaGuard</h1><p>Ask about your saved health measurements, screening records, appointments, and care progress. Answers are informational and are not a diagnosis.</p></header>`;
      panel.classList.add('record-chat-page');
      panel.setAttribute('role', 'region');
      panel.setAttribute('aria-label', 'Chat about my patient records');
      panel.hidden = false;
      app.append(panel);
    } else {
      if (panel.parentElement !== recordChatRoot) recordChatRoot.append(panel);
      panel.classList.remove('record-chat-page');
      panel.hidden = true;
      const app = document.getElementById('app');
      app.innerHTML = `${patientPagePicker()}<section class="record-chat-login"><h2>Sign in to use your personal chat</h2><p>The assistant can only look up records belonging to the patient account you sign in with.</p><button class="primary" type="button" onclick="showAuth('signin')">Sign in</button> <button class="secondary" type="button" onclick="showAuth('signup')">Create patient account</button></section>`;
    }
    return;
  }

  if (panel.parentElement !== recordChatRoot) recordChatRoot.append(panel);
  panel.classList.remove('record-chat-page');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Chat about my patient records');
  launcher.hidden = !available;
  if (!available) recordChatOpen = false;
  panel.hidden = !available || !recordChatOpen;
  launcher.setAttribute('aria-expanded', String(recordChatOpen));
}

function toggleRecordChat() {
  recordChatOpen = !recordChatOpen;
  document.getElementById('recordChatPanel').hidden = !recordChatOpen;
  document.getElementById('recordChatLauncher').setAttribute('aria-expanded', String(recordChatOpen));
  if (recordChatOpen && recordChatConsentGiven) document.getElementById('recordChatInput').focus();
}

function updateRecordChatConsent() {
  document.getElementById('recordChatStart').disabled = !document.getElementById('recordChatConsentCheck').checked;
}

function startRecordChat() {
  if (!document.getElementById('recordChatConsentCheck').checked) return;
  recordChatConsentGiven = true;
  document.getElementById('recordChatConsent').hidden = true;
  document.getElementById('recordChatThread').hidden = false;
  document.getElementById('recordChatForm').hidden = false;
  appendRecordChatBubble('assistant', 'Ask me about information saved in your RetinaGuard account. I can help you find dates and summarize entries, but I cannot diagnose or recommend treatment.');
  document.getElementById('recordChatInput').focus();
}

function appendRecordChatBubble(role, content) {
  const bubble = document.createElement('div');
  bubble.className = `record-chat-bubble ${role}`;
  bubble.textContent = content;
  const thread = document.getElementById('recordChatThread');
  thread.append(bubble);
  thread.scrollTop = thread.scrollHeight;
}

async function sendRecordChat(event) {
  event.preventDefault();
  if (!recordChatConsentGiven || recordChatBusy) return;
  const sessionVersion = recordChatSessionVersion;
  const input = document.getElementById('recordChatInput');
  const message = input.value.trim();
  if (!message) return;

  const history = recordChatTurns.slice(-10);
  appendRecordChatBubble('user', message);
  input.value = '';
  recordChatBusy = true;
  let requestFailed = false;
  document.getElementById('recordChatSend').disabled = true;
  document.getElementById('recordChatStatus').textContent = 'Checking your records…';

  try {
    const result = await apiRequest('/chat', {
      method: 'POST',
      body: JSON.stringify({ consent: true, message, history }),
    });
    if (sessionVersion !== recordChatSessionVersion) return;
    recordChatTurns.push({ role: 'user', content: message });
    recordChatTurns.push({ role: 'assistant', content: result.reply });
    appendRecordChatBubble('assistant', result.reply);
  } catch (error) {
    if (sessionVersion !== recordChatSessionVersion) return;
    requestFailed = true;
    document.getElementById('recordChatStatus').textContent = error.message;
    document.getElementById('recordChatThread').lastElementChild?.remove();
    input.value = message;
  } finally {
    if (sessionVersion === recordChatSessionVersion) {
      recordChatBusy = false;
      document.getElementById('recordChatSend').disabled = false;
      if (!requestFailed) document.getElementById('recordChatStatus').textContent = '';
      input.focus();
    }
  }
}

function resetRecordChat() {
  clearRecordChatSession();
  recordChatOpen = true;
  document.getElementById('recordChatPanel').hidden = false;
  document.getElementById('recordChatLauncher').setAttribute('aria-expanded', 'true');
}

const renderBeforeRecordChat = render;
render = function () {
  renderBeforeRecordChat();
  recordChatSyncVisibility();
};
recordChatSyncVisibility();

const MAX_FILE_SIZE = 10 * 1024 * 1024;
const PREVIEW_LENGTH = 400;
const UPLOAD_TITLE = "Arraste seus PDFs até aqui";
const RATE_LIMITED_MESSAGE = "Limite de uso da IA atingido. Aguarde um minuto e tente de novo.";
const CONNECTION_ERROR =
  "Não foi possível conectar à API. Verifique se o servidor está em execução e tente novamente.";

const fileInput = document.querySelector("#document-file");
const uploadArea = document.querySelector("#upload-area");
const uploadTitle = document.querySelector("#upload-title");
const uploadError = document.querySelector("#upload-error");
const activeDocumentPanel = document.querySelector("#active-document");
const activeName = document.querySelector("#active-name");
const activeMeta = document.querySelector("#active-meta");
const newChatButton = document.querySelector("#new-chat-button");
const chatHistory = document.querySelector("#chat-history");
const questionForm = document.querySelector("#question-form");
const questionInput = document.querySelector("#question");
const submitButton = document.querySelector("#submit-button");
const buttonLabel = document.querySelector("#button-label");
const statusMessage = document.querySelector("#status-message");
const authPanel = document.querySelector("#auth-panel");
const authScreen = document.querySelector(".auth-screen");
const workspace = document.querySelector(".workspace");
const authTitle = document.querySelector("#auth-title");
const authSubtitle = document.querySelector("#auth-subtitle");
const authPasswordLabel = document.querySelector("#auth-password-label");
const authConfirmPasswordField = document.querySelector("#auth-confirm-password-field");
const authConfirmPassword = document.querySelector("#auth-confirm-password");
const authForm = document.querySelector("#auth-form");
const authEmail = document.querySelector("#auth-email");
const authPassword = document.querySelector("#auth-password");
const authSubmit = document.querySelector("#auth-submit");
const authSubmitLabel = authSubmit.querySelector("span");
const authMessage = document.querySelector("#auth-message");
const loginTab = document.querySelector("#login-tab");
const signupTab = document.querySelector("#signup-tab");
const signoutButton = document.querySelector("#signout-button");
const accountEmail = document.querySelector("#account-email");
const signupLink = document.querySelector("#signup-link");
const forgotPassword = document.querySelector("#forgot-password");
const themeToggles = document.querySelectorAll("[data-theme-toggle]");
const themeLabels = document.querySelectorAll("[data-theme-label]");

let activeDocuments = [];
let isUploading = false;
let isAsking = false;
let authMode = "login";
let supabaseClient = null;
let accessToken = null;

function updateThemeControls() {
  const isDark = document.documentElement.dataset.theme === "dark";
  themeLabels.forEach((label) => {
    label.textContent = isDark ? "Modo claro" : "Modo escuro";
  });
  themeToggles.forEach((toggle) => {
    toggle.setAttribute("aria-label", isDark ? "Ativar modo claro" : "Ativar modo escuro");
    toggle.setAttribute("aria-pressed", String(isDark));
  });
}

themeToggles.forEach((toggle) => {
  toggle.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    localStorage.setItem("documind-theme", nextTheme);
    updateThemeControls();
  });
});

updateThemeControls();

class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function showStatus(message, isError = false) {
  statusMessage.textContent = message;
  statusMessage.classList.toggle("error", isError);
  statusMessage.hidden = false;
}

function hideStatus() {
  statusMessage.hidden = true;
}

function showUploadError(message) {
  uploadError.textContent = message;
  uploadError.hidden = false;
}

function getErrorMessage(payload, fallback) {
  if (typeof payload?.detail === "string") {
    return payload.detail;
  }

  if (payload?.detail && typeof payload.detail.message === "string") {
    return payload.detail.message;
  }

  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((error) => error.msg).join(" ");
  }

  return fallback;
}

function getErrorCode(payload) {
  return typeof payload?.detail?.code === "string" ? payload.detail.code : null;
}

async function requestJson(url, options, fallbackMessage) {
  const requestOptions = { ...options, headers: { ...(options?.headers || {}) } };
  if (supabaseClient) {
    const session = (await supabaseClient.auth.getSession()).data.session;
    accessToken = session?.access_token || null;
  }
  if (accessToken) {
    requestOptions.headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(url, requestOptions);
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    throw new ApiError(
      getErrorMessage(payload, fallbackMessage),
      response.status,
      getErrorCode(payload),
    );
  }

  return payload;
}

function showAuthMessage(message, isError = true) {
  authMessage.textContent = message;
  authMessage.classList.toggle("error", isError);
  authMessage.hidden = false;
}

function setAuthMode(mode) {
  authMode = mode;
  const signingUp = mode === "signup";
  authPanel.classList.remove("auth-mode-change");
  void authPanel.offsetWidth;
  authPanel.classList.add("auth-mode-change");
  loginTab.classList.toggle("is-active", !signingUp);
  signupTab.classList.toggle("is-active", signingUp);
  loginTab.setAttribute("aria-selected", String(!signingUp));
  signupTab.setAttribute("aria-selected", String(signingUp));
  authTitle.textContent = signingUp
    ? "Crie sua conta no DocuMind"
    : "Entre para consultar seus documentos";
  authSubtitle.textContent = signingUp
    ? "Cadastre-se para salvar seus documentos com segurança."
    : "Acesse sua conta para continuar.";
  authPasswordLabel.textContent = signingUp ? "Crie uma senha" : "Senha";
  authSubmitLabel.textContent = signingUp ? "Criar minha conta" : "Entrar no DocuMind";
  authPassword.autocomplete = signingUp ? "new-password" : "current-password";
  authConfirmPasswordField.hidden = !signingUp;
  authConfirmPassword.required = signingUp;
  authMessage.hidden = true;
}

function showAuthenticated(user) {
  authScreen.hidden = true;
  workspace.hidden = false;
  document.body.classList.add("is-authenticated");
  accountEmail.textContent = user.email || "Conta autenticada";
  updateControls();
}

function showUnauthenticated() {
  accessToken = null;
  activeDocuments = [];
  authPanel.hidden = false;
  authScreen.hidden = false;
  workspace.hidden = true;
  document.body.classList.remove("is-authenticated");
  resetUploadArea();
  chatHistory.replaceChildren();
  chatHistory.hidden = true;
  updateControls();
}

async function initializeAuth() {
  const config = await requestJson(
    "/config/public",
    {},
    "Não foi possível carregar a configuração de autenticação.",
  );
  if (!config.supabase_url || !config.supabase_publishable_key || !window.supabase) {
    showAuthMessage("A autenticação ainda não foi configurada no servidor.");
    return;
  }
  supabaseClient = window.supabase.createClient(
    config.supabase_url,
    config.supabase_publishable_key,
  );
  const { data } = await supabaseClient.auth.getSession();
  accessToken = data.session?.access_token || null;
  if (data.session?.user) {
    showAuthenticated(data.session.user);
  } else {
    showUnauthenticated();
  }
  supabaseClient.auth.onAuthStateChange((_event, session) => {
    accessToken = session?.access_token || null;
    if (session?.user) {
      showAuthenticated(session.user);
    } else {
      showUnauthenticated();
    }
  });
}

function describeError(error) {
  if (error instanceof TypeError) {
    return CONNECTION_ERROR;
  }

  if (error.code === "RATE_LIMITED") {
    return error.message || RATE_LIMITED_MESSAGE;
  }

  return error.message;
}

function validateFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    throw new Error("O arquivo precisa estar no formato PDF.");
  }

  if (file.size > MAX_FILE_SIZE) {
    throw new Error("O arquivo excede o limite máximo permitido de 10 MiB.");
  }
}

function updateControls() {
  const canAsk = activeDocuments.length > 0 && !isAsking;
  questionInput.disabled = !canAsk;
  submitButton.disabled = !canAsk;
  submitButton.classList.toggle("is-loading", isAsking);
  newChatButton.disabled = isAsking;
  fileInput.disabled = isUploading || activeDocuments.length > 0;
  uploadArea.classList.toggle("is-busy", isUploading);
  buttonLabel.textContent = isAsking ? "Buscando resposta..." : "Perguntar";
  questionInput.placeholder = activeDocuments.length > 0
    ? "Ex.: Quais são os principais prazos mencionados?"
    : "Envie PDFs para começar a perguntar.";
}

function formatPages(pages) {
  return pages === 1 ? "1 página" : `${pages} páginas`;
}

function showActiveDocuments() {
  const filenames = activeDocuments.map((item) => item.filename);
  const pages = activeDocuments.reduce((total, item) => total + item.pages, 0);
  activeName.textContent =
    `${activeDocuments.length} PDF${activeDocuments.length === 1 ? "" : "s"} na conversa`;
  activeName.title = filenames.join(", ");
  activeMeta.textContent = `${filenames.join(" · ")} · ${formatPages(pages)}`;
  uploadArea.hidden = true;
  activeDocumentPanel.hidden = false;
}

function resetUploadArea() {
  fileInput.value = "";
  uploadTitle.textContent = UPLOAD_TITLE;
  uploadArea.classList.remove("has-file");
  uploadArea.hidden = false;
  activeDocumentPanel.hidden = true;
}

async function uploadFiles(files) {
  if (files.length === 0 || activeDocuments.length > 0 || isUploading) {
    return;
  }

  uploadError.hidden = true;
  hideStatus();

  try {
    files.forEach(validateFile);
  } catch (error) {
    resetUploadArea();
    showUploadError(error.message);
    return;
  }

  isUploading = true;
  uploadTitle.textContent = `Analisando ${files.length} PDF${files.length === 1 ? "" : "s"}...`;
  uploadArea.classList.add("has-file");
  updateControls();

  try {
    const failures = [];
    for (const file of files) {
      try {
        const formData = new FormData();
        formData.append("file", file);
        const uploaded = await requestJson(
          "/documents/upload",
          { method: "POST", body: formData },
          "Não foi possível enviar o documento.",
        );
        activeDocuments.push(uploaded);
      } catch (error) {
        failures.push(`${file.name}: ${describeError(error)}`);
      }
    }

    if (activeDocuments.length > 0) {
      showActiveDocuments();
    } else {
      resetUploadArea();
    }
    if (failures.length > 0) {
      showUploadError(`Não foi possível enviar:\n${failures.join("\n")}`);
    }
  } finally {
    isUploading = false;
    updateControls();
  }

  if (activeDocuments.length > 0) {
    questionInput.focus();
  }
}

fileInput.addEventListener("change", () => {
  uploadFiles([...fileInput.files]);
});

for (const eventName of ["dragenter", "dragover"]) {
  uploadArea.addEventListener(eventName, (event) => {
    event.preventDefault();
    if (!isUploading) {
      uploadArea.classList.add("is-dragging");
    }
  });
}

for (const eventName of ["dragleave", "drop"]) {
  uploadArea.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadArea.classList.remove("is-dragging");
  });
}

uploadArea.addEventListener("drop", (event) => {
  uploadFiles([...event.dataTransfer.files]);
});

function createElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  if (text !== undefined) {
    element.textContent = text;
  }
  return element;
}

function previewText(text) {
  if (text.length <= PREVIEW_LENGTH) {
    return text;
  }

  const cut = text.slice(0, PREVIEW_LENGTH);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > PREVIEW_LENGTH * 0.7 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}

function createToggle(label, onToggle) {
  const toggle = createElement("button", "link-button", label);
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(expanded));
    onToggle(expanded);
  });
  return toggle;
}

function createSourceCard(source) {
  const card = createElement("article", "source-card");

  const header = createElement("p", "source-page");
  header.append(createElement("span", "source-ref", String(source.ref)));
  if (source.filename) {
    header.append(
      createElement("strong", "source-filename", source.filename),
      ` · Página ${source.page}`,
    );
  } else {
    header.append(`Página ${source.page}`);
  }
  card.append(header);

  const quote = source.quote.trim();
  const text = createElement("p", "source-text");

  if (quote) {
    card.append(createElement("blockquote", "source-quote", `“${quote}”`));
    text.textContent = source.text;
    text.hidden = true;
    const toggle = createToggle("Ver trecho completo", (expanded) => {
      text.hidden = !expanded;
      toggle.textContent = expanded ? "Ocultar trecho" : "Ver trecho completo";
    });
    card.append(text, toggle);
    return card;
  }

  const preview = previewText(source.text);
  text.textContent = preview;
  card.append(text);

  if (preview !== source.text) {
    const toggle = createToggle("Ver mais", (expanded) => {
      text.textContent = expanded ? source.text : preview;
      toggle.textContent = expanded ? "Ver menos" : "Ver mais";
    });
    card.append(toggle);
  }

  return card;
}

function renderAnswerText(container, answer, sources) {
  const locations = new Map(
    sources.map((source) => [
      String(source.ref),
      source.filename ? `${source.filename}, página ${source.page}` : `página ${source.page}`,
    ]),
  );
  const parts = answer.split(/(\[\d+\])/);
  const lastIndex = new Map();
  parts.forEach((part, index) => {
    const match = part.match(/^\[(\d+)\]$/);
    if (match) {
      lastIndex.set(match[1], index);
    }
  });

  parts.forEach((part, index) => {
    const match = part.match(/^\[(\d+)\]$/);
    if (!match) {
      const text = index < parts.length - 1 ? part.trimEnd() : part;
      if (text) {
        container.append(text);
      }
      return;
    }
    if (lastIndex.get(match[1]) !== index) {
      return;
    }
    const ref = createElement("sup", "citation-ref", match[1]);
    const location = locations.get(match[1]);
    ref.title = location === undefined ? `Fonte ${match[1]}` : `Fonte ${match[1]}, ${location}`;
    container.append(ref);
  });
}

function renderAnswer(answerBlock, result) {
  answerBlock.classList.remove("is-pending");
  answerBlock.replaceChildren();

  const text = createElement("p", "answer-text");

  if (!result.found) {
    answerBlock.classList.add("is-not-found");
    text.textContent = result.answer;
    answerBlock.append(text);
    return;
  }

  renderAnswerText(text, result.answer, result.sources);
  answerBlock.append(text);

  if (result.sources.length === 0) {
    return;
  }

  const sourcesSection = createElement("div", "sources-section");
  const title = createElement("h3", null, "Fontes ");
  title.append(createElement("span", null, String(result.sources.length)));
  const list = createElement("div", "sources-list");
  list.append(...result.sources.map(createSourceCard));
  sourcesSection.append(title, list);
  answerBlock.append(sourcesSection);
}

function addChatEntry(question) {
  const entry = createElement("article", "chat-entry");
  const questionBubble = createElement("p", "chat-question");
  questionBubble.append(createElement("span", "visually-hidden", "Você perguntou: "), question);

  const answerBlock = createElement("div", "chat-answer is-pending");
  answerBlock.append(createElement("p", "answer-text", "Procurando nos documentos..."));

  entry.append(questionBubble, answerBlock);
  chatHistory.append(entry);
  chatHistory.hidden = false;
  entry.scrollIntoView({ block: "nearest", behavior: "smooth" });
  return { entry, answerBlock };
}

questionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideStatus();

  const question = questionInput.value.trim();

  if (activeDocuments.length === 0 || isAsking) {
    return;
  }

  if (!question) {
    showStatus("Escreva uma pergunta antes de consultar.", true);
    questionInput.focus();
    return;
  }

  isAsking = true;
  updateControls();
  questionInput.value = "";
  const { entry, answerBlock } = addChatEntry(question);

  try {
    const result = await requestJson(
      "/documents/ask",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          document_ids: activeDocuments.map((item) => item.document_id),
        }),
      },
      "Não foi possível consultar os documentos.",
    );
    renderAnswer(answerBlock, result);
  } catch (error) {
    entry.remove();
    chatHistory.hidden = chatHistory.childElementCount === 0;
    questionInput.value = question;
    showStatus(describeError(error), true);
  } finally {
    isAsking = false;
    updateControls();
    questionInput.focus();
  }
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    questionForm.requestSubmit();
  }
});

newChatButton.addEventListener("click", async () => {
  if (activeDocuments.length === 0 || isAsking) {
    return;
  }

  newChatButton.disabled = true;
  hideStatus();

  try {
    await requestJson(
      "/documents",
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          document_ids: activeDocuments.map((item) => item.document_id),
        }),
      },
      "Não foi possível encerrar a conversa.",
    );
  } catch (error) {
    showStatus(describeError(error), true);
    newChatButton.disabled = false;
    return;
  }

  activeDocuments = [];
  chatHistory.replaceChildren();
  chatHistory.hidden = true;
  questionInput.value = "";
  uploadError.hidden = true;
  resetUploadArea();
  updateControls();
  fileInput.focus();
});

loginTab.addEventListener("click", () => setAuthMode("login"));
signupTab.addEventListener("click", () => setAuthMode("signup"));
signupLink.addEventListener("click", () => setAuthMode("signup"));

document.querySelectorAll("[data-password-toggle]").forEach((toggle) => {
  toggle.addEventListener("click", () => {
    const field = document.querySelector(`#${toggle.dataset.passwordToggle}`);
    const isVisible = field.type === "password";
    field.type = isVisible ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(isVisible));
    toggle.setAttribute("aria-label", isVisible ? "Ocultar senha" : "Mostrar senha");
    toggle.classList.toggle("is-visible", isVisible);
  });
});

forgotPassword.addEventListener("click", () => {
  showAuthMessage("A recuperação de senha será disponibilizada na próxima etapa.", false);
});

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!supabaseClient) {
    showAuthMessage("A autenticação ainda não foi configurada no servidor.");
    return;
  }
  authSubmit.disabled = true;
  authMessage.hidden = true;
  const email = authEmail.value.trim();
  const password = authPassword.value;
  if (authMode === "signup" && password !== authConfirmPassword.value) {
    authSubmit.disabled = false;
    showAuthMessage("As senhas não conferem.");
    authConfirmPassword.focus();
    return;
  }
  const result =
    authMode === "signup"
      ? await supabaseClient.auth.signUp({
          email,
          password,
        })
      : await supabaseClient.auth.signInWithPassword({ email, password });
  authSubmit.disabled = false;
  if (result.error) {
    showAuthMessage(result.error.message);
    return;
  }
  if (authMode === "signup" && !result.data.session) {
    showAuthMessage("Conta criada. Verifique seu e-mail para confirmar o cadastro.", false);
  }
  authForm.reset();
});

signoutButton.addEventListener("click", async () => {
  await supabaseClient?.auth.signOut();
});

updateControls();
initializeAuth().catch((error) => {
  showAuthMessage(describeError(error));
});

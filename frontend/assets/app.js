// APIWatch frontend. No framework, no dependencies: one module that talks to the
// FastAPI backend and prints every exchange to the request log ("tape").
// All user-supplied text is rendered with textContent, never innerHTML.

const API = String(window.APIWATCH_CONFIG?.apiUrl || "http://localhost:8000").replace(/\/+$/, "");
const STATUSES = ["pending", "in_progress", "done"];
const LABEL = { pending: "Pending", in_progress: "In progress", done: "Done" };
const PAGE_SIZE = 50;
const SESSION_KEY = "apiwatch.session";
const MAX_TAPE_ROWS = 80;

const $ = (selector, root = document) => root.querySelector(selector);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------- utilities

function randomId(length = 6) {
  const alphabet = "abcdefghijkmnpqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// The API stores UTC timestamps without an offset; read them as UTC.
function parseUtc(value) {
  if (!value) return new Date(NaN);
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
}

const relativeFormat = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
function relativeTime(value) {
  const seconds = Math.round((parseUtc(value).getTime() - Date.now()) / 1000);
  if (Number.isNaN(seconds)) return "";
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 3600) return relativeFormat.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return relativeFormat.format(Math.round(seconds / 3600), "hour");
  return relativeFormat.format(Math.round(seconds / 86400), "day");
}

const store = {
  get() {
    try {
      return JSON.parse(sessionStorage.getItem(SESSION_KEY));
    } catch {
      return null;
    }
  },
  set(value) {
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
    } catch {
      /* storage unavailable (private mode): the session just won't survive a reload */
    }
  },
  clear() {
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      /* nothing to clear */
    }
  },
};

// ---------------------------------------------------------------- toasts

function toast(message, { error = false } = {}) {
  const host = $("#toasts");
  const node = document.createElement("div");
  node.className = error ? "toast error" : "toast";
  node.textContent = message;
  host.append(node);
  while (host.children.length > 3) host.firstElementChild.remove();
  setTimeout(() => node.remove(), error ? 7000 : 4500);
}

// ---------------------------------------------------------------- request log

const tape = {
  root: $("#tape"),
  rows: $("#tape-rows"),
  empty: $("#tape-empty"),
  badge: $("#tape-badge"),
  count: 0,
};

function logRequest({ method, path, status, ms, detail, note }) {
  const row = document.createElement("li");
  row.className = "row";
  row.dataset.class = status ? String(Math.floor(status / 100)) : "0";

  const line = document.createElement("div");
  line.className = "row-line";
  const parts = [
    ["row-method", method],
    ["row-path", path],
    ["row-code", status ? String(status) : "ERR"],
    ["row-ms", `${ms} ms`],
  ];
  for (const [cls, text] of parts) {
    const span = document.createElement("span");
    span.className = cls;
    span.textContent = text;
    line.append(span);
  }
  line.children[1].title = path;
  row.append(line);

  if (detail) {
    const d = document.createElement("p");
    d.className = "row-detail";
    d.textContent = detail;
    row.append(d);
  }
  if (note) {
    const n = document.createElement("p");
    n.className = "row-note";
    n.textContent = note;
    row.append(n);
  }

  const bar = document.createElement("div");
  bar.className = "row-bar";
  const fill = document.createElement("span");
  bar.append(fill);
  row.append(bar);

  tape.rows.prepend(row);
  // Log scale: 10 ms is a sliver, 1 s fills the track.
  const width = Math.max(3, Math.min(100, (Math.log10(Math.max(ms, 1)) / 3) * 100));
  requestAnimationFrame(() => {
    fill.style.width = `${width}%`;
  });

  while (tape.rows.children.length > MAX_TAPE_ROWS) tape.rows.lastElementChild.remove();
  tape.count += 1;
  tape.badge.textContent = String(tape.count);
  tape.empty.hidden = true;
}

function clearTape() {
  tape.rows.replaceChildren();
  tape.count = 0;
  tape.badge.textContent = "0";
  tape.empty.hidden = false;
}

// ---------------------------------------------------------------- HTTP

let session = null; // { token, username, expiresAt, guestPassword? }

function validationMessage(data) {
  const first = Array.isArray(data?.detail) ? data.detail[0] : null;
  if (!first) return "Some input wasn't accepted.";
  const field = first.loc?.[first.loc.length - 1];
  const name = typeof field === "string" ? field.replace(/_/g, " ") : "input";
  return `${name.charAt(0).toUpperCase()}${name.slice(1)}: ${first.msg}`;
}

function defaultNote(result) {
  const { status, data } = result;
  if (status === 0) return "No response. The API may be starting up or unreachable.";
  if (status === 401) return "Rejected: no valid token was sent.";
  if (status === 404) return "Not found. Tasks owned by other accounts look exactly like this too.";
  if (status === 409) return typeof data?.detail === "string" ? data.detail : "Conflict.";
  if (status === 422) return `Rejected by validation. ${validationMessage(data)}`;
  if (status === 429) return "Rate limited: too many requests from this client in the last minute.";
  if (status >= 500) return "Server error. The body carries no internal details by design.";
  return null;
}

function errorMessage(result, fallback) {
  const { status, data } = result;
  if (status === 0) return "Can't reach the API right now. It may still be starting; try again in a few seconds.";
  if (status === 422) return validationMessage(data);
  if (status === 429) return "Too many requests in the last minute. Wait a moment and try again.";
  if (status >= 500) return "The API hit an unexpected error. Try again.";
  if (typeof data?.detail === "string") return data.detail;
  return fallback;
}

async function request(method, path, options = {}) {
  const { body, form, headers = {}, auth = true, note, detail, log = true, timeout = 20000 } = options;
  const sendHeaders = { Accept: "application/json", ...headers };
  let payload;
  if (form) {
    payload = new URLSearchParams(form);
    sendHeaders["Content-Type"] = "application/x-www-form-urlencoded";
  } else if (body !== undefined) {
    payload = JSON.stringify(body);
    sendHeaders["Content-Type"] = "application/json";
  }
  if (auth && session?.token) sendHeaders.Authorization = `Bearer ${session.token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const started = performance.now();
  let response = null;
  let data = null;
  try {
    response = await fetch(`${API}${path}`, {
      method,
      headers: sendHeaders,
      body: payload,
      signal: controller.signal,
      mode: "cors",
      credentials: "omit",
      cache: "no-store",
    });
    const text = await response.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null; // e.g. an HTML page from the host while the Space is starting
      }
    }
  } catch {
    response = null;
  } finally {
    clearTimeout(timer);
  }

  const result = {
    ok: Boolean(response?.ok) && (data !== null || response.status === 204),
    status: response ? response.status : 0,
    data,
    ms: Math.round(performance.now() - started),
  };
  if (log) {
    const custom = typeof note === "function" ? note(result) : note;
    logRequest({
      method,
      path,
      status: result.status,
      ms: result.ms,
      detail: typeof detail === "function" ? detail(result) : detail,
      note: custom ?? defaultNote(result),
    });
  }
  return result;
}

// Authenticated request: on 401, a guest session silently signs in again once;
// anyone else is signed out with an explanation.
async function authed(method, path, options = {}, retried = false) {
  const result = await request(method, path, options);
  if (result.status !== 401 || !session) return result;
  if (session.guestPassword && !retried) {
    const renewed = await signIn(session.username, session.guestPassword, {
      guestPassword: session.guestPassword,
      renew: true,
    });
    if (renewed) return authed(method, path, options, true);
    // The guest's own credentials were refused, so the account itself is gone:
    // the free-tier demo database was reset (restart or redeploy).
    endSession("The demo data was reset, so this guest board is gone. Start a new guest session to continue.");
    return result;
  }
  endSession("Your session expired. Sign in again to continue.");
  return result;
}

// ---------------------------------------------------------------- API status

const statusEl = $("#api-status");
let waking = false;

function setStatus(state, text, title = "") {
  statusEl.dataset.state = state;
  $(".status-text", statusEl).textContent = text;
  statusEl.title = title;
  $("#status-retry").hidden = state !== "down";
}

function readyNote(result) {
  return result.ok ? "Ready: the database answered a test query." : null;
}

async function waitForApi() {
  if (waking) return false;
  waking = true;
  setStatus("checking", "Checking the API");
  try {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      const result = await request("GET", "/health/ready", {
        auth: false,
        log: attempt === 0,
        timeout: 15000,
        note: readyNote,
      });
      if (result.ok) {
        if (attempt > 0) {
          logRequest({ method: "GET", path: "/health/ready", status: 200, ms: result.ms, note: "Awake and ready." });
        }
        setStatus("ready", `API ready, ${result.ms} ms`, "Round trip of the last readiness check");
        return true;
      }
      setStatus(
        "waking",
        "Waking the API",
        "The backend runs on free hosting that sleeps when idle. Starting it can take a minute."
      );
      await sleep(attempt < 4 ? 3000 : 6000);
    }
    setStatus("down", "API unreachable");
    return false;
  } finally {
    waking = false;
  }
}

async function heartbeat() {
  if (document.hidden || waking) return;
  const result = await request("GET", "/health/ready", { auth: false, log: false, timeout: 10000 });
  if (result.ok) setStatus("ready", `API ready, ${result.ms} ms`, "Round trip of the last readiness check");
  else waitForApi();
}

// ---------------------------------------------------------------- views

const views = { intro: $("#view-intro"), workspace: $("#view-workspace") };

function showIntro() {
  views.intro.hidden = false;
  views.workspace.hidden = true;
  $("#account").hidden = true;
  $("#tape-lab").hidden = true;
}

async function enterWorkspace() {
  views.intro.hidden = true;
  views.workspace.hidden = false;
  $("#account").hidden = false;
  $("#tape-lab").hidden = false;
  const who = session.guestPassword ? `${session.username} (guest)` : session.username;
  $("#account-name").textContent = who;
  labelButton($("#sign-out"), `Sign out ${who}`);
  $("#workspace-title").focus({ preventScroll: true });
  await loadBoard();
  $("#new-title").focus();
}

function endSession(message) {
  session = null;
  store.clear();
  resetBoard();
  showIntro();
  if (message) toast(message, { error: true });
}

// ---------------------------------------------------------------- auth

async function signIn(username, password, { guestPassword, renew = false } = {}) {
  const result = await request("POST", "/auth/login", {
    form: { username, password },
    auth: false,
    detail: `username=${username}`,
    note: (r) => {
      if (r.ok) {
        const minutes = Math.round((r.data.expires_in || 1800) / 60);
        return renew
          ? `Token expired, so the guest session signed in again. New token valid ${minutes} min.`
          : `Signed token issued, valid for ${minutes} minutes.`;
      }
      if (r.status === 401) {
        return "Rejected. Wrong password and unknown username get the same answer, so usernames can't be probed.";
      }
      return null;
    },
  });
  if (!result.ok) return renew ? false : result;
  session = {
    token: result.data.access_token,
    username,
    expiresAt: Date.now() + (result.data.expires_in || 1800) * 1000,
    ...(guestPassword ? { guestPassword } : {}),
  };
  store.set(session);
  return renew ? true : result;
}

async function register(username, password) {
  return request("POST", "/auth/register", {
    body: { username, password },
    auth: false,
    detail: `username=${username}`,
    note: (r) =>
      r.status === 201
        ? "Account created. Only a bcrypt hash of the password is stored."
        : r.status === 409
          ? "Username already taken."
          : null,
  });
}

function setBusy(button, busy) {
  button.disabled = busy;
  if (busy) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
}

async function startGuest(button) {
  setBusy(button, true);
  try {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const username = `guest-${randomId()}`;
      const password = `${randomId(12)}-${randomId(12)}`;
      const created = await register(username, password);
      if (created.status === 409) continue;
      if (!created.ok) {
        toast(errorMessage(created, "Couldn't start a guest session."), { error: true });
        return;
      }
      const signed = await signIn(username, password, { guestPassword: password });
      if (signed.ok) await enterWorkspace();
      else toast(errorMessage(signed, "Couldn't sign in the guest account."), { error: true });
      return;
    }
    toast("Couldn't pick a free guest name. Try again.", { error: true });
  } finally {
    setBusy(button, false);
  }
}

// Field rules: return an error string, or "" when valid.
const rules = {
  "login-username": (v) => (v.trim() ? "" : "Enter your username."),
  "login-password": (v) => (v ? "" : "Enter your password."),
  "register-username": (v) => {
    const s = v.trim();
    if (!s) return "Choose a username.";
    if (s.length < 3 || s.length > 50) return "Use 3 to 50 characters.";
    if (!/^[A-Za-z0-9_.-]+$/.test(s)) return "Use only letters, numbers, dots, dashes or underscores.";
    return "";
  },
  "register-password": (v) => {
    if (v.length < 8) return "Use at least 8 characters.";
    if (new TextEncoder().encode(v).length > 72) return "Use at most 72 characters.";
    return "";
  },
  "new-title": (v) => {
    if (!v.trim()) return "Give the task a title.";
    if (v.trim().length > 200) return "Keep the title under 200 characters.";
    return "";
  },
};

function showFieldError(input, message) {
  const error = document.getElementById(`${input.id}-error`);
  if (message) {
    input.setAttribute("aria-invalid", "true");
    if (error) {
      error.textContent = message;
      error.hidden = false;
      input.setAttribute("aria-errormessage", error.id);
    }
  } else {
    input.removeAttribute("aria-invalid");
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
  }
}

function validateInput(input) {
  const rule = rules[input.id];
  const message = rule ? rule(input.value) : "";
  showFieldError(input, message);
  return !message;
}

function wireValidation(root) {
  for (const input of root.querySelectorAll("input")) {
    if (!rules[input.id]) continue;
    input.addEventListener("blur", () => {
      if (input.value || input.hasAttribute("aria-invalid")) validateInput(input);
    });
    input.addEventListener("input", () => {
      if (input.getAttribute("aria-invalid") === "true") validateInput(input);
    });
  }
}

function validateForm(form) {
  let firstInvalid = null;
  for (const input of form.querySelectorAll("input")) {
    if (rules[input.id] && !validateInput(input) && !firstInvalid) firstInvalid = input;
  }
  firstInvalid?.focus();
  return !firstInvalid;
}

function formError(form, message) {
  const node = $(".form-error", form);
  node.textContent = message || "";
  node.hidden = !message;
}

async function onLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  formError(form, "");
  if (!validateForm(form)) return;
  const button = $("button[type=submit]", form);
  setBusy(button, true);
  try {
    const username = $("#login-username").value.trim();
    const result = await signIn(username, $("#login-password").value);
    if (result.ok) {
      form.reset();
      await enterWorkspace();
    } else if (result.status === 401) {
      formError(form, "That username and password don't match. Check both and try again.");
    } else {
      formError(form, errorMessage(result, "Couldn't sign in."));
    }
  } finally {
    setBusy(button, false);
  }
}

async function onRegister(event) {
  event.preventDefault();
  const form = event.currentTarget;
  formError(form, "");
  if (!validateForm(form)) return;
  const button = $("button[type=submit]", form);
  setBusy(button, true);
  try {
    const username = $("#register-username").value.trim();
    const password = $("#register-password").value;
    const created = await register(username, password);
    if (created.status === 409) {
      showFieldError($("#register-username"), "That username is taken. Try another.");
      $("#register-username").focus();
      return;
    }
    if (!created.ok) {
      formError(form, errorMessage(created, "Couldn't create the account."));
      return;
    }
    const signed = await signIn(username, password);
    if (signed.ok) {
      form.reset();
      toast("Account created");
      await enterWorkspace();
    } else {
      formError(form, errorMessage(signed, "Account created, but signing in failed. Use the Sign in tab."));
    }
  } finally {
    setBusy(button, false);
  }
}

function wireTabs() {
  const tabs = [...document.querySelectorAll('.segmented [role="tab"]')];
  const select = (tab, focus = true) => {
    for (const t of tabs) {
      const selected = t === tab;
      t.setAttribute("aria-selected", String(selected));
      t.tabIndex = selected ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !selected;
    }
    if (focus) tab.focus();
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(tab, false));
    tab.addEventListener("keydown", (event) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (step) {
        event.preventDefault();
        select(tabs[(index + step + tabs.length) % tabs.length]);
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        select(tabs[event.key === "Home" ? 0 : tabs.length - 1]);
      }
    });
  });
}

// ---------------------------------------------------------------- board

const board = {};
function resetBoard() {
  for (const status of STATUSES) board[status] = { items: [], total: 0, loaded: 0 };
  renderBoard();
}

function columnEl(status) {
  return document.querySelector(`.column[data-status="${status}"]`);
}

function findTask(id) {
  for (const status of STATUSES) {
    const task = board[status].items.find((t) => t.id === id);
    if (task) return task;
  }
  return null;
}

function sortItems(items) {
  items.sort((a, b) => parseUtc(b.created_at) - parseUtc(a.created_at));
}

function removeFromBoard(id) {
  for (const status of STATUSES) {
    const col = board[status];
    const before = col.items.length;
    col.items = col.items.filter((t) => t.id !== id);
    if (col.items.length !== before) {
      col.total = Math.max(0, col.total - 1);
      col.loaded = Math.max(0, col.loaded - 1);
    }
  }
}

function upsertTask(task) {
  removeFromBoard(task.id);
  const col = board[task.status];
  col.items.push(task);
  col.total += 1;
  col.loaded += 1;
  sortItems(col.items);
}

async function loadColumn(status, offset) {
  const result = await authed("GET", `/tasks?status=${status}&limit=${PAGE_SIZE}&offset=${offset}`, {
    note: (r) =>
      r.ok
        ? `${r.data.items.length} of ${r.data.total} ${LABEL[status].toLowerCase()} tasks, only from your account.`
        : null,
  });
  return result;
}

async function loadBoard() {
  resetBoard();
  const results = await Promise.all(STATUSES.map((s) => loadColumn(s, 0)));
  if (!session) return;
  results.forEach((result, i) => {
    const status = STATUSES[i];
    if (result.ok) {
      board[status] = { items: result.data.items, total: result.data.total, loaded: result.data.items.length };
    }
  });
  const failed = results.find((r) => !r.ok && r.status !== 401);
  if (failed) toast(errorMessage(failed, "Couldn't load your tasks."), { error: true });
  renderBoard();
}

async function loadMore(status, button) {
  setBusy(button, true);
  const result = await loadColumn(status, board[status].loaded);
  setBusy(button, false);
  if (!result.ok) {
    toast(errorMessage(result, "Couldn't load more tasks."), { error: true });
    return;
  }
  const col = board[status];
  const known = new Set(col.items.map((t) => t.id));
  col.items.push(...result.data.items.filter((t) => !known.has(t.id)));
  col.total = result.data.total;
  col.loaded += result.data.items.length;
  sortItems(col.items);
  renderBoard();
}

function renderBoard(flashId) {
  let total = 0;
  for (const status of STATUSES) {
    const col = board[status] || { items: [], total: 0, loaded: 0 };
    total += col.total;
    const el = columnEl(status);
    $("[data-total]", el).textContent = String(col.total);
    const list = $("[data-list]", el);
    list.replaceChildren();
    if (col.items.length === 0) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent =
        status === "pending"
          ? "Nothing pending. Add a task above."
          : `Drag a task here, or use its move button.`;
      list.append(empty);
    }
    for (const task of col.items) list.append(buildCard(task, task.id === flashId));
    $("[data-more]", el).hidden = col.loaded >= col.total;
  }
  const counts = STATUSES.map((s) => `${board[s]?.total ?? 0} ${LABEL[s].toLowerCase()}`).join(", ");
  $("#task-count").textContent = total ? `${total} ${total === 1 ? "task" : "tasks"}: ${counts}` : "No tasks yet";
}

function buildCard(task, flash) {
  const node = $("#card-template").content.firstElementChild.cloneNode(true);
  node.dataset.id = String(task.id);
  $(".card-title", node).textContent = task.title;
  const desc = $(".card-desc", node);
  if (task.description) desc.textContent = task.description;
  else desc.remove();
  const updated = relativeTime(task.updated_at);
  const meta = $(".card-meta", node);
  meta.textContent = `v${task.version}, updated ${updated || "recently"}`;
  meta.title = `Version ${task.version}. Created ${parseUtc(task.created_at).toLocaleString()}`;

  const index = STATUSES.indexOf(task.status);
  const back = $('[data-action="back"]', node);
  const forward = $('[data-action="forward"]', node);
  if (index === 0) back.remove();
  else labelButton(back, `Move to ${LABEL[STATUSES[index - 1]]}`);
  if (index === STATUSES.length - 1) forward.remove();
  else labelButton(forward, `Move to ${LABEL[STATUSES[index + 1]]}`);
  labelButton($('[data-action="edit"]', node), "Edit task");
  labelButton($('[data-action="delete"]', node), "Delete task");

  if (flash) node.classList.add("flash");
  return node;
}

function labelButton(button, label) {
  button.setAttribute("aria-label", label);
  button.title = label;
}

function focusCard(id, action) {
  const card = document.querySelector(`.card[data-id="${id}"]`);
  const target = (action && card?.querySelector(`[data-action="${action}"]`)) || card?.querySelector("button");
  target?.focus();
}

// ---------------------------------------------------------------- task writes

function putBody(task, changes) {
  return {
    title: changes.title ?? task.title,
    description: changes.description !== undefined ? changes.description : task.description,
    status: changes.status ?? task.status,
    version: changes.version ?? task.version,
  };
}

function putNote(sentVersion) {
  return (r) => {
    if (r.ok) return `Saved. Version ${sentVersion} became ${r.data.version}.`;
    if (r.status === 409) {
      return `Rejected with 409: this save was based on version ${sentVersion}, which is no longer current. Nothing was overwritten.`;
    }
    return null;
  };
}

async function refetchTask(id) {
  const result = await authed("GET", `/tasks/${id}`, {
    note: (r) => (r.ok ? `Latest copy is version ${r.data.version}.` : null),
  });
  return result.ok ? result.data : null;
}

async function moveTask(task, status, focusAction) {
  const sent = task.version;
  const result = await authed("PUT", `/tasks/${task.id}`, {
    body: putBody(task, { status }),
    detail: `version=${sent}, status=${status}`,
    note: putNote(sent),
  });
  if (result.ok) {
    upsertTask(result.data);
    renderBoard(result.data.id);
    toast(`Moved to ${LABEL[status]}`);
    focusCard(result.data.id, focusAction);
    return;
  }
  if (result.status === 409) {
    const latest = await refetchTask(task.id);
    if (latest) {
      upsertTask(latest);
      renderBoard(latest.id);
    }
    toast("This task changed somewhere else, so the move was rejected. Showing the latest version.", { error: true });
    return;
  }
  if (result.status === 404) {
    removeFromBoard(task.id);
    renderBoard();
    toast("That task no longer exists.", { error: true });
    return;
  }
  if (result.status !== 401) toast(errorMessage(result, "Couldn't move the task."), { error: true });
}

function openEditor(card, task) {
  const view = $(".card-view", card);
  view.hidden = true;
  card.draggable = false;
  let baseVersion = task.version;

  const form = document.createElement("form");
  form.className = "card-edit";
  form.noValidate = true;
  const uid = `edit-${task.id}`;
  form.innerHTML = `
    <div class="field">
      <label for="${uid}-title">Title</label>
      <input id="${uid}-title" maxlength="200" required />
      <p class="field-error" id="${uid}-title-error" hidden></p>
    </div>
    <div class="field">
      <label for="${uid}-desc">Details <span class="optional">(optional)</span></label>
      <textarea id="${uid}-desc" maxlength="2000" rows="3"></textarea>
    </div>
    <div class="field">
      <label for="${uid}-status">Status</label>
      <select id="${uid}-status">
        <option value="pending">Pending</option>
        <option value="in_progress">In progress</option>
        <option value="done">Done</option>
      </select>
    </div>
    <p class="notice" role="alert" hidden></p>
    <div class="card-buttons">
      <button class="button primary" type="submit">Save changes</button>
      <button class="button secondary" type="button" data-cancel>Cancel</button>
    </div>`;
  const title = form.querySelector(`#${uid}-title`);
  const desc = form.querySelector(`#${uid}-desc`);
  const status = form.querySelector(`#${uid}-status`);
  const notice = form.querySelector(".notice");
  title.value = task.title;
  desc.value = task.description || "";
  status.value = task.status;
  rules[title.id] = rules["new-title"];
  wireValidation(form);

  const close = (focusId = task.id) => {
    delete rules[title.id];
    form.remove();
    view.hidden = false;
    card.draggable = true;
    card.classList.remove("conflict");
    focusCard(focusId, "edit");
  };

  form.querySelector("[data-cancel]").addEventListener("click", () => close());
  form.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!validateInput(title)) {
      title.focus();
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true);
    const sent = baseVersion;
    const result = await authed("PUT", `/tasks/${task.id}`, {
      body: {
        title: title.value.trim(),
        description: desc.value.trim() || null,
        status: status.value,
        version: sent,
      },
      detail: `version=${sent}`,
      note: putNote(sent),
    });
    setBusy(button, false);
    if (result.ok) {
      delete rules[title.id];
      upsertTask(result.data);
      renderBoard(result.data.id);
      focusCard(result.data.id, "edit");
      toast("Changes saved");
      return;
    }
    if (result.status === 409) {
      const latest = await refetchTask(task.id);
      if (latest) {
        baseVersion = latest.version;
        card.classList.add("conflict");
        notice.textContent = `Someone saved version ${latest.version} while you were editing. Your edits are still here: save again to apply them on top of it, or cancel to keep theirs.`;
        notice.hidden = false;
        Object.assign(task, latest);
      }
      return;
    }
    if (result.status === 404) {
      removeFromBoard(task.id);
      renderBoard();
      toast("That task no longer exists.", { error: true });
      return;
    }
    if (result.status !== 401) {
      notice.textContent = errorMessage(result, "Couldn't save the changes.");
      notice.hidden = false;
    }
  });

  card.append(form);
  title.focus();
  title.select();
}

function openDeleteConfirm(card, task) {
  const view = $(".card-view", card);
  view.hidden = true;
  card.draggable = false;
  const box = document.createElement("div");
  box.className = "card-confirm";
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "Confirm delete");
  const text = document.createElement("p");
  text.textContent = `Delete “${task.title}”? This can't be undone.`;
  const buttons = document.createElement("div");
  buttons.className = "card-buttons";
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "button danger";
  confirm.textContent = "Delete task";
  const keep = document.createElement("button");
  keep.type = "button";
  keep.className = "button secondary";
  keep.textContent = "Keep task";
  buttons.append(confirm, keep);
  box.append(text, buttons);
  card.append(box);

  const close = () => {
    box.remove();
    view.hidden = false;
    card.draggable = true;
    focusCard(task.id, "delete");
  };
  keep.addEventListener("click", close);
  box.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
  confirm.addEventListener("click", async () => {
    setBusy(confirm, true);
    const result = await authed("DELETE", `/tasks/${task.id}`, {
      note: (r) => (r.status === 204 ? "Deleted. The same request again would return 404." : null),
    });
    if (result.status === 204 || result.status === 404) {
      removeFromBoard(task.id);
      renderBoard();
      toast(result.status === 204 ? "Task deleted" : "That task was already gone.");
      $("#new-title").focus();
      return;
    }
    setBusy(confirm, false);
    if (result.status !== 401) toast(errorMessage(result, "Couldn't delete the task."), { error: true });
  });
  keep.focus();
}

async function createTask(fields, { key = uuid(), note } = {}) {
  const send = () =>
    authed("POST", "/tasks", {
      body: fields,
      headers: { "Idempotency-Key": key },
      detail: `Idempotency-Key: ${key.slice(0, 8)}…`,
      note:
        note ||
        ((r) => (r.status === 201 ? `Created task #${r.data.id}. Retrying with this key can't make a duplicate.` : null)),
    });
  let result = await send();
  if (result.status === 0) {
    // The request may or may not have reached the server. Because it carries an
    // Idempotency-Key, sending it again is safe: worst case we get the original back.
    logRequest({ method: "POST", path: "/tasks", status: 0, ms: 0, note: "No response, so retrying with the same key." });
    result = await send();
  }
  return result;
}

async function onCompose(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const titleInput = $("#new-title");
  if (!validateInput(titleInput)) {
    titleInput.focus();
    return;
  }
  const button = $('button[type="submit"]', form);
  setBusy(button, true);
  const description = $("#new-description").value.trim();
  const result = await createTask({ title: titleInput.value.trim(), description: description || null });
  setBusy(button, false);
  if (result.ok) {
    upsertTask(result.data);
    renderBoard(result.data.id);
    form.reset();
    titleInput.focus();
    toast("Task added");
  } else if (result.status !== 401) {
    toast(errorMessage(result, "Couldn't add the task."), { error: true });
  }
}

// ---------------------------------------------------------------- board events

function wireBoard() {
  const boardEl = $("#board");

  boardEl.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-more")) {
      loadMore(button.closest(".column").dataset.status, button);
      return;
    }
    const action = button.dataset.action;
    if (!action) return;
    const card = button.closest(".card");
    const task = findTask(Number(card.dataset.id));
    if (!task) return;
    const index = STATUSES.indexOf(task.status);
    if (action === "forward") moveTask(task, STATUSES[index + 1], "forward");
    else if (action === "back") moveTask(task, STATUSES[index - 1], "back");
    else if (action === "edit") openEditor(card, task);
    else if (action === "delete") openDeleteConfirm(card, task);
  });

  boardEl.addEventListener("dragstart", (event) => {
    const card = event.target.closest?.(".card");
    if (!card) return;
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.id);
  });
  boardEl.addEventListener("dragend", (event) => {
    event.target.closest?.(".card")?.classList.remove("dragging");
    document.querySelectorAll(".drop-target").forEach((el) => el.classList.remove("drop-target"));
  });
  for (const status of STATUSES) {
    const column = columnEl(status);
    column.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      column.classList.add("drop-target");
    });
    column.addEventListener("dragleave", (event) => {
      if (!column.contains(event.relatedTarget)) column.classList.remove("drop-target");
    });
    column.addEventListener("drop", (event) => {
      event.preventDefault();
      column.classList.remove("drop-target");
      const task = findTask(Number(event.dataTransfer.getData("text/plain")));
      if (task && task.status !== status) moveTask(task, status);
    });
  }
}

// ---------------------------------------------------------------- lab

async function runLab(button, work) {
  const buttons = document.querySelectorAll(".lab-actions button");
  buttons.forEach((b) => setBusy(b, true));
  try {
    await work();
  } finally {
    buttons.forEach((b) => setBusy(b, false));
    button.focus();
  }
}

async function labReplay() {
  const key = uuid();
  const fields = { title: "Idempotency check", description: "Sent twice with one Idempotency-Key." };
  const first = await createTask(fields, {
    key,
    note: (r) => (r.status === 201 ? `First send: created task #${r.data.id}.` : null),
  });
  if (!first.ok) {
    if (first.status !== 401) toast(errorMessage(first, "The first create failed."), { error: true });
    return;
  }
  const second = await createTask(fields, {
    key,
    note: (r) =>
      r.ok
        ? r.data.id === first.data.id
          ? `Second send, same key: the server replayed task #${r.data.id}. No duplicate was created.`
          : `Second send returned a different task (#${r.data.id}).`
        : null,
  });
  upsertTask(first.data);
  renderBoard(first.data.id);
  if (second.ok && second.data.id === first.data.id) toast("Sent the same create twice. One task exists, not two.");
  else if (second.status !== 401) toast(errorMessage(second, "The replay didn't behave as expected."), { error: true });
}

async function labConflict() {
  let task = STATUSES.map((s) => board[s].items[0]).find(Boolean);
  if (!task) {
    const created = await createTask(
      { title: "Conflict check", description: "Used to show a rejected stale write." },
      { note: (r) => (r.ok ? `Created task #${r.data.id} to edit.` : null) }
    );
    if (!created.ok) {
      if (created.status !== 401) toast(errorMessage(created, "Couldn't create a task to edit."), { error: true });
      return;
    }
    task = created.data;
  }
  const read = await authed("GET", `/tasks/${task.id}`, {
    note: (r) => (r.ok ? `Two editors read task #${r.data.id} at version ${r.data.version}.` : null),
  });
  if (!read.ok) {
    if (read.status !== 401) toast(errorMessage(read, "Couldn't read the task."), { error: true });
    return;
  }
  const base = read.data;
  const firstSave = await authed("PUT", `/tasks/${base.id}`, {
    body: putBody(base, {}),
    detail: `editor A, version=${base.version}`,
    note: (r) => (r.ok ? `Editor A saved first. Version ${base.version} became ${r.data.version}.` : null),
  });
  if (!firstSave.ok) {
    if (firstSave.status !== 401) toast(errorMessage(firstSave, "The first save failed."), { error: true });
    return;
  }
  const secondSave = await authed("PUT", `/tasks/${base.id}`, {
    body: putBody(base, {}),
    detail: `editor B, version=${base.version}`,
    note: (r) =>
      r.status === 409
        ? `Editor B still had version ${base.version}, so its save was rejected instead of overwriting A.`
        : null,
  });
  upsertTask(firstSave.data);
  renderBoard(firstSave.data.id);
  if (secondSave.status === 409) toast("The stale save got a 409. Editor A's change was kept.");
  else if (secondSave.status !== 401) toast(errorMessage(secondSave, "The stale save wasn't rejected."), { error: true });
}

// ---------------------------------------------------------------- start

async function restoreSession() {
  const saved = store.get();
  if (!saved?.token || !saved.username) return false;
  session = saved;
  if (saved.expiresAt <= Date.now() && !saved.guestPassword) {
    endSession("Your session expired. Sign in again to continue.");
    return false;
  }
  const me = await authed("GET", "/auth/me", {
    note: (r) => (r.ok ? `Stored token is still valid for ${r.data.username}.` : null),
  });
  if (!me.ok) {
    if (session) endSession(me.status === 0 ? null : "Your session ended. Sign in again to continue.");
    return false;
  }
  await enterWorkspace();
  return true;
}

function init() {
  $("#docs-link").href = `${API}/docs`;
  resetBoard();
  wireTabs();
  wireValidation(document);
  wireBoard();

  $("#start-guest").addEventListener("click", (e) => startGuest(e.currentTarget));
  $("#panel-login").addEventListener("submit", onLogin);
  $("#panel-register").addEventListener("submit", onRegister);
  $("#composer").addEventListener("submit", onCompose);
  $("#sign-out").addEventListener("click", () => {
    endSession();
    toast("Signed out");
    $("#start-guest").focus();
  });
  $("#status-retry").addEventListener("click", () => waitForApi());
  $("#tape-clear").addEventListener("click", clearTape);
  $("#tape-toggle").addEventListener("click", (e) => {
    const open = tape.root.classList.toggle("open");
    e.currentTarget.setAttribute("aria-expanded", String(open));
  });
  $("#lab-replay").addEventListener("click", (e) => runLab(e.currentTarget, labReplay));
  $("#lab-conflict").addEventListener("click", (e) => runLab(e.currentTarget, labConflict));

  // Keep relative times fresh without refetching.
  setInterval(() => {
    for (const card of document.querySelectorAll(".card")) {
      const task = findTask(Number(card.dataset.id));
      const meta = card.querySelector(".card-meta");
      if (task && meta) {
        const updated = relativeTime(task.updated_at);
        meta.textContent = `v${task.version}, updated ${updated || "recently"}`;
      }
    }
  }, 60000);
  setInterval(heartbeat, 60000);

  waitForApi().then((ready) => {
    if (ready) restoreSession();
  });
}

init();

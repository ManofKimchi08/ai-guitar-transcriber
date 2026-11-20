// AI Band Transcriber web page: finds the engine running on this PC (engine.py),
// pairs once with the code shown in its window, then uploads songs, follows the
// job and shows the finished songs. Everything runs on the user's own PC; this page
// only talks to http://127.0.0.1:<port>. API: docs/engine_api.md
"use strict";

const DEFAULT_PORT = 51823;
const STAGE_LABELS = {
  download: "유튜브 음원 받기", separate: "음원 분리 (Demucs)", split: "리드 / 리듬 기타 분리",
  transcribe: "악기별 채보", lyrics: "가사 인식 (Whisper)", score: "악보 · PDF 만들기",
};
const PART_LABELS = { drums: "드럼", bass: "베이스", lead: "리드 기타", rhythm: "리듬 기타", vocals: "보컬 + 가사" };
const STYLE_LABELS = { tab: "TAB", standard: "오선보", both: "TAB + 오선보" };
const VRAM_LABELS = { auto: "자동", "8gb": "8GB 이상", "4gb": "4GB" };
const FOLDER_LABELS = { scores: "악보", midi: "MIDI", stems: "분리된 음원" };

const $ = (id) => document.getElementById(id);
const store = {
  get: (key, fallback) => { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set: (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
  del: (key) => { try { localStorage.removeItem(key); } catch { /* private mode */ } },
};

const state = {
  port: store.get("enginePort", DEFAULT_PORT),
  token: store.get("engineToken", null),
  status: null,
  view: null,
  lost: false,
  source: "file",
  file: null,
  options: Object.assign({ parts: ["drums", "bass", "rhythm", "lead"], style: "tab", vram: "auto", lyrics_language: "" },
                         store.get("options", {})),
  job: null,           // last job snapshot
  logNext: 0,
  stageSeen: {},       // stage key -> time it was first seen running (for durations)
  songs: [],
  selected: null,      // song name
  pdfUrl: null,
};

// ---------------------------------------------------------------- engine calls
class EngineError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function base() { return `http://127.0.0.1:${state.port}`; }

async function api(path, { method = "GET", body, raw = false, timeout = 8000 } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const init = { method, headers, targetAddressSpace: "loopback", cache: "no-store" };
  if (body !== undefined) { init.body = JSON.stringify(body); headers["Content-Type"] = "application/json"; }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  init.signal = ctrl.signal;
  let res;
  try {
    res = await fetch(base() + path, init);
  } catch (e) {
    throw new EngineError("엔진에 접속할 수 없습니다.", 0);
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 401 && path !== "/api/pair") { forgetToken(); }
  if (raw) {
    if (!res.ok) throw new EngineError((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`, res.status);
    return res;
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new EngineError((data && data.error) || `HTTP ${res.status}`, res.status);
  return data;
}

function upload(file, onProgress) {
  // XHR rather than fetch: it reports upload progress
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", base() + "/api/uploads");
    xhr.setRequestHeader("Authorization", `Bearer ${state.token}`);
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status === 201) resolve(data);
      else reject(new EngineError((data && data.error) || `HTTP ${xhr.status}`, xhr.status));
    };
    xhr.onerror = () => reject(new EngineError("업로드 중에 엔진 연결이 끊겼습니다.", 0));
    xhr.send(file);
  });
}

function forgetToken() {
  state.token = null;
  store.del("engineToken");
}

// ---------------------------------------------------------------- views
function show(view) {
  if (state.view === view) return;
  state.view = view;
  for (const v of ["searching", "missing", "pair", "app"]) $(`view-${v}`).hidden = v !== view;
  $("unpair").hidden = view !== "app";
  if (view === "pair") setTimeout(() => $("pair-code").focus(), 0);
}

function setChip(text, kind) {
  const chip = $("engine-chip");
  chip.textContent = text;
  chip.className = `chip chip-${kind}`;
}

function gpuText(status) {
  if (!status.gpu_checked) return "GPU 확인 중";
  return status.gpu ? `GPU: ${status.gpu.name} (${status.gpu.vram_gb}GB)` : "GPU 없음 · CPU로 변환 (느림)";
}

async function probe() {
  let status;
  try {
    status = await api("/api/status", { timeout: 2500 });
    if (status.app !== "ai-band-transcriber") throw new EngineError("다른 프로그램이 응답했습니다.", 0);
  } catch (e) {
    state.status = null;
    if (state.view === "app") {
      state.lost = true;
      $("lost-banner").hidden = false;
      setChip("엔진 연결 끊김", "warn");
    } else {
      show("missing");
      setChip("엔진 없음", "muted");
    }
    return;
  }
  state.status = status;
  $("engine-version").textContent = `엔진 v${status.version}`;
  if (!status.paired) {
    if (state.token) forgetToken();
    show("pair");
    setChip("연결 필요", "warn");
    return;
  }
  setChip(`연결됨 · ${gpuText(status)}`, status.gpu ? "ok" : "warn");
  if (state.view !== "app") {
    show("app");
    buildOptions();
    await Promise.all([loadSongs(), reattach()]);
  } else if (state.lost) {
    state.lost = false;
    $("lost-banner").hidden = true;
    await Promise.all([loadSongs(), reattach()]);
  }
}

let probeTimer = null;
function scheduleProbe() {
  clearTimeout(probeTimer);
  const busy = state.job && state.job.state === "running";
  const delay = state.view === "app" && !state.lost ? (busy ? 15000 : 8000) : 2500;
  probeTimer = setTimeout(async () => { await probe(); scheduleProbe(); }, delay);
}

// ---------------------------------------------------------------- pairing
async function pair(code) {
  $("pair-error").hidden = true;
  try {
    const { token } = await api("/api/pair", { method: "POST", body: { code } });
    state.token = token;
    store.set("engineToken", token);
    await probe();
  } catch (e) {
    $("pair-error").textContent = e.message;
    $("pair-error").hidden = false;
    $("pair-code").select();
  }
}

function readHash() {
  // engine.py opens …/#pair=<code>&port=<port>; the code works once
  const params = new URLSearchParams(location.hash.slice(1));
  const port = parseInt(params.get("port"), 10);
  if (port > 0 && port < 65536) { state.port = port; store.set("enginePort", port); }
  const code = params.get("pair");
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  return code && /^\d{6}$/.test(code) ? code : null;
}

// ---------------------------------------------------------------- options
function saveOptions() { store.set("options", state.options); }

function buildOptions() {
  const chips = $("part-chips");
  chips.replaceChildren();
  for (const key of (state.status && state.status.parts) || Object.keys(PART_LABELS)) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip-toggle";
    b.dataset.part = key;
    b.addEventListener("click", () => {
      const on = new Set(state.options.parts);
      on.has(key) ? on.delete(key) : on.add(key);
      state.options.parts = [...on];
      saveOptions();
      renderOptions();
    });
    chips.append(b);
  }
  buildSegmented("style-seg", STYLE_LABELS, "style");
  buildSegmented("vram-seg", VRAM_LABELS, "vram");
  $("lyrics-lang").value = state.options.lyrics_language || "";
  renderOptions();
}

function buildSegmented(id, labels, option) {
  const box = $(id);
  box.replaceChildren();
  for (const [key, label] of Object.entries(labels)) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "seg";
    b.dataset.value = key;
    b.textContent = label;
    b.addEventListener("click", () => { state.options[option] = key; saveOptions(); renderOptions(); });
    box.append(b);
  }
}

function renderOptions() {
  for (const b of $("part-chips").children) {
    const on = state.options.parts.includes(b.dataset.part);
    b.classList.toggle("on", on);
    b.textContent = (on ? "✓ " : "") + PART_LABELS[b.dataset.part];
  }
  for (const [id, option] of [["style-seg", "style"], ["vram-seg", "vram"]]) {
    for (const b of $(id).children) b.classList.toggle("selected", b.dataset.value === state.options[option]);
  }
  $("lyrics-lang").disabled = !state.options.parts.includes("vocals");
  for (const b of document.querySelectorAll(".source-tabs .seg")) b.classList.toggle("selected", b.dataset.source === state.source);
  $("drop-zone").hidden = state.source !== "file";
  $("url-input").hidden = state.source !== "url";
  renderRunButton();
}

function renderRunButton() {
  const running = state.job && state.job.state === "running";
  const ready = state.source === "file" ? !!state.file : /^https?:\/\//.test($("url-input").value.trim());
  $("run-btn").disabled = running || !ready || state.options.parts.length === 0 || uploading;
  $("cancel-btn").hidden = !running;
}

function chooseFile(file) {
  if (!file) return;
  state.file = file;
  $("drop-text").textContent = `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
  $("drop-zone").classList.add("chosen");
  renderRunButton();
}

// ---------------------------------------------------------------- running a job
let uploading = false;

async function startRun() {
  const body = {
    parts: state.options.parts, style: state.options.style,
    vram: state.options.vram === "auto" ? null : state.options.vram,
    lyrics: true, lyrics_language: state.options.lyrics_language || null,
  };
  $("log").textContent = "";
  try {
    if (state.source === "url") {
      body.url = $("url-input").value.trim();
    } else {
      uploading = true;
      renderRunButton();
      $("run-status").textContent = "음원을 엔진으로 보내는 중…";
      $("upload-bar").hidden = false;
      const bar = $("upload-bar").firstElementChild;
      const up = await upload(state.file, (f) => { bar.style.width = `${Math.round(f * 100)}%`; });
      body.upload_id = up.upload_id;
    }
    const job = await api("/api/jobs", { method: "POST", body });
    beginJob(job);
  } catch (e) {
    $("run-status").textContent = `시작하지 못했습니다: ${e.message}`;
  } finally {
    uploading = false;
    $("upload-bar").hidden = true;
    renderRunButton();
  }
}

function plannedStages(job) {
  const isUrl = /^https?:\/\//.test(job.input);
  return [...(isUrl ? ["download"] : []), "separate", "split", "transcribe",
          ...(job.options.parts.includes("vocals") ? ["lyrics"] : []), "score"];
}

function beginJob(job) {
  state.job = job;
  state.logNext = 0;
  state.stageSeen = {};
  $("log").textContent = "";
  applyJob(job);
  pollJob();
}

async function reattach() {
  // after a reload, or when another tab started a run: follow the engine's current job
  try {
    const job = await api("/api/jobs/current");
    if (job && job.state === "running" && (!state.job || state.job.id !== job.id || state.job.state !== "running")) beginJob(job);
  } catch { /* engine just went away; probe() handles it */ }
}

let pollTimer = null;
async function pollJob() {
  clearTimeout(pollTimer);
  const id = state.job && state.job.id;
  if (!id) return;
  try {
    const job = await api(`/api/jobs/${id}?log_from=${state.logNext}`);
    applyJob(job);
    if (job.state === "running") pollTimer = setTimeout(pollJob, 1000);
    else finishJob(job);
  } catch (e) {
    pollTimer = setTimeout(pollJob, 3000);
  }
}

function applyJob(job) {
  state.job = job;
  if (job.log.length) {
    const log = $("log");
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
    log.textContent += job.log.join("\n") + "\n";
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
  state.logNext = job.log_next;
  const now = Date.now();
  for (const key of job.stages) if (!(key in state.stageSeen)) state.stageSeen[key] = now;
  renderStages(job);
  $("run-status").textContent = job.state === "running" ? job.status
    : job.state === "done" ? "완료했습니다." : job.state === "cancelled" ? "취소했습니다." : `오류: ${job.error}`;
  $("run-elapsed").textContent = formatTime(job.elapsed);
  renderRunButton();
}

function renderStages(job) {
  const list = $("stage-list");
  list.hidden = false;
  list.replaceChildren();
  const keys = plannedStages(job);
  for (const key of job.stages) if (!keys.includes(key)) keys.push(key);
  const current = job.stages[job.stages.length - 1];
  for (const key of keys) {
    const started = job.stages.includes(key);
    let kind = "todo";
    if (started) kind = key !== current || job.state === "done" ? "done" : job.state === "running" ? "now" : "failed";
    const li = document.createElement("li");
    li.className = kind;
    const mark = { todo: "○", now: "●", done: "✓", failed: job.state === "cancelled" ? "■" : "✕" }[kind];
    const next = job.stages[job.stages.indexOf(key) + 1];
    let note = "";
    if (kind === "done" && next && state.stageSeen[next] && state.stageSeen[key]) note = formatTime((state.stageSeen[next] - state.stageSeen[key]) / 1000);
    if (kind === "now") note = "진행 중";
    if (kind === "failed") note = job.state === "cancelled" ? "취소됨" : "실패";
    li.innerHTML = `<span class="mark"></span><span class="name"></span><span class="note"></span>`;
    li.children[0].textContent = mark;
    li.children[1].textContent = STAGE_LABELS[key] || key;
    li.children[2].textContent = note;
    list.append(li);
  }
}

async function finishJob(job) {
  renderRunButton();
  if (job.state === "done") {
    await loadSongs();
    if (job.result && job.result.song) {
      selectSong(job.result.song);
      $("song-card").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } else if (job.state === "error") {
    $("log-details").open = true;
  }
}

async function cancelRun() {
  if (!state.job) return;
  $("cancel-btn").disabled = true;
  try {
    await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" });
    $("run-status").textContent = "취소하는 중… (진행 중인 단계가 멈출 때까지 잠시 걸릴 수 있습니다)";
  } catch (e) {
    $("run-status").textContent = `취소하지 못했습니다: ${e.message}`;
  } finally {
    $("cancel-btn").disabled = false;
  }
}

function formatTime(seconds) {
  if (seconds == null) return "";
  const s = Math.round(seconds);
  return s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${String(s % 60).padStart(2, "0")}초`;
}

// ---------------------------------------------------------------- songs
async function loadSongs() {
  try {
    state.songs = await api("/api/songs");
  } catch { return; }
  renderSongs();
  if (state.selected && state.songs.some((s) => s.name === state.selected)) renderSong();
}

function renderSongs() {
  const q = $("song-search").value.trim().toLowerCase();
  const list = $("song-list");
  list.replaceChildren();
  const shown = state.songs.filter((s) => s.name.toLowerCase().includes(q));
  for (const song of shown) {
    const li = document.createElement("li");
    li.classList.toggle("selected", song.name === state.selected);
    li.tabIndex = 0;
    const name = document.createElement("div");
    name.className = "song-name";
    name.textContent = song.name;
    name.title = song.name;
    const meta = document.createElement("div");
    meta.className = "song-meta";
    const when = document.createElement("span");
    when.className = "muted small";
    when.textContent = new Date(song.mtime * 1000).toLocaleDateString("ko-KR", { month: "short", day: "numeric" });
    meta.append(when, ...song.tags.map(tagEl));
    li.append(name, meta);
    li.addEventListener("click", () => selectSong(song.name));
    li.addEventListener("keydown", (e) => e.key === "Enter" && selectSong(song.name));
    list.append(li);
  }
  $("song-empty").hidden = state.songs.length > 0;
}

function tagEl(text) {
  const t = document.createElement("span");
  t.className = "tag";
  t.textContent = text;
  return t;
}

function selectSong(name) {
  if (state.selected !== name) closePdf();
  state.selected = name;
  renderSongs();
  renderSong();
}

function renderSong() {
  const song = state.songs.find((s) => s.name === state.selected);
  $("song-card").hidden = !song;
  if (!song) return;
  $("song-title").textContent = song.name;
  $("song-tags").replaceChildren(...song.tags.map(tagEl));
  $("open-pdf").disabled = !song.pdf;
  $("open-pdf").textContent = $("pdf-frame").hidden ? "PDF 보기" : "PDF 닫기";
  $("download-score").disabled = !song.score;
  const list = $("file-list");
  list.replaceChildren();
  for (const f of song.files) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = `${FOLDER_LABELS[f.path.split("/")[0]] || ""} · ${f.path.split("/").slice(1).join("/")}`;
    const size = document.createElement("span");
    size.className = "muted small";
    size.textContent = f.size > 1024 * 1024 ? `${(f.size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(f.size / 1024))} KB`;
    const btn = document.createElement("button");
    btn.className = "btn btn-small";
    btn.textContent = "받기";
    btn.addEventListener("click", () => downloadFile(song.name, f.path));
    li.append(name, size, btn);
    list.append(li);
  }
}

async function fetchFile(song, path) {
  const enc = path.split("/").map(encodeURIComponent).join("/");
  const res = await api(`/api/songs/${encodeURIComponent(song)}/files/${enc}`, { raw: true, timeout: 120000 });
  return res.blob();
}

async function downloadFile(song, path) {
  try {
    const blob = await fetchFile(song, path);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = path.split("/").pop();
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (e) {
    alert(`파일을 받지 못했습니다: ${e.message}`);
  }
}

function closePdf() {
  $("pdf-frame").hidden = true;
  $("pdf-frame").removeAttribute("src");
  if (state.pdfUrl) URL.revokeObjectURL(state.pdfUrl);
  state.pdfUrl = null;
}

async function togglePdf() {
  const song = state.songs.find((s) => s.name === state.selected);
  if (!song || !song.pdf) return;
  if (!$("pdf-frame").hidden) { closePdf(); renderSong(); return; }
  try {
    const blob = await fetchFile(song.name, song.pdf);
    closePdf();
    state.pdfUrl = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
    $("pdf-frame").src = state.pdfUrl;
    $("pdf-frame").hidden = false;
    renderSong();
  } catch (e) {
    alert(`PDF를 열지 못했습니다: ${e.message}`);
  }
}

// ---------------------------------------------------------------- wiring
function wire() {
  $("pair-form").addEventListener("submit", (e) => { e.preventDefault(); pair($("pair-code").value.trim()); });
  $("pair-code").addEventListener("input", (e) => {
    e.target.value = e.target.value.replace(/\D/g, "").slice(0, 6);
    if (e.target.value.length === 6) pair(e.target.value);
  });
  $("retry-search").addEventListener("click", () => { setChip("엔진 찾는 중…", "muted"); probe(); });
  $("unpair").addEventListener("click", () => {
    forgetToken();
    location.reload();
  });
  for (const b of document.querySelectorAll(".source-tabs .seg")) {
    b.addEventListener("click", () => { state.source = b.dataset.source; renderOptions(); });
  }
  const zone = $("drop-zone");
  $("file-input").addEventListener("change", (e) => chooseFile(e.target.files[0]));
  zone.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && $("file-input").click());
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("over");
    chooseFile(e.dataTransfer.files[0]);
  });
  $("url-input").addEventListener("input", renderRunButton);
  $("lyrics-lang").addEventListener("change", (e) => { state.options.lyrics_language = e.target.value; saveOptions(); });
  $("run-btn").addEventListener("click", startRun);
  $("cancel-btn").addEventListener("click", cancelRun);
  $("new-convert").addEventListener("click", () => {
    $("convert-card").scrollIntoView({ behavior: "smooth" });
    (state.source === "file" ? $("drop-zone") : $("url-input")).focus();
  });
  $("song-search").addEventListener("input", renderSongs);
  $("open-pdf").addEventListener("click", togglePdf);
  $("download-score").addEventListener("click", () => {
    const song = state.songs.find((s) => s.name === state.selected);
    if (song && song.score) downloadFile(song.name, song.score);
  });
  // Safari refuses http://127.0.0.1 from an https page
  const ua = navigator.userAgent;
  $("browser-note").hidden = !(/Safari\//.test(ua) && !/Chrome\/|Chromium\/|Edg\//.test(ua));
}

async function main() {
  wire();
  const code = readHash();
  show("searching");
  await probe();
  if (code && state.view === "pair") await pair(code);
  scheduleProbe();
  // the engine opened this page in a tab that already shows it: only the address changed
  window.addEventListener("hashchange", async () => {
    const again = readHash();
    if (!again) return;
    await probe();
    if (state.view === "pair") await pair(again);
  });
}

main();

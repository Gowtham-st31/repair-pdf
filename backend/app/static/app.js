const tabs = [
  { id: "find", label: "Find & Replace" },
  { id: "merge", label: "Merge PDFs" },
  { id: "reorder", label: "Change Pages" },
  { id: "remove", label: "Remove Pages" },
];

const TRANSPARENT_PIXEL =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";

let _previewImgObserver = null;
function ensurePreviewObserver() {
  if (_previewImgObserver) return _previewImgObserver;
  if (typeof IntersectionObserver === "undefined") return null;

  _previewImgObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const img = entry.target;
        const src = img?.dataset?.src;
        if (src) img.src = src;
        _previewImgObserver.unobserve(img);
      }
    },
    { root: null, rootMargin: "800px 0px", threshold: 0.01 }
  );

  return _previewImgObserver;
}

function lazySetImgSrc(img, src) {
  if (!img) return;
  const io = ensurePreviewObserver();
  if (!io) {
    img.src = src;
    return;
  }
  img.src = TRANSPARENT_PIXEL;
  img.dataset.src = src;
  io.observe(img);
}

function getLightboxEls() {
  return {
    root: document.getElementById("page-lightbox"),
    img: document.getElementById("page-lightbox-img"),
    title: document.getElementById("page-lightbox-title"),
    close: document.getElementById("page-lightbox-close"),
  };
}

function openPageLightbox({ sessionId, pageNum, title }) {
  const { root, img, title: titleEl } = getLightboxEls();
  if (!root || !img || !sessionId || !Number.isFinite(pageNum)) return;

  const pageTitle = title || `Page ${pageNum}`;
  titleEl.textContent = pageTitle;
  img.alt = pageTitle;
  img.src = previewImageUrl(sessionId, pageNum, 2.0);

  root.classList.remove("hidden");
  root.classList.add("flex");
  document.body.classList.add("overflow-hidden");
}

function closePageLightbox() {
  const { root, img } = getLightboxEls();
  if (!root || !img) return;
  root.classList.add("hidden");
  root.classList.remove("flex");
  document.body.classList.remove("overflow-hidden");
  img.removeAttribute("src");
  img.alt = "";
}

function wireLightbox() {
  const { root, close } = getLightboxEls();
  if (!root || !close) return;

  close.addEventListener("click", closePageLightbox);

  root.addEventListener("click", (e) => {
    // Click outside the content closes.
    if (e.target === root) closePageLightbox();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !root.classList.contains("hidden")) {
      closePageLightbox();
    }
  });
}

function clearPreview(container, message) {
  container.classList.remove("space-y-2");
  container.innerHTML = "";
  const msg = document.createElement("div");
  msg.className = "p-3 text-sm text-slate-500";
  msg.textContent = message;
  container.appendChild(msg);
}

function previewImageUrl(sessionId, pageNum, scale) {
  const s = typeof scale === "number" ? scale : 1.2;
  return `/api/preview-page/${encodeURIComponent(sessionId)}/${pageNum}?scale=${encodeURIComponent(String(s))}`;
}

async function renderPdfPreview(file, container) {
  const token =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : String(Date.now()) + String(Math.random());

  container.dataset.previewToken = token;
  delete container.dataset.previewSession;

  if (!file) {
    clearPreview(container, "No PDF selected.");
    return 0;
  }

  try {
    // Reliable preview for all environments (including VS Code Simple Browser):
    // upload once, then lazy-load server-rendered page images.
    clearPreview(container, "Loading preview…");

    const fd = new FormData();
    fd.append("pdf", file);
    const resp = await fetch("/api/preview-session", { method: "POST", body: fd });
    if (!resp.ok) {
      let msg = `Request failed (${resp.status})`;
      try {
        const data = await resp.json();
        if (data?.detail) msg = data.detail;
      } catch {
        // ignore
      }
      throw new Error(msg);
    }

    const data = await resp.json();
    if (container.dataset.previewToken !== token) return 0;

    const sessionId = data?.sessionId;
    const pageCount = Number(data?.pageCount);
    if (!sessionId || !Number.isFinite(pageCount) || pageCount <= 0) {
      throw new Error("Preview init failed");
    }

    container.dataset.previewSession = sessionId;
    container.innerHTML = "";
    container.classList.add("space-y-2");

    for (let pageNum = 1; pageNum <= pageCount; pageNum++) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = `Page ${pageNum}`;
      img.className = "w-full cursor-zoom-in rounded-xl border border-slate-200 bg-white";
      img.dataset.pageNum = String(pageNum);
      img.dataset.scale = "1.2";
      lazySetImgSrc(img, previewImageUrl(sessionId, pageNum, 1.2));
      img.addEventListener("click", () => openPageLightbox({ sessionId, pageNum }));
      container.appendChild(img);
    }

    return pageCount;
  } catch (e) {
    if (container.dataset.previewToken === token) {
      clearPreview(container, `Preview failed: ${e?.message || String(e)}`);
    }
    return 0;
  }
}

async function fetchPageCount(file) {
  const fd = new FormData();
  fd.append("pdf", file);
  const resp = await fetch("/api/page-count", { method: "POST", body: fd });
  if (!resp.ok) {
    let msg = `Request failed (${resp.status})`;
    try {
      const data = await resp.json();
      if (data?.detail) msg = data.detail;
    } catch {
      // ignore
    }
    throw new Error(msg);
  }
  const data = await resp.json();
  const count = Number(data?.pageCount);
  if (!Number.isFinite(count) || count <= 0) throw new Error("Invalid page count");
  return count;
}

function setHiddenValue(input, value) {
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function buildRemovePagesUI(container, hiddenInput, pageCount, sessionId) {
  container.innerHTML = "";

  for (let i = 1; i <= pageCount; i++) {
    const label = document.createElement("label");
    label.className = "relative overflow-hidden rounded-xl border border-slate-200 bg-white transition hover:bg-slate-50";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = String(i);

    cb.className = "absolute left-3 top-3 h-4 w-4";

    const wrap = document.createElement("div");
    wrap.className = "p-3 pl-10";

    const thumb = document.createElement("img");
    thumb.loading = "lazy";
    thumb.alt = `Page ${i}`;
    thumb.className = "w-full rounded-lg border border-slate-200 bg-white";
    if (sessionId) {
      lazySetImgSrc(thumb, previewImageUrl(sessionId, i, 0.6));
    }

    const text = document.createElement("span");
    text.className = "mt-2 block text-sm font-medium text-slate-900";
    text.textContent = `Page ${i}`;

    const actions = document.createElement("div");
    actions.className = "mt-2 flex items-center gap-2";

    if (sessionId) {
      const maxBtn = document.createElement("button");
      maxBtn.type = "button";
      maxBtn.className = "text-xs font-medium text-slate-700 underline decoration-slate-300 underline-offset-2 hover:text-slate-900";
      maxBtn.textContent = "Maximize";
      maxBtn.addEventListener("click", (ev) => {
        // Prevent toggling the checkbox when maximizing.
        ev.preventDefault();
        ev.stopPropagation();
        openPageLightbox({ sessionId, pageNum: i });
      });
      actions.appendChild(maxBtn);
    }

    wrap.appendChild(thumb);
    wrap.appendChild(text);
    wrap.appendChild(actions);
    label.appendChild(cb);
    label.appendChild(wrap);
    container.appendChild(label);
  }

  function updateHidden() {
    const selected = Array.from(container.querySelectorAll("input[type='checkbox']:checked")).map((el) => Number(el.value));
    selected.sort((a, b) => a - b);
    // backend parser accepts comma-separated pages/ranges; we send a simple comma list
    setHiddenValue(hiddenInput, selected.join(","));
  }

  container.addEventListener("change", updateHidden);
  updateHidden();
  hiddenInput.required = true;
}

function buildReorderUI(container, hiddenInput, pageCount, sessionId) {
  container.innerHTML = "";

  const items = [];
  for (let i = 1; i <= pageCount; i++) {
    const item = document.createElement("div");
    item.className = "flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3 text-sm transition hover:bg-slate-50";
    item.draggable = true;
    item.dataset.page = String(i);

    const thumb = document.createElement("img");
    thumb.loading = "lazy";
    thumb.alt = `Page ${i}`;
    thumb.className = "h-20 w-16 flex-none rounded-md border border-slate-200 bg-white object-cover";
    if (sessionId) {
      lazySetImgSrc(thumb, previewImageUrl(sessionId, i, 0.6));
    }

    const left = document.createElement("div");
    left.className = "min-w-0 flex-1";

    const title = document.createElement("div");
    title.className = "font-medium";
    title.textContent = `Page ${i}`;

    const sub = document.createElement("div");
    sub.className = "mt-0.5 text-xs text-slate-500";
    sub.textContent = "Drag";

    const actions = document.createElement("div");
    actions.className = "mt-2 flex items-center gap-2";

    if (sessionId) {
      const maxBtn = document.createElement("button");
      maxBtn.type = "button";
      maxBtn.className = "text-xs font-medium text-slate-700 underline decoration-slate-300 underline-offset-2 hover:text-slate-900";
      maxBtn.textContent = "Maximize";
      maxBtn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        openPageLightbox({ sessionId, pageNum: i });
      });
      actions.appendChild(maxBtn);
    }

    left.appendChild(title);
    left.appendChild(sub);
    left.appendChild(actions);

    item.appendChild(thumb);
    item.appendChild(left);
    container.appendChild(item);
    items.push(item);
  }

  let dragged = null;

  function updateHidden() {
    const order = Array.from(container.querySelectorAll("[data-page]")).map((el) => el.dataset.page);
    setHiddenValue(hiddenInput, order.join(","));
  }

  container.addEventListener("dragstart", (e) => {
    const target = e.target.closest("[data-page]");
    if (!target) return;
    dragged = target;
    e.dataTransfer.effectAllowed = "move";
    try {
      e.dataTransfer.setData("text/plain", target.dataset.page);
    } catch {
      // ignore
    }
    target.classList.add("ring-2", "ring-slate-900");
  });

  container.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("ring-2", "ring-slate-900");
    dragged = null;
  });

  container.addEventListener("dragover", (e) => {
    if (!dragged) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const over = e.target.closest("[data-page]");
    if (!over || over === dragged) return;

    const rect = over.getBoundingClientRect();
    const before = e.clientY < rect.top + rect.height / 2;
    container.insertBefore(dragged, before ? over : over.nextSibling);
  });

  container.addEventListener("drop", (e) => {
    if (!dragged) return;
    e.preventDefault();
    updateHidden();
  });

  updateHidden();
  hiddenInput.required = true;
}

function $(sel) {
  return document.querySelector(sel);
}

function getPreviewForForm(form) {
  const id = form?.dataset?.previewId;
  if (!id) return null;
  return document.getElementById(id);
}

function setActiveTab(id) {
  for (const t of tabs) {
    const btn = $(`#tab-${t.id}`);
    const panel = $(`#panel-${t.id}`);
    const active = t.id === id;
    btn.classList.toggle("text-slate-900", active);
    btn.classList.toggle("text-slate-500", !active);
    btn.classList.toggle("border-slate-900", active);
    btn.classList.toggle("border-transparent", !active);
    panel.classList.toggle("hidden", !active);
    if (active) {
      panel.classList.remove("panel-in");
      void panel.offsetWidth;
      panel.classList.add("panel-in");
    }
  }
}

function setBusy(form, busy) {
  const btn = form.querySelector("[data-apply]");
  const dl = form.querySelector("[data-download]");
  const hint = form.querySelector("[data-hint]");
  if (btn) {
    btn.disabled = busy;
    btn.setAttribute("aria-busy", busy ? "true" : "false");
    btn.classList.toggle("opacity-70", busy);
    btn.classList.toggle("cursor-not-allowed", busy);
    btn.classList.toggle("shimmer", busy);
    btn.classList.toggle("bg-gradient-to-r", busy);
    btn.classList.toggle("from-slate-900", busy);
    btn.classList.toggle("via-slate-700", busy);
    btn.classList.toggle("to-slate-900", busy);
    btn.classList.toggle("bg-slate-900", !busy);

    const spinnerAttr = "data-busy-spinner";
    const existing = btn.querySelector(`[${spinnerAttr}]`);
    if (busy && !existing) {
      const sp = document.createElement("span");
      sp.setAttribute(spinnerAttr, "1");
      sp.className =
        "ml-2 inline-block h-4 w-4 rounded-full border-2 border-white/70 border-t-transparent align-[-2px] animate-spin";
      btn.appendChild(sp);
    }
    if (!busy && existing) existing.remove();
  }
  if (dl) dl.disabled = busy || !form.dataset.resultUrl;
  if (hint) {
    hint.classList.toggle("animate-pulse", busy);
    if (busy) hint.textContent = "Working…";
    else if (hint.textContent === "Working…") hint.textContent = "";
  }
}

function downloadUrl(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function sanitizePdfFilename(name, fallback) {
  const fb = (fallback || "output.pdf").trim() || "output.pdf";
  let n = String(name || "").trim();
  if (!n) n = fb;
  // Remove path separators and control chars.
  n = n.replace(/[\\/\x00-\x1F\x7F]/g, "-");
  // Collapse whitespace.
  n = n.replace(/\s+/g, " ").trim();
  // Limit length.
  if (n.length > 120) n = n.slice(0, 120).trim();
  if (!n.toLowerCase().endsWith(".pdf")) n += ".pdf";
  return n;
}

function clearResult(form) {
  const dl = form.querySelector("[data-download]");
  const oldUrl = form.dataset.resultUrl;
  if (oldUrl) URL.revokeObjectURL(oldUrl);
  delete form.dataset.resultUrl;
  delete form.dataset.resultFilename;
  if (dl) dl.disabled = true;
}

function setResult(form, blob, filename) {
  clearResult(form);
  const url = URL.createObjectURL(blob);
  form.dataset.resultUrl = url;
  form.dataset.resultFilename = filename;
  const dl = form.querySelector("[data-download]");
  if (dl) dl.disabled = false;
  const nameInput = form.querySelector("[data-filename]");
  if (nameInput && !nameInput.value) nameInput.value = filename;
}

function formatApiErrorPayload(data, status) {
  // FastAPI errors often look like: { detail: [ { loc, msg, type }, ... ] }
  const detail = data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;

  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => {
        if (!d) return null;
        if (typeof d === "string") return d;
        if (typeof d?.msg === "string") return d.msg;
        return null;
      })
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }

  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      // ignore
    }
  }

  if (data && typeof data === "object") {
    try {
      return JSON.stringify(data);
    } catch {
      // ignore
    }
  }

  return `Request failed (${status})`;
}

async function applyForm(form, endpoint, filename) {
  const errorEl = form.querySelector("[data-error]");
  const hint = form.querySelector("[data-hint]");
  errorEl.textContent = "";
  clearResult(form);
  if (hint) hint.textContent = "";

  setBusy(form, true);
  try {
    const nameInput = form.querySelector("[data-filename]");
    const desiredName = sanitizePdfFilename(nameInput?.value, filename);
    const fd = new FormData(form);
    const resp = await fetch(endpoint, { method: "POST", body: fd });
    if (!resp.ok) {
      let msg = `Request failed (${resp.status})`;
      try {
        const data = await resp.json();
        msg = formatApiErrorPayload(data, resp.status);
      } catch {
        // ignore
      }
      throw new Error(msg);
    }
    const blob = await resp.blob();
    setResult(form, blob, desiredName);

    // Optional: show replacement count (Find/Replace endpoint only).
    const repHeader = resp.headers.get("X-Replacements");
    if (hint && repHeader && endpoint === "/api/find-replace") {
      const n = Number(repHeader);
      if (Number.isFinite(n)) {
        let msg = `Replaced ${n} occurrence${n === 1 ? "" : "s"}.`;
        const det = resp.headers.get("X-Detected-Font");
        const used = resp.headers.get("X-Used-Font");
        const src = resp.headers.get("X-Used-Source");
        if ((det && det.trim()) || (used && used.trim())) {
          const parts = [];
          if (src && src.trim()) parts.push(String(src).trim());
          if (det && det.trim()) parts.push(`detected: ${String(det).trim()}`);
          if (used && used.trim()) parts.push(`used: ${String(used).trim()}`);
          msg += ` Font(${parts.join("; ")}).`;
        }
        hint.textContent = msg;
      }
    }

    // Update preview to show the applied output.
    const preview = getPreviewForForm(form);
    if (preview) {
      try {
        const outFile = new File([blob], desiredName, { type: "application/pdf" });
        const pageCount = await renderPdfPreview(outFile, preview);
        const sid = preview.dataset.previewSession;

        // If this tool has a page list UI, rebuild it from the applied PDF.
        if (form.id === "form-remove" && pageCount) {
          const list = document.getElementById("remove-pages");
          const pagesInput = document.getElementById("pages-input");
          if (list && pagesInput) buildRemovePagesUI(list, pagesInput, pageCount, sid);
        }
        if (form.id === "form-reorder" && pageCount) {
          const list = document.getElementById("reorder-list");
          const orderInput = document.getElementById("order-input");
          if (list && orderInput) buildReorderUI(list, orderInput, pageCount, sid);
        }
      } catch (e) {
        // Non-fatal: processing succeeded, but preview failed.
        errorEl.textContent = `Applied, but preview failed: ${e?.message || String(e)}`;
      }
    }
  } catch (e) {
    errorEl.textContent = e?.message || String(e);
  } finally {
    setBusy(form, false);
  }
}

function wireApplyDownload(form, endpoint, filename) {
  const applyBtn = form.querySelector("[data-apply]");
  const downloadBtn = form.querySelector("[data-download]");
  const errorEl = form.querySelector("[data-error]");
  const hint = form.querySelector("[data-hint]");

  // Prevent Enter from submitting the form (we use explicit buttons)
  form.addEventListener("submit", (ev) => ev.preventDefault());

  if (!applyBtn || !downloadBtn || !errorEl) return;

  applyBtn.addEventListener("click", () => applyForm(form, endpoint, filename));
  downloadBtn.addEventListener("click", () => {
    errorEl.textContent = "";
    const url = form.dataset.resultUrl;
    const nameInput = form.querySelector("[data-filename]");
    const rawName = nameInput?.value || form.dataset.resultFilename || filename;
    const name = sanitizePdfFilename(rawName, filename);
    if (!url) {
      errorEl.textContent = "Click Apply first.";
      return;
    }
    downloadUrl(url, name);
    if (hint && hint.textContent === "Working…") hint.textContent = "";
  });
}

function wireFindReplace() {
  const form = $("#form-find");
  const scopeAll = $("#scope-all");
  const scopeRange = $("#scope-range");
  const rangeWrap = $("#range-wrap");

  const fromInput = form.querySelector("input[name='fromPage']");
  const toInput = form.querySelector("input[name='toPage']");

  const pdfInput = $("#pdf-find");
  const preview = $("#preview-find");
  const pickToggle = $("#pick-find-text");

  const fontChoice = form.querySelector("select[name='fontChoice']");
  const fontModeAuto = form.querySelector("input[name='fontMode'][value='auto']");
  const fontModeManual = form.querySelector("input[name='fontMode'][value='manual']");

  const findInput = form.querySelector("input[name='findText']");
  const replaceInput = form.querySelector("input[name='replaceText']");
  const extraFontsInput = form.querySelector("input[name='extraFonts']");

  // Inline editor that appears when user clicks a word in preview.
  let inlineEditor = null;
  function closeInlineEditor() {
    if (!inlineEditor) return;
    inlineEditor.remove();
    inlineEditor = null;
  }

  function openInlineEditorAt(clientX, clientY, pickedText) {
    closeInlineEditor();

    inlineEditor = document.createElement("div");
    inlineEditor.className =
      "fixed z-50 w-[20rem] max-w-[90vw] rounded-xl border border-slate-200 bg-white p-3 shadow-sm";
    inlineEditor.style.left = `${Math.max(8, Math.min(window.innerWidth - 340, clientX + 8))}px`;
    inlineEditor.style.top = `${Math.max(8, Math.min(window.innerHeight - 120, clientY + 8))}px`;

    const title = document.createElement("div");
    title.className = "text-xs font-medium text-slate-700";
    title.textContent = pickedText ? `Edit: ${pickedText}` : "Edit";

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Type replacement, press Enter";
    input.className = "mt-2 block w-full rounded-xl border border-slate-200 px-3 py-2 text-sm";

    const help = document.createElement("div");
    help.className = "mt-2 text-[11px] text-slate-500";
    help.textContent = "Press Enter to set. Then click Apply.";

    inlineEditor.appendChild(title);
    inlineEditor.appendChild(input);
    inlineEditor.appendChild(help);
    document.body.appendChild(inlineEditor);

    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeInlineEditor();
        return;
      }
      if (e.key === "Enter") {
        const val = String(input.value || "").trim();
        if (replaceInput) {
          replaceInput.value = val;
          replaceInput.dispatchEvent(new Event("input", { bubbles: true }));
          try {
            const applyBtn = form.querySelector("[data-apply]");
            if (applyBtn) applyBtn.focus();
          } catch {
            // ignore
          }
        }
        closeInlineEditor();
      }
    });

    // Close when clicking outside.
    const onDoc = (e) => {
      if (!inlineEditor) return;
      if (inlineEditor.contains(e.target)) return;
      closeInlineEditor();
      document.removeEventListener("mousedown", onDoc, true);
    };
    document.addEventListener("mousedown", onDoc, true);

    try {
      input.focus();
      input.select();
    } catch {
      // ignore
    }
  }

  async function pickWordFromPreview(ev) {
    if (!pickToggle || !pickToggle.checked) return;

    const img = ev.target;
    if (!img || img.tagName !== "IMG") return;
    const sessionId = preview?.dataset?.previewSession;
    const pageNum = Number(img.dataset.pageNum);
    const scale = Number(img.dataset.scale || "1.2");
    if (!sessionId || !Number.isFinite(pageNum) || pageNum <= 0) return;

    // Map click point from rendered CSS pixels -> image pixels.
    const r = img.getBoundingClientRect();
    const nx = (ev.clientX - r.left) / Math.max(1, r.width);
    const ny = (ev.clientY - r.top) / Math.max(1, r.height);
    const xImg = nx * (img.naturalWidth || r.width);
    const yImg = ny * (img.naturalHeight || r.height);

    try {
      const resp = await fetch("/api/preview-pick-word", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId, pageNumber: pageNum, x: xImg, y: yImg, scale }),
      });
      if (!resp.ok) return;
      const data = await resp.json();
      const picked = String(data?.text || "").trim();
      if (!picked) return;

      if (findInput) {
        findInput.value = picked;
        findInput.dispatchEvent(new Event("input", { bubbles: true }));
      }

      // Scope to the clicked page to make this feel like a direct edit.
      if (scopeRange && fromInput && toInput) {
        scopeRange.checked = true;
        scopeRange.dispatchEvent(new Event("change", { bubbles: true }));
        fromInput.value = String(pageNum);
        toInput.value = String(pageNum);
        fromInput.dispatchEvent(new Event("input", { bubbles: true }));
        toInput.dispatchEvent(new Event("input", { bubbles: true }));
      }

      // Direct edit UI: let user type replacement right from preview.
      openInlineEditorAt(ev.clientX, ev.clientY, picked);

      const hint = form.querySelector("[data-hint]");
      if (hint) hint.textContent = `Picked: ${picked}`;
    } catch {
      // ignore
    }
  }

  function updateFontMode() {
    const manual = Boolean(fontModeManual && fontModeManual.checked);
    if (fontChoice) {
      // Disabled fields are not submitted -> backend sees fontChoice=None (true Auto).
      fontChoice.disabled = !manual;
    }
  }

  if (fontModeAuto) fontModeAuto.addEventListener("change", updateFontMode);
  if (fontModeManual) fontModeManual.addEventListener("change", updateFontMode);
  updateFontMode();

  // No live apply: user explicitly clicks Apply.
  if (fontChoice) fontChoice.addEventListener("change", () => clearResult(form));
  if (findInput) findInput.addEventListener("input", () => clearResult(form));
  if (replaceInput) replaceInput.addEventListener("input", () => clearResult(form));
  if (extraFontsInput) extraFontsInput.addEventListener("change", () => clearResult(form));

  function updateScope() {
    const isRange = scopeRange.checked;
    rangeWrap.classList.toggle("hidden", !isRange);

    // Important: disabled controls are NOT submitted.
    // This avoids FastAPI 422 errors when the inputs are present but empty.
    if (fromInput) {
      fromInput.disabled = !isRange;
      fromInput.required = isRange;
      if (!isRange) fromInput.value = "";
    }
    if (toInput) {
      toInput.disabled = !isRange;
      toInput.required = isRange;
      if (!isRange) toInput.value = "";
    }
  }

  scopeAll.addEventListener("change", updateScope);
  scopeRange.addEventListener("change", updateScope);
  updateScope();

  clearPreview(preview, "Upload a PDF to preview.");
  pdfInput.addEventListener("change", () => {
    const file = pdfInput.files?.[0];
    renderPdfPreview(file, preview).catch((e) => {
      const errorEl = form.querySelector("[data-error]");
      errorEl.textContent = e?.message || String(e);
    });
    clearResult(form);
    closeInlineEditor();
  });

  // Capture clicks before the image's lightbox handler.
  if (preview) {
    preview.addEventListener(
      "click",
      (ev) => {
        if (!pickToggle || !pickToggle.checked) return;
        ev.preventDefault();
        ev.stopPropagation();
        pickWordFromPreview(ev);
      },
      true
    );
  }

  wireApplyDownload(form, "/api/find-replace", "find-replace.pdf");
}

function wireMerge() {
  const form = $("#form-merge");
  const pdfInput = $("#pdf-merge");
  const preview = $("#preview-merge");

  clearPreview(preview, "Select one or more PDFs to preview the first.");
  pdfInput.addEventListener("change", () => {
    const file = pdfInput.files?.[0];
    renderPdfPreview(file, preview).catch((e) => {
      const errorEl = form.querySelector("[data-error]");
      errorEl.textContent = e?.message || String(e);
    });
    clearResult(form);
  });

  wireApplyDownload(form, "/api/merge", "merged.pdf");
}

function wireReorder() {
  const form = $("#form-reorder");
  const pdfInput = $("#pdf-reorder");
  const preview = $("#preview-reorder");
  const list = $("#reorder-list");
  const orderInput = $("#order-input");

  clearPreview(preview, "Upload a PDF to preview.");
  pdfInput.addEventListener("change", () => {
    const file = pdfInput.files?.[0];
    renderPdfPreview(file, preview)
      .then((count) => {
        if (!count) return;
        const sid = preview.dataset.previewSession;
        buildReorderUI(list, orderInput, count, sid);
      })
      .catch((e) => {
        const errorEl = form.querySelector("[data-error]");
        errorEl.textContent = e?.message || String(e);
      });
    clearResult(form);
    list.innerHTML = "";
    setHiddenValue(orderInput, "");
    if (!file) return;
  });

  wireApplyDownload(form, "/api/reorder", "reordered.pdf");
}

function wireRemove() {
  const form = $("#form-remove");
  const pdfInput = $("#pdf-remove");
  const preview = $("#preview-remove");
  const list = $("#remove-pages");
  const pagesInput = $("#pages-input");

  clearPreview(preview, "Upload a PDF to preview.");
  pdfInput.addEventListener("change", () => {
    const file = pdfInput.files?.[0];
    renderPdfPreview(file, preview)
      .then((count) => {
        if (!count) return;
        const sid = preview.dataset.previewSession;
        buildRemovePagesUI(list, pagesInput, count, sid);
      })
      .catch((e) => {
        const errorEl = form.querySelector("[data-error]");
        errorEl.textContent = e?.message || String(e);
      });
    clearResult(form);
    list.innerHTML = "";
    setHiddenValue(pagesInput, "");
    if (!file) return;
  });

  wireApplyDownload(form, "/api/remove-pages", "pages-removed.pdf");
}

for (const t of tabs) {
  $(`#tab-${t.id}`).addEventListener("click", () => setActiveTab(t.id));
}

wireFindReplace();
wireMerge();
wireReorder();
wireRemove();

wireLightbox();

setActiveTab("find");

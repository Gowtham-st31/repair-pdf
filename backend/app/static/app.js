/* ═══════════════════════════════════════════════════
   RepairPDF — Luxury UI Controller (Vanilla JS)
   ═══════════════════════════════════════════════════ */

/* ── Global State ── */
let currentTool = 'find-replace';
let currentFile = null;          // File object for the primary PDF
let previewSessionId = null;
let previewPageCount = 0;
let previewScale = 1.1;
let previewThumbScale = 0.85;
let lightboxScale = 1.5;
let lastPickedText = '';
let lastResultBlob = null;       // Blob from last Apply
let lastResultName = '';          // filename
let selectedPages = new Set();    // for remove-pages
let reorderList = [];             // ordered page nums for reorder
let mergeFiles = [];              // File[] for merge
let isBusy = false;

/* ── DOM refs ── */
const $stateUpload    = document.getElementById('state-upload');
const $stateUploading = document.getElementById('state-uploading');
const $stateWorkspace = document.getElementById('state-workspace');
const $heroFile       = document.getElementById('hero-file-input');
const $progressBar    = document.getElementById('upload-progress-bar');
const $navStatus      = document.getElementById('nav-status');
const $navFilename    = document.getElementById('nav-filename');
const $filenameInput  = document.getElementById('filename-input');
const $pageCountBadge = document.getElementById('page-count-badge');
const $toolOptions    = document.getElementById('tool-options');
const $canvasTitle    = document.getElementById('canvas-title');
const $canvasSubtitle = document.getElementById('canvas-subtitle');
const $canvasActions  = document.getElementById('canvas-actions');
const $canvasBody     = document.getElementById('canvas-body');
const $btnApply       = document.getElementById('btn-apply');
const $btnDownload    = document.getElementById('btn-download');
const $btnReset       = document.getElementById('btn-reset');
const $btnApplyM      = document.getElementById('btn-apply-m');
const $btnDownloadM   = document.getElementById('btn-download-m');
const $btnResetM      = document.getElementById('btn-reset-m');
const $lightbox       = document.getElementById('lightbox');
const $lightboxImg    = document.getElementById('lightbox-img');
const $lightboxClose  = document.getElementById('lightbox-close');
const $lightboxPrev   = document.getElementById('lightbox-prev');
const $lightboxNext   = document.getElementById('lightbox-next');
const $lightboxPage   = document.getElementById('lightbox-page');
const $toast          = document.getElementById('toast');
const $toastTitle     = document.getElementById('toast-title');
const $toastMsg       = document.getElementById('toast-msg');
const $confirmModal   = document.getElementById('confirm-modal');
const $modalTitle     = document.getElementById('modal-title');
const $modalBody      = document.getElementById('modal-body');

let _modalCallback = null;
let lightboxPageNumber = null;
let lightboxRenderScale = null;

function isMobile() {
  return window.matchMedia && window.matchMedia('(max-width: 1024px)').matches;
}

function calibrateScales() {
  if (isMobile()) {
    previewScale = 1.0;
    previewThumbScale = 0.6;
    lightboxScale = 1.2;
  } else {
    previewScale = 1.1;
    previewThumbScale = 0.75;
    lightboxScale = 1.4;
  }
}

calibrateScales();
window.addEventListener('resize', () => {
  const before = previewThumbScale;
  calibrateScales();
  if (previewSessionId && before !== previewThumbScale) {
    renderToolCanvas(currentTool);
  }
});

function getPageGridEl() {
  return document.getElementById('page-grid');
}

/* ════════════════════════════════════════
   UTILITY HELPERS
   ════════════════════════════════════════ */

function showToast(title, msg, duration = 3500) {
  $toastTitle.textContent = title;
  $toastMsg.textContent = msg;
  $toast.classList.remove('hidden');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => $toast.classList.add('hidden'), duration);
}

function sanitizePdfFilename(name, fallback) {
  const fb = String(fallback || 'result.pdf').trim() || 'result.pdf';
  let n = String(name || '').trim();
  if (!n) n = fb;
  // Remove path separators and control chars.
  n = n.replace(/[\\/\x00-\x1F\x7F]/g, '-');
  // Collapse whitespace.
  n = n.replace(/\s+/g, ' ').trim();
  // Limit length.
  if (n.length > 120) n = n.slice(0, 120).trim();
  if (!n.toLowerCase().endsWith('.pdf')) n += '.pdf';
  return n;
}

function getDesiredDownloadName() {
  // Prefer what the user typed. Fall back to server header / default.
  const typed = ($filenameInput?.value || '').trim();
  return sanitizePdfFilename(typed, lastResultName || 'result.pdf');
}

function showModal(title, body, onConfirm) {
  $modalTitle.textContent = title;
  $modalBody.textContent = body;
  _modalCallback = onConfirm;
  $confirmModal.classList.remove('hidden');
  $confirmModal.classList.add('flex');
}
function closeModal() {
  $confirmModal.classList.add('hidden');
  $confirmModal.classList.remove('flex');
  _modalCallback = null;
}
window.closeModal = closeModal;
window.confirmModalAction = function() {
  if (_modalCallback) _modalCallback();
  closeModal();
};

function openLightbox(src) {
  $lightboxImg.src = src;
  $lightbox.classList.remove('hidden');
  $lightbox.classList.add('flex');

  // Derive page number + scale if possible
  const pn = getPageNumberFromPreviewUrl(src);
  lightboxPageNumber = Number.isFinite(pn) ? pn : null;
  lightboxRenderScale = getScaleFromPreviewUrl(src);
  updateLightboxChrome();

  // Prevent background scroll (mobile-friendly)
  document.documentElement.style.overflow = 'hidden';
  document.body.style.overflow = 'hidden';
}

function updateLightboxChrome() {
  if ($lightboxPage) {
    $lightboxPage.textContent = lightboxPageNumber ? `Page ${lightboxPageNumber}` : '';
  }
  const hasNav = !$lightbox.classList.contains('hidden') && !!previewPageCount && previewPageCount > 1;
  if ($lightboxPrev) {
    $lightboxPrev.disabled = !hasNav || !lightboxPageNumber || lightboxPageNumber <= 1;
  }
  if ($lightboxNext) {
    $lightboxNext.disabled = !hasNav || !lightboxPageNumber || lightboxPageNumber >= previewPageCount;
  }
}

function goLightbox(delta) {
  if (!$lightbox || $lightbox.classList.contains('hidden')) return;
  if (!lightboxPageNumber) return;
  if (!previewPageCount) return;

  const nextPage = Math.max(1, Math.min(previewPageCount, lightboxPageNumber + delta));
  if (nextPage === lightboxPageNumber) return;
  const scale = lightboxRenderScale || lightboxScale;
  openLightboxForPage(nextPage, scale);
}

function openLightboxForPage(pageNum, scale) {
  lightboxPageNumber = pageNum;
  const src = pagePreviewUrl(pageNum, scale);
  lightboxRenderScale = getScaleFromPreviewUrl(src);
  openLightbox(src);
}

function pagePreviewUrl(pageNum, scale) {
  const s = typeof scale === 'number' ? scale : previewScale;
  return `/api/preview-page/${previewSessionId}/${pageNum}?scale=${encodeURIComponent(String(s))}`;
}

function getScaleFromPreviewUrl(url) {
  try {
    const u = new URL(url, window.location.origin);
    const s = parseFloat(u.searchParams.get('scale') || '');
    if (!Number.isFinite(s) || s <= 0.05 || s > 6) return previewScale;
    return s;
  } catch {
    return previewScale;
  }
}

function getPageNumberFromPreviewUrl(url) {
  const m = String(url || '').match(/\/api\/preview-page\/[^/]+\/(\d+)/);
  return m ? parseInt(m[1], 10) : NaN;
}

async function refreshPreviewFromFile(file, { showBusy = true } = {}) {
  // Refresh preview session and re-render current tool canvas.
  if (showBusy) setBusy(true, 'Refreshing preview…');
  else setNavStatus('Refreshing preview…');
  try {
    const fd = new FormData();
    fd.append('pdf', file);
    const res = await fetch('/api/preview-session', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    previewSessionId = data.sessionId;
    previewPageCount = data.pageCount;
    $pageCountBadge.textContent = previewPageCount + ' pages';
    renderToolCanvas(currentTool);
  } finally {
    if (showBusy) setBusy(false);
    else setNavStatus('Ready');
  }
}

async function isProbablyPdfBlob(blob) {
  try {
    const slice = blob.slice(0, 8);
    const buf = await slice.arrayBuffer();
    const arr = new Uint8Array(buf);
    const text = String.fromCharCode(...arr);
    return text.startsWith('%PDF-');
  } catch {
    return false;
  }
}

function ensurePdfResponseOrThrow(res) {
  const ct = (res.headers.get('Content-Type') || '').toLowerCase();
  if (!ct.includes('application/pdf')) {
    throw new Error(`Unexpected response type: ${ct || 'unknown'}`);
  }
}
function closeLightbox() {
  $lightbox.classList.add('hidden');
  $lightbox.classList.remove('flex');
  $lightboxImg.src = '';
  lightboxPageNumber = null;
  lightboxRenderScale = null;
  if ($lightboxPage) $lightboxPage.textContent = '';

  if ($lightboxPrev) $lightboxPrev.disabled = true;
  if ($lightboxNext) $lightboxNext.disabled = true;

  document.documentElement.style.overflow = '';
  document.body.style.overflow = '';
}
window.closeLightbox = closeLightbox;

$lightboxClose?.addEventListener('click', (e) => {
  e.preventDefault();
  closeLightbox();
});

$lightboxPrev?.addEventListener('click', (e) => {
  e.preventDefault();
  e.stopPropagation();
  goLightbox(-1);
});

$lightboxNext?.addEventListener('click', (e) => {
  e.preventDefault();
  e.stopPropagation();
  goLightbox(1);
});

window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$lightbox.classList.contains('hidden')) closeLightbox();
  if ($lightbox && !$lightbox.classList.contains('hidden')) {
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      goLightbox(-1);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      goLightbox(1);
    }
  }
});

function setNavStatus(text) {
  $navStatus.textContent = text;
  $navStatus.classList.toggle('hidden', !text);

  // Visual status cue (CSS-defined) so we don't rely on Tailwind JIT for
  // dynamic class names.
  $navStatus.classList.remove('nav-status--busy', 'nav-status--ready');
  if (!text) return;
  const t = String(text).toLowerCase();
  if (t.includes('processing')) {
    $navStatus.classList.add('nav-status--busy');
  } else if (t === 'ready') {
    $navStatus.classList.add('nav-status--ready');
  }
}

function getBusyLabelForTool(tool) {
  if (tool === 'find-replace') return 'TXT processing…';
  return 'Processing…';
}

function setBusy(busy, label) {
  isBusy = busy;
  if ($btnApply) $btnApply.disabled = busy;
  if ($btnApplyM) $btnApplyM.disabled = busy;
  if (busy) {
    if ($btnApply) $btnApply.innerHTML = `<span class="spinner"></span>`;
    if ($btnApplyM) $btnApplyM.innerHTML = `Working<span class="spinner"></span>`;
    setNavStatus(label || 'Processing…');
  } else {
    if ($btnApply) {
      $btnApply.innerHTML = `<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></svg>`;
    }
    if ($btnApplyM) $btnApplyM.textContent = 'Apply';
    setNavStatus('Ready');
  }
}

/* ════════════════════════════════════════
   STATE MANAGEMENT
   ════════════════════════════════════════ */

function showState(state) {
  $stateUpload.classList.add('hidden');
  $stateUpload.classList.remove('flex');
  $stateUploading.classList.add('hidden');
  $stateUploading.classList.remove('flex');
  $stateWorkspace.classList.add('hidden');
  if (state === 'upload') {
    $stateUpload.classList.remove('hidden');
    $stateUpload.classList.add('flex');
    $navFilename.classList.add('hidden');
    $navFilename.classList.remove('flex');
  } else if (state === 'uploading') {
    $stateUploading.classList.remove('hidden');
    $stateUploading.classList.add('flex');
  } else if (state === 'workspace') {
    $stateWorkspace.classList.remove('hidden');
    $navFilename.classList.remove('hidden');
    $navFilename.classList.add('flex');
  }
}

/* ════════════════════════════════════════
   FILE UPLOAD & PREVIEW SESSION
   ════════════════════════════════════════ */

$heroFile.addEventListener('change', handleFileUpload);

function handleFileUpload(e) {
  const file = e.target.files[0];
  if (!file) return;
  currentFile = file;
  $filenameInput.value = file.name.replace(/\.pdf$/i, '');
  lastResultBlob = null;
  lastResultName = '';
  $btnDownload.classList.add('hidden');
  $btnDownloadM?.classList.add('hidden');
  showState('uploading');
  animateProgress();
  createPreviewSession(file);
}

function animateProgress() {
  let pct = 0;
  const iv = setInterval(() => {
    pct += Math.random() * 15 + 5;
    if (pct > 92) pct = 92;
    $progressBar.style.width = pct + '%';
    if (pct >= 92) clearInterval(iv);
  }, 200);
  animateProgress._iv = iv;
}

async function createPreviewSession(file) {
  try {
    const fd = new FormData();
    fd.append('pdf', file);
    const res = await fetch('/api/preview-session', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    previewSessionId = data.sessionId;
    previewPageCount = data.pageCount;
    $pageCountBadge.textContent = previewPageCount + ' pages';

    // Sync hidden form files
    syncFileToHiddenField('ff-pdf', file);
    syncFileToHiddenField('fr-pdf', file);
    syncFileToHiddenField('fp-pdf', file);

    clearInterval(animateProgress._iv);
    $progressBar.style.width = '100%';
    setTimeout(() => {
      showState('workspace');
      window.switchTool(currentTool);
      setNavStatus('Ready');
    }, 400);
  } catch (err) {
    showState('upload');
    showToast('Error', err.message || 'Upload failed');
  }
}

function syncFileToHiddenField(inputId, file) {
  // We can't set value on file inputs; we sync via FormData at submit time.
  const dt = new DataTransfer();
  dt.items.add(file);
  document.getElementById(inputId).files = dt.files;
}

/* ════════════════════════════════════════
   PAGE GRID RENDERING
   ════════════════════════════════════════ */

function renderPageGrid() {
  const pageGridEl = getPageGridEl();
  if (!pageGridEl) return;
  pageGridEl.innerHTML = '';
  for (let i = 1; i <= previewPageCount; i++) {
    const card = document.createElement('div');
    card.className = 'page-thumb stagger-item';
    card.style.animationDelay = `${(i - 1) * 50}ms`;
    card.dataset.page = i;

    const imgWrap = document.createElement('div');
    imgWrap.className = 'w-full h-full flex items-center justify-center relative';
    const img = document.createElement('img');
    img.className = 'max-w-full max-h-full rounded-xl object-contain';
    img.alt = `Page ${i}`;
    img.loading = 'lazy';
    img.src = pagePreviewUrl(i, previewThumbScale);

    // Primary click behavior depends on current tool
    if (currentTool === 'remove') {
      card.addEventListener('click', () => togglePageSelectionWrap(i));
      img.style.cursor = 'pointer';
    } else {
      card.addEventListener('click', () => openLightboxForPage(i, lightboxScale));
      img.style.cursor = 'zoom-in';
    }

    imgWrap.appendChild(img);

    // Maximize button (requested for precision remove; harmless elsewhere)
    const maxBtn = document.createElement('button');
    maxBtn.type = 'button';
    maxBtn.className = 'lux-max-btn';
    maxBtn.title = 'Maximize';
    maxBtn.innerHTML = `<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>`;
    maxBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      openLightboxForPage(i, lightboxScale);
    });
    if (currentTool === 'remove') {
      imgWrap.appendChild(maxBtn);
    }

    const label = document.createElement('div');
    label.className = 'absolute bottom-3 left-0 right-0 text-center text-[10px] font-bold text-slate-400 tracking-widest';
    label.textContent = String(i).padStart(2, '0');
    imgWrap.appendChild(label);

    card.appendChild(imgWrap);
    pageGridEl.appendChild(card);
  }
}

function onPageClick(pageNum, imgSrc) {
  if (currentTool === 'find-replace') {
    // Click to pick-text from preview
    openLightbox(imgSrc);
  } else if (currentTool === 'remove') {
    togglePageSelection(pageNum);
  } else if (currentTool === 'reorder') {
    openLightbox(imgSrc);
  } else {
    openLightbox(imgSrc);
  }
}

function togglePageSelection(pageNum) {
  if (selectedPages.has(pageNum)) selectedPages.delete(pageNum);
  else selectedPages.add(pageNum);
  updatePageSelectionUI();
  updateRemoveHiddenField();
}

function updatePageSelectionUI() {
  document.querySelectorAll('.page-thumb').forEach(el => {
    const p = parseInt(el.dataset.page);
    el.classList.toggle('selected', selectedPages.has(p));
  });
}

function updateRemoveHiddenField() {
  document.getElementById('fp-pages').value = [...selectedPages].sort((a,b) => a-b).join(',');
}

/* ════════════════════════════════════════
   TOOL SWITCHING
   ════════════════════════════════════════ */

window.switchTool = function(tool) {
  currentTool = tool;
  // Update sidebar buttons
  document.querySelectorAll('.lux-tool-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tool === tool);
  });
  // Render tool options + adjust canvas
  renderToolOptions(tool);
  renderToolCanvas(tool);

  // Reset download state when switching tool
  lastResultBlob = null;
  lastResultName = '';
  $btnDownload.classList.add('hidden');
  $btnDownloadM?.classList.add('hidden');
};

function renderToolOptions(tool) {
  let html = '';
  switch (tool) {
    case 'find-replace':
      html = buildFindReplaceOptions();
      break;
    case 'reorder':
      html = buildReorderOptions();
      break;
    case 'merge':
      html = buildMergeOptions();
      break;
    case 'remove':
      html = buildRemoveOptions();
      break;
  }
  $toolOptions.innerHTML = html;
  afterToolOptionsBind(tool);
}

function renderToolCanvas(tool) {
  $canvasActions.innerHTML = '';
  switch (tool) {
    case 'find-replace':
      $canvasTitle.textContent = 'Document Preview';
      $canvasSubtitle.textContent = 'Click a page to pick text';
      $canvasBody.innerHTML = `<div id="page-grid" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-6"></div>`;
      renderPageGrid();
      break;
    case 'reorder':
      $canvasTitle.textContent = 'Page Order';
      $canvasSubtitle.textContent = 'Drag to rearrange';
      renderReorderCanvas();
      break;
    case 'merge':
      $canvasTitle.textContent = 'Merge Queue';
      $canvasSubtitle.textContent = 'Add PDFs below';
      renderMergeCanvas();
      break;
    case 'remove':
      $canvasTitle.textContent = 'Select Pages to Remove';
      $canvasSubtitle.textContent = 'Click pages to mark';
      selectedPages.clear();
      $canvasBody.innerHTML = `<div id="page-grid" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-6"></div>`;
      renderPageGrid();
      break;
  }
}

/* ════════════════════════════════════════
   FIND & REPLACE — OPTIONS
   ════════════════════════════════════════ */

function buildFindReplaceOptions() {
  return `
    <div class="space-y-5">
      <div class="lux-input-group">
        <label>Find Text</label>
        <input type="text" id="lux-findText" class="lux-input" placeholder="Search for…" />
      </div>
      <div class="lux-input-group">
        <label>Replace With</label>
        <input type="text" id="lux-replaceText" class="lux-input" placeholder="Replace…" />
      </div>

      <div class="bg-slate-50 border border-slate-100 rounded-2xl p-4">
        <p class="text-[10px] font-bold tracking-[0.25em] text-slate-400 uppercase mb-2">Select Text</p>
        <div class="flex items-center gap-2">
          <button type="button" id="lux-pick-btn" class="lux-mini-btn">Pick from page</button>
          <button type="button" id="lux-use-picked-replace" class="lux-mini-btn lux-mini-btn-alt" disabled>Use picked as replace</button>
        </div>
        <p class="text-xs text-slate-400 mt-3" id="lux-picked-status">Picked: <span class="font-semibold">—</span></p>
        <p class="text-[10px] text-slate-300 mt-1">Click a page to open it, then click a word.</p>
      </div>

      <div class="lux-input-group">
        <label>Font Mode</label>
        <select id="lux-fontMode" class="lux-select">
          <option value="auto" selected>Auto Detect</option>
          <option value="manual">Manual</option>
        </select>
      </div>
      <div id="lux-manual-font-section" class="hidden space-y-4">
        <div class="lux-input-group">
          <label>Font Choice</label>
          <select id="lux-fontChoice" class="lux-select">
            <option value="">— Select —</option>
            <option value="helv">Helvetica</option>
            <option value="tiro">Times Roman</option>
            <option value="cour">Courier</option>
          </select>
        </div>
        <div class="lux-input-group">
          <label>Upload Custom Font (.ttf/.otf)</label>
          <input type="file" id="lux-extraFonts" accept=".ttf,.otf" class="lux-file-input" multiple />
        </div>
      </div>
      <div class="lux-input-group">
        <label>Scope</label>
        <select id="lux-scope" class="lux-select">
          <option value="all" selected>All Pages</option>
          <option value="range">Page Range</option>
        </select>
      </div>
      <div id="lux-range-section" class="hidden grid grid-cols-2 gap-3">
        <div class="lux-input-group">
          <label>From</label>
          <input type="number" id="lux-fromPage" class="lux-input" min="1" value="1" />
        </div>
        <div class="lux-input-group">
          <label>To</label>
          <input type="number" id="lux-toPage" class="lux-input" min="1" value="1" />
        </div>
      </div>
    </div>`;
}

function afterToolOptionsBind(tool) {
  if (tool === 'find-replace') {
    const fontMode = document.getElementById('lux-fontMode');
    const manSection = document.getElementById('lux-manual-font-section');
    fontMode?.addEventListener('change', () => {
      manSection.classList.toggle('hidden', fontMode.value !== 'manual');
    });
    const scope = document.getElementById('lux-scope');
    const rangeSection = document.getElementById('lux-range-section');
    scope?.addEventListener('change', () => {
      rangeSection.classList.toggle('hidden', scope.value !== 'range');
    });

    const pickBtn = document.getElementById('lux-pick-btn');
    pickBtn?.addEventListener('click', () => {
      showToast('Pick Text', 'Click any page, then click a word.');
    });
    const usePickedReplace = document.getElementById('lux-use-picked-replace');
    if (usePickedReplace) usePickedReplace.disabled = !lastPickedText;

    const status = document.getElementById('lux-picked-status');
    if (status) {
      status.innerHTML = `Picked: <span class="font-semibold"></span>`;
      status.querySelector('span').textContent = lastPickedText || '—';
    }

    usePickedReplace?.addEventListener('click', () => {
      const replaceInput = document.getElementById('lux-replaceText');
      if (replaceInput && lastPickedText) {
        replaceInput.value = lastPickedText;
        replaceInput.focus();
      }
    });
  }
}

/* ════════════════════════════════════════
   REORDER — OPTIONS + CANVAS
   ════════════════════════════════════════ */

function buildReorderOptions() {
  return `
    <div class="space-y-4">
      <p class="text-xs text-slate-400 leading-relaxed">Drag and drop the pages below to set the new order. The order will be applied when you hit Apply.</p>
      <div class="lux-input-group">
        <label>Custom Order (comma-separated)</label>
        <input type="text" id="lux-reorder-input" class="lux-input" placeholder="e.g. 3,1,2,4" />
      </div>
    </div>`;
}

function renderReorderCanvas() {
  // Init reorder list
  reorderList = [];
  for (let i = 1; i <= previewPageCount; i++) reorderList.push(i);
  drawReorderList();
}

function drawReorderList() {
  $canvasBody.innerHTML = '';
  const list = document.createElement('div');
  list.className = 'space-y-3';
  list.id = 'reorder-list';
  reorderList.forEach((pageNum, idx) => {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-4 bg-white rounded-2xl p-4 border border-slate-100 shadow-sm cursor-grab active:cursor-grabbing hover:shadow-md transition-all duration-300';
    row.draggable = true;
    row.dataset.idx = idx;

    row.innerHTML = `
      <span class="text-xs font-bold text-slate-300 w-6 text-center">${idx + 1}</span>
      <img src="${pagePreviewUrl(pageNum, 0.5)}" class="w-12 h-16 rounded-lg object-cover border border-slate-100 lux-zoomable" alt="Page ${pageNum}" loading="lazy" />
      <div class="flex-1">
        <span class="text-sm font-semibold">Page ${pageNum}</span>
      </div>
      <button type="button" class="lux-mini-btn" title="Maximize">Max</button>
      <svg class="w-5 h-5 text-slate-300" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 8h16M4 16h16"/></svg>
    `;

    const thumb = row.querySelector('img');
    thumb?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      openLightboxForPage(pageNum, lightboxScale);
    });
    const maxBtn = row.querySelector('button');
    maxBtn?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      openLightboxForPage(pageNum, lightboxScale);
    });

    row.addEventListener('dragstart', onReorderDragStart);
    row.addEventListener('dragover', onReorderDragOver);
    row.addEventListener('drop', onReorderDrop);
    row.addEventListener('dragend', onReorderDragEnd);
    list.appendChild(row);
  });
  $canvasBody.appendChild(list);
  syncReorderHiddenField();
}

let _dragIdx = null;
function onReorderDragStart(e) {
  _dragIdx = parseInt(this.dataset.idx);
  this.style.opacity = '0.4';
  e.dataTransfer.effectAllowed = 'move';
}
function onReorderDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  this.classList.add('ring-2', 'ring-indigo-400');
}
function onReorderDragEnd() {
  this.style.opacity = '1';
  document.querySelectorAll('#reorder-list > div').forEach(el => el.classList.remove('ring-2', 'ring-indigo-400'));
}
function onReorderDrop(e) {
  e.preventDefault();
  const toIdx = parseInt(this.dataset.idx);
  if (_dragIdx === null || _dragIdx === toIdx) return;
  const [moved] = reorderList.splice(_dragIdx, 1);
  reorderList.splice(toIdx, 0, moved);
  drawReorderList();
  // Also update the text input
  const inp = document.getElementById('lux-reorder-input');
  if (inp) inp.value = reorderList.join(',');
}

function syncReorderHiddenField() {
  document.getElementById('fr-order').value = reorderList.join(',');
}

/* ════════════════════════════════════════
   MERGE — OPTIONS + CANVAS
   ════════════════════════════════════════ */

function buildMergeOptions() {
  return `
    <div class="space-y-4">
      <p class="text-xs text-slate-400 leading-relaxed">Upload multiple PDFs to merge them into a single document. The current PDF is included as the first file.</p>
      <div class="lux-input-group">
        <label>Add More PDFs</label>
        <input type="file" id="lux-merge-files" accept=".pdf" class="lux-file-input" multiple />
      </div>
    </div>`;
}

function renderMergeCanvas() {
  mergeFiles = currentFile ? [currentFile] : [];
  drawMergeList();

  setTimeout(() => {
    const inp = document.getElementById('lux-merge-files');
    if (inp) {
      inp.addEventListener('change', () => {
        for (const f of inp.files) mergeFiles.push(f);
        drawMergeList();
      });
    }
  }, 50);
}

function drawMergeList() {
  $canvasBody.innerHTML = '';
  if (mergeFiles.length === 0) {
    $canvasBody.innerHTML = '<p class="text-slate-300 text-sm text-center py-12">No files added yet.</p>';
    return;
  }
  const list = document.createElement('div');
  list.className = 'space-y-3';
  mergeFiles.forEach((f, idx) => {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-4 bg-white rounded-2xl p-4 border border-slate-100 shadow-sm hover:shadow-md transition-all duration-300';
    row.innerHTML = `
      <span class="text-xs font-bold text-slate-300 w-6 text-center">${idx + 1}</span>
      <div class="w-10 h-10 bg-indigo-50 rounded-xl flex items-center justify-center flex-shrink-0">
        <svg class="w-5 h-5 text-indigo-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"/></svg>
      </div>
      <div class="flex-1 min-w-0">
        <p class="text-sm font-semibold truncate">${f.name}</p>
        <p class="text-[10px] text-slate-400">${(f.size / 1024).toFixed(1)} KB</p>
      </div>
      ${idx > 0 ? `<button onclick="removeMergeFile(${idx})" class="w-8 h-8 rounded-lg bg-red-50 text-red-400 flex items-center justify-center hover:bg-red-100 transition-colors"><svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 18L18 6M6 6l12 12"/></svg></button>` : ''}
    `;
    list.appendChild(row);
  });
  $canvasBody.appendChild(list);
  syncMergeHiddenField();
}

window.removeMergeFile = function(idx) {
  mergeFiles.splice(idx, 1);
  drawMergeList();
};

function syncMergeHiddenField() {
  const dt = new DataTransfer();
  mergeFiles.forEach(f => dt.items.add(f));
  document.getElementById('fm-pdfs').files = dt.files;
}

/* ════════════════════════════════════════
   REMOVE PAGES — OPTIONS
   ════════════════════════════════════════ */

function buildRemoveOptions() {
  return `
    <div class="space-y-4">
      <p class="text-xs text-slate-400 leading-relaxed">Click pages in the grid to select them for removal. Selected pages will be highlighted.</p>
      <div class="lux-input-group">
        <label>Pages to Remove</label>
        <input type="text" id="lux-remove-input" class="lux-input" placeholder="e.g. 2,4,5" readonly />
      </div>
      <p class="text-[10px] text-slate-300">Selected: <span id="remove-count">0</span> pages</p>
    </div>`;
}

/* ════════════════════════════════════════
   PICK-TEXT FROM PREVIEW (lightbox click)
   ════════════════════════════════════════ */

// Backdrop click closes; image click picks text (find/replace) or does nothing.
$lightbox.addEventListener('click', async function(e) {
  // Only close when clicking the backdrop itself.
  if (e.target === $lightbox) {
    closeLightbox();
    return;
  }

  // Ignore clicks on chrome/buttons/header.
  if (e.target !== $lightboxImg) return;

  if (currentTool !== 'find-replace') {
    return;
  }

  // Get click coords relative to image
  const rect = $lightboxImg.getBoundingClientRect();
  const x = (e.clientX - rect.left) * ($lightboxImg.naturalWidth / rect.width);
  const y = (e.clientY - rect.top) * ($lightboxImg.naturalHeight / rect.height);

  // Figure out which page this is
  const src = $lightboxImg.src;
  const match = src.match(/\/api\/preview-page\/([^/]+)\/(\d+)/);
  if (!match) { return; }
  const sessionId = match[1];
  const pageNumber = getPageNumberFromPreviewUrl(src);
  const renderScale = getScaleFromPreviewUrl(src);

  try {
    const res = await fetch('/api/preview-pick-word', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId, pageNumber, x, y, scale: renderScale })
    });
    if (res.ok) {
      const data = await res.json();
      if (data.text) {
        lastPickedText = data.text;
        const findInput = document.getElementById('lux-findText');
        if (findInput) {
          findInput.value = data.text;
          findInput.focus();
          showToast('Text Picked', `"${data.text}" selected`);
        }

        const status = document.getElementById('lux-picked-status');
        if (status) {
          status.innerHTML = `Picked: <span class="font-semibold"></span>`;
          status.querySelector('span').textContent = data.text;
        }
        const usePickedReplace = document.getElementById('lux-use-picked-replace');
        if (usePickedReplace) usePickedReplace.disabled = !lastPickedText;
      }
    }
  } catch (err) {
    // silent
  }
  // Keep it open so user can try again if needed.
});

/* ════════════════════════════════════════
   APPLY BUTTON — Submit to Backend
   ════════════════════════════════════════ */

$btnApply.addEventListener('click', async () => {
  if (isBusy) return;
  if (!currentFile && currentTool !== 'merge') {
    showToast('No File', 'Please upload a PDF first.');
    return;
  }

  setBusy(true, getBusyLabelForTool(currentTool));

  try {
    let res;
    switch (currentTool) {
      case 'find-replace':
        res = await submitFindReplace();
        break;
      case 'reorder':
        res = await submitReorder();
        break;
      case 'merge':
        res = await submitMerge();
        break;
      case 'remove':
        res = await submitRemove();
        break;
    }

    if (res && res.ok) {
      ensurePdfResponseOrThrow(res);
      const outBlob = await res.blob();
      if (!outBlob || outBlob.size < 200) {
        throw new Error('Received an unexpectedly small PDF output.');
      }
      const cd = res.headers.get('Content-Disposition') || '';
      const fnMatch = cd.match(/filename="?([^"]+)"?/);
      // Server may provide a filename, but user input should take precedence for downloads.
      const headerName = fnMatch ? fnMatch[1] : 'result.pdf';
      lastResultName = sanitizePdfFilename(($filenameInput?.value || '').trim(), headerName);

      // Fast validation: PDF magic header check (avoids extra server roundtrip).
      const nextName = lastResultName || getDesiredDownloadName();
      const nextFile = new File([outBlob], nextName, { type: 'application/pdf' });
      const okPdf = await isProbablyPdfBlob(outBlob);
      if (!okPdf) {
        throw new Error('Generated output is not a valid PDF (header check failed).');
      }

      lastResultBlob = outBlob;

      // Show debug info for find-replace
      if (currentTool === 'find-replace') {
        const count = res.headers.get('X-Replacements') || '0';
        const detFont = res.headers.get('X-Detected-Font') || '';
        const usedFont = res.headers.get('X-Used-Font') || '';
        let msg = `${count} replacement(s) made.`;
        if (detFont) msg += ` Detected: ${detFont}.`;
        if (usedFont) msg += ` Used: ${usedFont}.`;
        showToast('Success', msg, 5000);
      } else {
        showToast('Success', 'Operation completed successfully.');
      }

      $btnDownload.classList.remove('hidden');
      $btnDownloadM?.classList.remove('hidden');

      // Promote result as the new working file + refresh preview
      currentFile = nextFile;
      syncFileToHiddenField('ff-pdf', nextFile);
      syncFileToHiddenField('fr-pdf', nextFile);
      syncFileToHiddenField('fp-pdf', nextFile);

      // Refresh preview in background so Apply returns faster (important on Render free tier).
      refreshPreviewFromFile(nextFile, { showBusy: false }).catch(() => {
        showToast('Note', 'Preview refresh failed; download still available.');
      });
    } else if (res) {
      let errText = '';
      try { errText = await res.text(); } catch(_) {}
      try { errText = JSON.parse(errText).detail || errText; } catch(_) {}
      showToast('Error', errText || 'Operation failed.');
    }
  } catch (err) {
    showToast('Error', err.message || 'Something went wrong.');
  } finally {
    setBusy(false);
  }
});

async function submitFindReplace() {
  const findVal = (document.getElementById('lux-findText')?.value || '').trim();
  if (!findVal) {
    showToast('Missing Find Text', 'Pick a word or type the text to find.');
    return null;
  }

  const fd = new FormData();
  fd.append('pdf', currentFile);
  fd.append('findText', findVal);
  fd.append('replaceText', document.getElementById('lux-replaceText')?.value || '');

  const fontMode = document.getElementById('lux-fontMode')?.value || 'auto';
  if (fontMode === 'manual') {
    const fc = document.getElementById('lux-fontChoice')?.value || '';
    if (fc) fd.append('fontChoice', fc);
    const extraInput = document.getElementById('lux-extraFonts');
    if (extraInput && extraInput.files) {
      for (const f of extraInput.files) fd.append('extraFonts', f);
    }
  }

  const scope = document.getElementById('lux-scope')?.value || 'all';
  fd.append('scope', scope);
  if (scope === 'range') {
    const fromVal = (document.getElementById('lux-fromPage')?.value || '1').trim();
    const toVal = (document.getElementById('lux-toPage')?.value || '1').trim();
    fd.append('fromPage', fromVal);
    fd.append('toPage', toVal);
  }

  return fetch('/api/find-replace', { method: 'POST', body: fd });
}

async function submitReorder() {
  // Use drag list or text input
  const inp = document.getElementById('lux-reorder-input');
  let order = reorderList.join(',');
  if (inp && inp.value.trim()) order = inp.value.trim();

  const fd = new FormData();
  fd.append('pdf', currentFile);
  fd.append('order', order);
  return fetch('/api/reorder', { method: 'POST', body: fd });
}

async function submitMerge() {
  if (mergeFiles.length < 2) {
    showToast('Need More Files', 'Add at least 2 PDFs to merge.');
    return null;
  }
  const fd = new FormData();
  mergeFiles.forEach(f => fd.append('pdfs', f));
  return fetch('/api/merge', { method: 'POST', body: fd });
}

async function submitRemove() {
  if (selectedPages.size === 0) {
    showToast('No Selection', 'Click pages to select them for removal.');
    return null;
  }
  const pages = [...selectedPages].sort((a,b) => a-b).join(',');
  const fd = new FormData();
  fd.append('pdf', currentFile);
  fd.append('pages', pages);
  return fetch('/api/remove-pages', { method: 'POST', body: fd });
}

/* ════════════════════════════════════════
   DOWNLOAD BUTTON
   ════════════════════════════════════════ */

$btnDownload.addEventListener('click', () => {
  if (!lastResultBlob) return;
  const url = URL.createObjectURL(lastResultBlob);
  const a = document.createElement('a');
  a.href = url;
  a.download = getDesiredDownloadName();
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 200);
});

$btnDownloadM?.addEventListener('click', () => {
  $btnDownload.click();
});

/* ════════════════════════════════════════
   RESET BUTTON
   ════════════════════════════════════════ */

$btnReset.addEventListener('click', () => {
  showModal('Reset Workspace', 'Upload a new PDF? Current progress will be lost.', () => {
    currentFile = null;
    previewSessionId = null;
    previewPageCount = 0;
    lastResultBlob = null;
    lastResultName = '';
    selectedPages.clear();
    reorderList = [];
    mergeFiles = [];
    $btnDownload.classList.add('hidden');
    $heroFile.value = '';
    showState('upload');
    setNavStatus('');
  });
});

$btnApplyM?.addEventListener('click', () => {
  $btnApply.click();
});

$btnResetM?.addEventListener('click', () => {
  $btnReset.click();
});

/* ════════════════════════════════════════
   REMOVE TOOL — update counter
   ════════════════════════════════════════ */

const _origToggle = togglePageSelection;
// We override to also update sidebar counter
function togglePageSelectionWrap(pageNum) {
  if (selectedPages.has(pageNum)) selectedPages.delete(pageNum);
  else selectedPages.add(pageNum);
  updatePageSelectionUI();
  updateRemoveHiddenField();
  // Update sidebar
  const countEl = document.getElementById('remove-count');
  if (countEl) countEl.textContent = selectedPages.size;
  const inp = document.getElementById('lux-remove-input');
  if (inp) inp.value = [...selectedPages].sort((a,b) => a-b).join(', ');
}
// Replace original
window.togglePageSelection = togglePageSelectionWrap;

// Patch onPageClick to use the wrapped version
function onPageClickPatched(pageNum, imgSrc) {
  if (currentTool === 'find-replace') {
    openLightbox(imgSrc);
  } else if (currentTool === 'remove') {
    togglePageSelectionWrap(pageNum);
  } else {
    openLightbox(imgSrc);
  }
}

/* ════════════════════════════════════════
   INITIAL STATE
   ════════════════════════════════════════ */

showState('upload');

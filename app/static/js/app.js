import API from '/static/js/api.js';
import { t, applyI18n, setLang, langSelectorHtml } from '/static/js/i18n.js';

// state
const state = {
    currentPath: '/',
    items: [],
    selected: new Set(),
    viewMode: localStorage.getItem('nkcloud_view') || 'list',
    sortBy: localStorage.getItem('nkcloud_sort') || 'name',
    sortAsc: true,
    stats: null,
    user: null, // { id, username, role, quota_bytes, used_bytes }
    cursorIdx: -1, // keyboard cursor index (in sorted items)
};

// icons
const ICONS = {
    folder: '<svg width="24" height="24" viewBox="0 0 24 24" fill="#6c8cff" stroke="none"><path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"/></svg>',
    file: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#8b8fa3" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    image: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#4ecdc4" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
    video: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ff6b6b" stroke-width="1.5"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>',
    audio: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ffa94d" stroke-width="1.5"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
    pdf: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ff6b6b" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    archive: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#8b8fa3" stroke-width="1.5"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>',
    check: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>',
};

const IMAGE_EXTS = ['jpg','jpeg','png','gif','webp','bmp','svg','heic','heif','avif','tiff','tif'];
const NEEDS_RENDER_EXTS = ['heic','heif','tiff','tif'];
const VIDEO_EXTS_ALL = ['mp4','mkv','webm','avi','mov','m4v','flv','wmv'];
const VIDEO_EXTS_PLAYABLE = ['mp4','webm','mov','m4v'];
const AUDIO_EXTS = ['mp3','flac','ogg','wav','m4a','aac','opus','wma'];

function getFileIcon(item) {
    if (item.is_dir) return ICONS.folder;
    const ext = item.name.split('.').pop().toLowerCase();
    if (IMAGE_EXTS.includes(ext)) return ICONS.image;
    if (VIDEO_EXTS_ALL.includes(ext)) return ICONS.video;
    if (AUDIO_EXTS.includes(ext)) return ICONS.audio;
    if (ext === 'pdf') return ICONS.pdf;
    if (['zip','tar','gz','7z','rar','bz2'].includes(ext)) return ICONS.archive;
    return ICONS.file;
}

function isMedia(item) {
    const ext = item.name.split('.').pop().toLowerCase();
    return IMAGE_EXTS.includes(ext)
        || VIDEO_EXTS_ALL.includes(ext)
        || AUDIO_EXTS.includes(ext);
}

function getMediaType(item) {
    const ext = item.name.split('.').pop().toLowerCase();
    if (IMAGE_EXTS.includes(ext)) return 'image';
    if (VIDEO_EXTS_ALL.includes(ext)) return 'video';
    if (AUDIO_EXTS.includes(ext)) return 'audio';
    return null;
}

// utils
function formatSize(bytes) {
    if (!bytes) return '-';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (bytes >= 1024 && i < u.length - 1) { bytes /= 1024; i++; }
    return bytes.toFixed(i > 0 ? 1 : 0) + ' ' + u[i];
}

function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('zh-TW', { year: 'numeric', month: '2-digit', day: '2-digit' })
        + ' ' + d.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function toast(msg, type = '') {
    const c = document.getElementById('toasts');
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => t.remove(), 3000);
}

// rendering
function renderBreadcrumbs() {
    const el = document.getElementById('breadcrumbs');
    const parts = state.currentPath.split('/').filter(Boolean);
    let html = `<a href="#/" data-path="/">${t('breadcrumb.home')}</a>`;
    let cumulative = '';
    for (const part of parts) {
        cumulative += '/' + part;
        html += `<span class="sep">/</span><a href="#${cumulative}" data-path="${escapeHtml(cumulative)}">${escapeHtml(part)}</a>`;
    }
    el.innerHTML = html;
    el.querySelectorAll('a').forEach(a => {
        a.addEventListener('click', e => {
            e.preventDefault();
            navigateTo(a.dataset.path);
        });
    });
}

function sortItems(items) {
    const sorted = [...items];
    sorted.sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        let cmp = 0;
        if (state.sortBy === 'name') cmp = a.name.localeCompare(b.name, 'zh');
        else if (state.sortBy === 'size') cmp = a.size - b.size;
        else if (state.sortBy === 'modified') cmp = (a.modified || '').localeCompare(b.modified || '');
        return state.sortAsc ? cmp : -cmp;
    });
    return sorted;
}

function renderFiles() {
    const container = document.getElementById('fileContainer');
    const sorted = sortItems(state.items);

    if (sorted.length === 0) {
        container.innerHTML = `<div class="empty-state">
            <div class="empty-icon">&#128193;</div>
            <div class="empty-text">${t('files.empty')}</div>
        </div>`;
        updateToolbar();
        return;
    }

    if (state.viewMode === 'grid') {
        renderGridView(container, sorted);
    } else {
        renderListView(container, sorted);
    }
    updateToolbar();
}

function renderListView(container, items) {
    const animate = state._animateNext;
    const staggerCls = animate ? ' stagger-in' : '';
    let html = `<ul class="file-list${staggerCls}">`;
    items.forEach((item, i) => {
        const selected = state.selected.has(item.path) ? ' selected' : '';
        const cursor = state.cursorIdx === i ? ' cursor' : '';
        const thumbHtml = !item.is_dir && item.has_thumb
            ? `<img src="${API.thumbUrl(item.path)}" loading="lazy" alt="">`
            : getFileIcon(item);
        const delay = animate ? ` style="animation-delay:${Math.min(i, 19) * 30}ms"` : '';
        html += `<li class="file-item${selected}${cursor}" data-path="${escapeHtml(item.path)}" data-idx="${i}" data-dir="${item.is_dir}"${delay}>
            <div class="checkbox">${state.selected.has(item.path) ? ICONS.check : ''}</div>
            <div class="icon">${thumbHtml}</div>
            <div class="name">${escapeHtml(item.name)}</div>
            <div class="meta size">${item.is_dir ? '' : formatSize(item.size)}</div>
            <div class="meta date">${formatDate(item.modified)}</div>
        </li>`;
    });
    html += '</ul>';
    container.innerHTML = html;
    bindFileEvents(container);
    state._animateNext = false;
}

function renderGridView(container, items) {
    const animate = state._animateNext;
    const staggerCls = animate ? ' stagger-in' : '';
    let html = `<div class="file-grid${staggerCls}">`;
    items.forEach((item, i) => {
        const selected = state.selected.has(item.path) ? ' selected' : '';
        const cursor = state.cursorIdx === i ? ' cursor' : '';
        const thumbContent = !item.is_dir && item.has_thumb
            ? `<img src="${API.thumbUrl(item.path)}" loading="lazy" alt="">`
            : `<div style="font-size:40px">${item.is_dir ? '&#128193;' : getFileIcon(item)}</div>`;
        const delay = animate ? ` style="animation-delay:${Math.min(i, 19) * 30}ms"` : '';
        html += `<div class="grid-item${selected}${cursor}" data-path="${escapeHtml(item.path)}" data-idx="${i}" data-dir="${item.is_dir}"${delay}>
            <div class="grid-check">${state.selected.has(item.path) ? ICONS.check : ''}</div>
            <div class="thumb">${thumbContent}</div>
            <div class="grid-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
            <div class="grid-meta">${item.is_dir ? '' : formatSize(item.size)}</div>
        </div>`;
    });
    html += '</div>';
    container.innerHTML = html;
    bindFileEvents(container);
    state._animateNext = false;
}

function bindFileEvents(container) {
    const items = container.querySelectorAll('.file-item, .grid-item');
    items.forEach(el => {
        el.addEventListener('click', (e) => {
            // Swallow ghost clicks that bleed through right after closing the viewer
            if (window._suppressFileClick) { e.stopPropagation(); return; }
            const path = el.dataset.path;
            const isDir = el.dataset.dir === 'true';
            const isCheckbox = e.target.closest('.checkbox, .grid-check');

            if (isCheckbox || e.ctrlKey || e.metaKey) {
                e.preventDefault();
                toggleSelect(path);
                return;
            }
            if (e.shiftKey && state.selected.size > 0) {
                e.preventDefault();
                rangeSelect(path);
                return;
            }

            state.selected.clear();
            // Update cursor to clicked index
            const idx = parseInt(el.dataset.idx, 10);
            if (!isNaN(idx)) state.cursorIdx = idx;
            if (isDir) {
                navigateTo(path);
            } else {
                const item = state.items.find(i => i.path === path);
                if (item && isMedia(item)) {
                    openViewer(item);
                } else if (item && item.is_text) {
                    openTextViewer(item);
                } else {
                    window.open(API.downloadUrl(path), '_blank');
                }
            }
        });

        el.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            const path = el.dataset.path;
            if (!state.selected.has(path)) {
                state.selected.clear();
                state.selected.add(path);
                renderFiles();
            }
            showContextMenu(e.clientX, e.clientY);
        });
    });
}

function toggleSelect(path) {
    if (state.selected.has(path)) state.selected.delete(path);
    else state.selected.add(path);
    renderFiles();
}

function rangeSelect(path) {
    const sorted = sortItems(state.items);
    const paths = sorted.map(i => i.path);
    const lastSelected = [...state.selected].pop();
    const startIdx = paths.indexOf(lastSelected);
    const endIdx = paths.indexOf(path);
    if (startIdx === -1 || endIdx === -1) return;
    const [from, to] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
    for (let i = from; i <= to; i++) state.selected.add(paths[i]);
    renderFiles();
}

function updateToolbar() {
    const toolbar = document.getElementById('toolbar');
    const count = state.selected.size;
    if (count === 0) {
        toolbar.classList.remove('visible');
        return;
    }
    toolbar.classList.add('visible');
    toolbar.querySelector('.count').textContent = t('toolbar.selected', {n: count});
}

// --- Navigation ---
async function navigateTo(path) {
    try {
        const data = await API.listFiles(path);
        state.currentPath = data.path;
        state.items = data.items;
        state.selected.clear();
        state.cursorIdx = -1;
        state._animateNext = true;
        window.location.hash = '#' + data.path;
        renderBreadcrumbs();
        renderFiles();
    } catch (err) {
        toast(t('files.error_load'), 'error');
    }
}

function scrollCursorIntoView() {
    const el = document.querySelector('.file-item.cursor, .grid-item.cursor');
    if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function moveCursor(delta) {
    const sorted = sortItems(state.items);
    if (sorted.length === 0) return;
    let idx = state.cursorIdx < 0 ? 0 : state.cursorIdx + delta;
    idx = Math.max(0, Math.min(sorted.length - 1, idx));
    state.cursorIdx = idx;
    renderFiles();
    scrollCursorIntoView();
}

function getGridColumns() {
    const items = document.querySelectorAll('.file-grid .grid-item');
    if (items.length < 2) return 1;
    const firstTop = items[0].offsetTop;
    let cols = 1;
    for (let i = 1; i < items.length; i++) {
        if (items[i].offsetTop !== firstTop) break;
        cols++;
    }
    return cols;
}

function activateCursor() {
    const sorted = sortItems(state.items);
    if (state.cursorIdx < 0 || state.cursorIdx >= sorted.length) return;
    const item = sorted[state.cursorIdx];
    if (item.is_dir) {
        navigateTo(item.path);
    } else if (isMedia(item)) {
        openViewer(item);
    } else if (item.is_text) {
        openTextViewer(item);
    } else {
        window.open(API.downloadUrl(item.path), '_blank');
    }
}

// --- Context Menu ---
function showContextMenu(x, y) {
    const menu = document.getElementById('contextMenu');
    const selectedPaths = [...state.selected];
    const single = selectedPaths.length === 1;
    const item = single ? state.items.find(i => i.path === selectedPaths[0]) : null;

    let html = '';
    if (single && item) {
        if (item.is_dir) {
            html += menuItem(t('context.open'), 'open');
            html += menuItem(t('context.download_zip'), 'download-zip');
        } else {
            html += menuItem(t('context.download'), 'download');
            if (isMedia(item)) html += menuItem(t('context.preview'), 'preview');
        }
        html += '<div class="context-menu-sep"></div>';
        html += menuItem(t('context.rename'), 'rename');
        html += menuItem(t('context.share'), 'share');
    }
    if (selectedPaths.length > 0) {
        if (single && item) {
            // already have items above
        } else {
            html += menuItem(t('context.download'), 'download');
        }
        html += menuItem(t('context.move_to'), 'move');
        html += '<div class="context-menu-sep"></div>';
        html += menuItem(t('context.delete'), 'delete', true);
    }

    menu.innerHTML = html;
    menu.style.left = Math.min(x, window.innerWidth - 200) + 'px';
    menu.style.top = Math.min(y, window.innerHeight - 300) + 'px';
    menu.classList.add('visible');

    menu.querySelectorAll('.context-menu-item').forEach(el => {
        el.addEventListener('click', () => {
            menu.classList.remove('visible');
            handleContextAction(el.dataset.action);
        });
    });
}

function menuItem(label, action, danger = false) {
    return `<div class="context-menu-item${danger ? ' danger' : ''}" data-action="${action}">${escapeHtml(label)}</div>`;
}

function handleContextAction(action) {
    const paths = [...state.selected];
    const item = state.items.find(i => i.path === paths[0]);

    switch (action) {
        case 'open':
            navigateTo(paths[0]);
            break;
        case 'download':
            if (paths.length === 1 && item && !item.is_dir) {
                window.open(API.downloadUrl(paths[0]), '_blank');
            } else if (paths.length === 1 && item?.is_dir) {
                window.open(API.downloadZipUrl(paths[0]), '_blank');
            } else if (paths.length > 1) {
                window.open(API.downloadBatchUrl(paths), '_blank');
            }
            break;
        case 'download-zip':
            window.open(API.downloadZipUrl(paths[0]), '_blank');
            break;
        case 'preview':
            if (item) openViewer(item);
            break;
        case 'rename':
            promptRename(item);
            break;
        case 'share':
            if (item) openShareDialog(item);
            break;
        case 'move':
            openMoveDialog(paths);
            break;
        case 'delete':
            confirmDelete(paths);
            break;
    }
}

// --- Media Viewer ---
let viewerMediaItems = [];
let viewerIndex = 0;

function openViewer(item) {
    viewerMediaItems = sortItems(state.items).filter(i => !i.is_dir && isMedia(i));
    viewerIndex = viewerMediaItems.findIndex(i => i.path === item.path);
    if (viewerIndex === -1) return;
    showViewerContent();
    document.getElementById('viewerOverlay').classList.add('visible');
    document.addEventListener('keydown', viewerKeyHandler);
}

function showViewerContent() {
    const item = viewerMediaItems[viewerIndex];
    const content = document.getElementById('viewerContent');
    const type = getMediaType(item);
    document.getElementById('viewerFilename').textContent = item.name;
    document.getElementById('viewerOverlay').classList.remove('text-mode');

    const ext = item.name.split('.').pop().toLowerCase();
    if (type === 'image') {
        const src = NEEDS_RENDER_EXTS.includes(ext) ? API.previewUrl(item.path) : API.streamUrl(item.path);
        content.innerHTML = `<div class="zoom-stage" id="zoomStage" onclick="if(event.target===this&&(!window._zoomScaleGt1||!window._zoomScaleGt1()))window._closeViewer()"><img id="zoomImg" src="${src}" alt="${escapeHtml(item.name)}" draggable="false"></div>`;
        initZoom();
    } else if (type === 'video') {
        if (VIDEO_EXTS_PLAYABLE.includes(ext)) {
            content.innerHTML = `<video src="${API.streamUrl(item.path)}" controls autoplay></video>`;
        } else {
            content.innerHTML = `
                <div class="viewer-unsupported">
                    <div style="font-size:64px;margin-bottom:16px;opacity:0.5">${ICONS.video}</div>
                    <div style="font-size:16px;margin-bottom:8px">${t('viewer.video_unsupported_title')}</div>
                    <div style="font-size:13px;color:var(--text-secondary);margin-bottom:20px">${t('viewer.video_unsupported_desc', {ext: ext.toUpperCase()})}</div>
                    <a href="${API.downloadUrl(item.path)}" class="btn btn-primary" download>${t('context.download')}</a>
                </div>`;
        }
    } else if (type === 'audio') {
        content.innerHTML = `<div style="text-align:center"><div style="font-size:80px;margin-bottom:24px">${ICONS.audio}</div><audio src="${API.streamUrl(item.path)}" controls autoplay style="width:400px;max-width:90vw"></audio></div>`;
    }
}

function viewerKeyHandler(e) {
    if (e.key === 'Escape') closeViewer();
    if (e.key === 'ArrowLeft') viewerPrev();
    if (e.key === 'ArrowRight') viewerNext();
}

function viewerPrev() {
    if (viewerIndex > 0) { viewerIndex--; showViewerContent(); }
}
function viewerNext() {
    if (viewerIndex < viewerMediaItems.length - 1) { viewerIndex++; showViewerContent(); }
}
function closeViewer() {
    const overlay = document.getElementById('viewerOverlay');
    const content = document.getElementById('viewerContent');
    document.removeEventListener('keydown', viewerKeyHandler);
    const v = content.querySelector('video');
    if (v) v.pause();
    const a = content.querySelector('audio');
    if (a) a.pause();
    teardownZoom();
    _textViewerToken++; // cancel any in-flight text fetch from rendering

    // CRITICAL: clear heavy DOM (e.g. 47k-line <pre>) BEFORE the opacity transition
    // begins. Otherwise compositing the dimming layer over 2MB of text freezes
    // the main thread for several seconds.
    content.innerHTML = '';

    overlay.classList.remove('visible');

    // Block file-item clicks for 350ms — protects against the second of a
    // rapid double-click landing on the file underneath.
    window._suppressFileClick = true;
    setTimeout(() => { window._suppressFileClick = false; }, 350);

    setTimeout(() => overlay.classList.remove('text-mode'), 220);
}

// --- Image zoom / pan ---
let zoomState = null; // { scale, x, y, dragging, startX, startY, origX, origY }

function initZoom() {
    const stage = document.getElementById('zoomStage');
    const img = document.getElementById('zoomImg');
    if (!stage || !img) return;
    zoomState = { scale: 1, x: 0, y: 0, dragging: false, startX: 0, startY: 0, origX: 0, origY: 0 };
    applyZoom();

    stage.addEventListener('wheel', onZoomWheel, { passive: false });
    stage.addEventListener('mousedown', onZoomDown);
    stage.addEventListener('dblclick', onZoomDblClick);
    window.addEventListener('mousemove', onZoomMove);
    window.addEventListener('mouseup', onZoomUp);
}

window._zoomScaleGt1 = () => zoomState && zoomState.scale > 1.001;

function teardownZoom() {
    const stage = document.getElementById('zoomStage');
    if (stage) {
        stage.removeEventListener('wheel', onZoomWheel);
        stage.removeEventListener('mousedown', onZoomDown);
        stage.removeEventListener('dblclick', onZoomDblClick);
    }
    window.removeEventListener('mousemove', onZoomMove);
    window.removeEventListener('mouseup', onZoomUp);
    zoomState = null;
}

function applyZoom() {
    const img = document.getElementById('zoomImg');
    const stage = document.getElementById('zoomStage');
    if (!img || !zoomState) return;
    img.style.transform = `translate(${zoomState.x}px, ${zoomState.y}px) scale(${zoomState.scale})`;
    if (stage) stage.classList.toggle('zoomed', zoomState.scale > 1.001);
}

function onZoomWheel(e) {
    if (!zoomState) return;
    e.preventDefault();
    const stage = e.currentTarget;
    const rect = stage.getBoundingClientRect();
    const cx = e.clientX - rect.left - rect.width / 2;
    const cy = e.clientY - rect.top - rect.height / 2;
    const delta = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const newScale = Math.max(1, Math.min(8, zoomState.scale * delta));
    const ratio = newScale / zoomState.scale;
    // Zoom toward pointer
    zoomState.x = cx - (cx - zoomState.x) * ratio;
    zoomState.y = cy - (cy - zoomState.y) * ratio;
    zoomState.scale = newScale;
    if (newScale === 1) { zoomState.x = 0; zoomState.y = 0; }
    applyZoom();
}

function onZoomDown(e) {
    if (!zoomState || zoomState.scale <= 1.001) return;
    zoomState.dragging = true;
    zoomState.startX = e.clientX;
    zoomState.startY = e.clientY;
    zoomState.origX = zoomState.x;
    zoomState.origY = zoomState.y;
    e.preventDefault();
}

function onZoomMove(e) {
    if (!zoomState || !zoomState.dragging) return;
    zoomState.x = zoomState.origX + (e.clientX - zoomState.startX);
    zoomState.y = zoomState.origY + (e.clientY - zoomState.startY);
    applyZoom();
}

function onZoomUp() {
    if (zoomState) zoomState.dragging = false;
}

function onZoomDblClick(e) {
    if (!zoomState) return;
    if (zoomState.scale > 1.001) {
        zoomState.scale = 1; zoomState.x = 0; zoomState.y = 0;
    } else {
        const stage = e.currentTarget;
        const rect = stage.getBoundingClientRect();
        const cx = e.clientX - rect.left - rect.width / 2;
        const cy = e.clientY - rect.top - rect.height / 2;
        const targetScale = 2.5;
        zoomState.x = -cx * (targetScale - 1);
        zoomState.y = -cy * (targetScale - 1);
        zoomState.scale = targetScale;
    }
    applyZoom();
}

// --- Text viewer ---
// LRU-ish cache so re-opening the same file is instant (skip both fetch + render).
const _textCache = new Map();
const TEXT_CACHE_MAX = 5;
function _cacheText(path, data) {
    if (_textCache.has(path)) _textCache.delete(path);
    _textCache.set(path, data);
    while (_textCache.size > TEXT_CACHE_MAX) {
        _textCache.delete(_textCache.keys().next().value);
    }
}

// Bumped on every open + on close — protects late fetches from rendering
// into a viewer the user has already dismissed.
let _textViewerToken = 0;

function openTextViewer(item) {
    const overlay = document.getElementById('viewerOverlay');
    const content = document.getElementById('viewerContent');
    const myToken = ++_textViewerToken;

    overlay.classList.add('text-mode');
    document.getElementById('viewerFilename').textContent = item.name;
    content.innerHTML = `<div class="text-viewer"><div class="text-loading">${t('common.loading')}</div></div>`;
    overlay.classList.add('visible');
    document.addEventListener('keydown', viewerKeyHandler);

    if (_textCache.has(item.path)) {
        renderTextViewer(content, item, _textCache.get(item.path), myToken);
        return;
    }

    API.getText(item.path).then(data => {
        _cacheText(item.path, data);
        renderTextViewer(content, item, data, myToken);
    }).catch(e => {
        if (myToken !== _textViewerToken) return;
        content.innerHTML = `<div class="viewer-unsupported"><div style="font-size:14px;color:var(--danger)">${escapeHtml(e.message || 'Failed')}</div></div>`;
    });
}

function renderTextViewer(container, item, data, token) {
    // User closed the viewer (or opened another) before fetch resolved — bail out.
    if (token !== _textViewerToken) return;

    const lineCount = (data.content.match(/\n/g) || []).length + 1;
    const lineNumsText = Array.from({ length: lineCount }, (_, i) => i + 1).join('\n');

    container.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'text-viewer';

    const header = document.createElement('div');
    header.className = 'text-viewer-header';
    const extSpan = document.createElement('span');
    extSpan.className = 'text-ext';
    extSpan.textContent = data.ext || 'txt';
    const sizeSpan = document.createElement('span');
    sizeSpan.className = 'text-size';
    sizeSpan.textContent = `${formatSize(data.size)} · ${data.encoding} · ${lineCount} ${t('viewer.lines')}`;
    const dlBtn = document.createElement('a');
    dlBtn.className = 'btn btn-ghost btn-sm';
    dlBtn.href = API.downloadUrl(item.path);
    dlBtn.download = '';
    dlBtn.textContent = t('context.download');
    header.append(extSpan, sizeSpan, dlBtn);

    const body = document.createElement('div');
    body.className = 'text-viewer-body';
    const lineno = document.createElement('pre');
    lineno.className = 'text-lineno';
    lineno.textContent = lineNumsText;
    const code = document.createElement('pre');
    code.className = 'text-content';
    code.textContent = data.content;
    body.append(lineno, code);
    wrap.append(header, body);
    container.appendChild(wrap);
}

// --- Modals ---
function showModal(html, modalClass = '') {
    const overlay = document.getElementById('modalOverlay');
    const content = document.getElementById('modalContent');
    content.className = 'modal' + (modalClass ? ' ' + modalClass : '');
    content.innerHTML = html;
    overlay.classList.add('visible');
}

function closeModal() {
    document.getElementById('modalOverlay').classList.remove('visible');
    if (window._securityPollTimer) { clearInterval(window._securityPollTimer); window._securityPollTimer = null; }
}

// --- Clipboard helper ---
window._copyToClipboard = async (text, btn) => {
    try {
        await navigator.clipboard.writeText(text);
        if (btn) {
            const prev = btn.textContent;
            btn.classList.add('copied');
            btn.textContent = t('common.copied');
            setTimeout(() => {
                btn.classList.remove('copied');
                btn.textContent = prev;
            }, 1200);
        }
        toast(t('common.copied'), 'success');
    } catch (e) { toast(e.message, 'error'); }
};

function promptRename(item) {
    if (!item) return;
    showModal(`
        <h3>${t('modal.rename_title')}</h3>
        <input type="text" id="renameInput" value="${escapeHtml(item.name)}">
        <div class="modal-actions">
            <button class="btn btn-ghost" onclick="window._closeModal()">${t('common.cancel')}</button>
            <button class="btn btn-primary" onclick="window._doRename()">${t('common.confirm')}</button>
        </div>`);
    const inp = document.getElementById('renameInput');
    inp.focus();
    const dotIdx = item.name.lastIndexOf('.');
    inp.setSelectionRange(0, dotIdx > 0 ? dotIdx : item.name.length);
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') window._doRename(); });
    window._renameTarget = item;
}

window._doRename = async () => {
    const newName = document.getElementById('renameInput').value.trim();
    if (!newName) return;
    try {
        await API.rename(window._renameTarget.path, newName);
        closeModal();
        toast(t('modal.renamed'));
        navigateTo(state.currentPath);
    } catch (e) { toast(e.message, 'error'); }
};

function confirmDelete(paths) {
    const count = paths.length;
    showModal(`
        <h3>${t('modal.delete_title', {n: count})}</h3>
        <p style="color:var(--text-secondary);font-size:14px;margin-bottom:8px">${t('modal.delete_warning')}</p>
        <div class="modal-actions">
            <button class="btn btn-ghost" onclick="window._closeModal()">${t('common.cancel')}</button>
            <button class="btn btn-danger" onclick="window._doDelete()">${t('common.delete')}</button>
        </div>`);
    window._deletePaths = paths;
}

window._doDelete = async () => {
    try {
        const res = await API.deleteFiles(window._deletePaths);
        closeModal();
        const failed = (res && res.failed) || [];
        if (failed.length) {
            toast(t('modal.deleted_partial', {ok: (res.deleted || []).length, fail: failed.length}), 'error');
        } else {
            toast(t('modal.deleted'));
        }
        navigateTo(state.currentPath);
    } catch (e) { toast(e.message, 'error'); }
};

function openMoveDialog(paths) {
    showModal(`
        <h3>${t('modal.move_title')}</h3>
        <input type="text" id="moveDestInput" placeholder="${t('modal.move_placeholder')}" value="${escapeHtml(state.currentPath)}">
        <div class="modal-actions">
            <button class="btn btn-ghost" onclick="window._closeModal()">${t('common.cancel')}</button>
            <button class="btn btn-primary" onclick="window._doMove()">${t('toolbar.move')}</button>
        </div>`);
    document.getElementById('moveDestInput').focus();
    window._movePaths = paths;
}

window._doMove = async () => {
    const dest = document.getElementById('moveDestInput').value.trim();
    if (!dest) return;
    try {
        const res = await API.move(window._movePaths, dest);
        closeModal();
        const failed = (res && res.failed) || [];
        if (failed.length) {
            toast(t('modal.moved_partial', {ok: (res.moved || []).length, fail: failed.length}), 'error');
        } else {
            toast(t('modal.moved'));
        }
        navigateTo(state.currentPath);
    } catch (e) { toast(e.message, 'error'); }
};

window._closeModal = closeModal;
window._closeViewer = closeViewer;
window._viewerPrev = viewerPrev;
window._viewerNext = viewerNext;

// --- Help Page ---
window._openHelp = () => {
    const sections = [
        ['browse', '📂'], ['select', '✅'], ['upload', '⬆️'], ['download', '⬇️'],
        ['media', '🎬'], ['text', '📄'], ['trash', '🗑'], ['share', '🔗'],
        ['context', '📝'], ['webdav', '🖥'],
    ].map(([key, icon]) => `
        <div style="margin-bottom:20px">
            <div style="font-weight:600;color:var(--accent);margin-bottom:6px">${icon} ${t('help.'+key+'_title')}</div>
            <div style="color:var(--text-secondary)">${t('help.'+key+'_desc')}</div>
        </div>`).join('');

    const shortcutKeys = [
        'shortcut_arrows', 'shortcut_enter', 'shortcut_backspace',
        'shortcut_space', 'shortcut_home_end',
        'shortcut_select_all', 'shortcut_delete', 'shortcut_escape',
    ];
    const shortcuts = shortcutKeys
        .map(k => `<li style="padding:4px 0">${t('help.' + k)}</li>`).join('');

    // Role-specific permissions section
    const role = state.user?.role || 'user';
    const roleSection = `
        <div style="margin-bottom:20px;padding:16px;background:var(--accent-dim);border-radius:8px;border:1px solid var(--border)">
            <div style="font-weight:600;color:var(--accent);margin-bottom:6px">🔑 ${t('help.role_title')}</div>
            <div style="color:var(--text-secondary)">${t('help.role_' + role + '_desc')}</div>
        </div>`;

    showModal(`
        <h3 style="margin-bottom:20px">${t('help.title')}</h3>
        <div style="max-height:65vh;overflow-y:auto;font-size:14px;line-height:1.8;color:var(--text-primary)">
        ${roleSection}
        ${sections}
        <div style="margin-bottom:8px">
            <div style="font-weight:600;color:var(--accent);margin-bottom:6px">⌨️ ${t('help.shortcuts_title')}</div>
            <ul style="color:var(--text-secondary);font-size:13px;list-style:none;padding-left:0">${shortcuts}</ul>
        </div>
        </div>
        <div class="modal-actions" style="margin-top:16px">
            <button class="btn btn-primary" onclick="window._closeModal()">${t('common.ok')}</button>
        </div>`);
};

// --- Trash ---
window._openTrash = async () => {
    showModal(`
        <h3 style="margin-bottom:12px">${t('trash.title')}</h3>
        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:12px">${t('trash.retention_hint')}</div>
        <div id="trashContent" style="color:var(--text-secondary);padding:20px;text-align:center">${t('common.loading')}</div>
        <div class="modal-actions" style="margin-top:16px">
            <button class="btn btn-danger" id="trashEmptyBtn" onclick="window._emptyTrash()" style="display:none">${t('trash.empty_all')}</button>
            <button class="btn btn-ghost" onclick="window._closeModal()">${t('common.close')}</button>
        </div>`, 'modal-trash');
    await _loadTrash();
};

async function _loadTrash() {
    const container = document.getElementById('trashContent');
    try {
        const data = await API.listTrash();
        const emptyBtn = document.getElementById('trashEmptyBtn');
        if (!data.entries || data.entries.length === 0) {
            container.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text-secondary)">${t('trash.empty_state')}</div>`;
            if (emptyBtn) emptyBtn.style.display = 'none';
            return;
        }
        if (emptyBtn) emptyBtn.style.display = '';
        let html = `<div class="trash-list">`;
        for (const e of data.entries) {
            const icon = e.is_dir ? ICONS.folder : ICONS.file;
            const deleted = formatDate(e.deleted_at);
            const daysCls = e.days_left <= 3 ? ' soon' : '';
            html += `<div class="trash-row" data-id="${escapeHtml(e.id)}">
                <div class="trash-icon">${icon}</div>
                <div class="trash-meta">
                    <div class="trash-name">${escapeHtml(e.orig_name)}</div>
                    <div class="trash-sub">
                        <span class="trash-path">${escapeHtml(e.orig_path)}</span>
                        <span class="trash-size">${e.is_dir ? '' : formatSize(e.size)}</span>
                        <span class="trash-deleted">${deleted}</span>
                        <span class="trash-days${daysCls}">${t('trash.days_left', {n: e.days_left})}</span>
                    </div>
                </div>
                <div class="trash-actions">
                    <button class="btn btn-ghost btn-sm" onclick="window._restoreOne('${escapeHtml(e.id)}')">${t('trash.restore')}</button>
                    <button class="btn btn-danger btn-sm" onclick="window._purgeOne('${escapeHtml(e.id)}')">${t('trash.delete_forever')}</button>
                </div>
            </div>`;
        }
        html += `</div>`;
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<div style="color:var(--danger)">${escapeHtml(err.message)}</div>`;
    }
}

window._restoreOne = async (id) => {
    try {
        const res = await API.restoreTrash([id]);
        if (res.restored.length > 0) {
            toast(t('trash.restored'), 'success');
            await _loadTrash();
            // Refresh current folder view
            navigateTo(state.currentPath);
        } else {
            toast(t('trash.restore_failed'), 'error');
        }
    } catch (e) { toast(e.message, 'error'); }
};

window._purgeOne = async (id) => {
    if (!confirm(t('trash.confirm_purge'))) return;
    try {
        await API.purgeTrash([id]);
        toast(t('trash.purged'), 'success');
        await _loadTrash();
        loadStats();
    } catch (e) { toast(e.message, 'error'); }
};

window._emptyTrash = async () => {
    if (!confirm(t('trash.confirm_empty'))) return;
    try {
        const res = await API.emptyTrash();
        toast(t('trash.emptied', {n: res.purged_count}), 'success');
        await _loadTrash();
        loadStats();
    } catch (e) { toast(e.message, 'error'); }
};

// --- Shares Management Page ---
window._openShares = async () => {
    showModal(`
        <h3 style="margin-bottom:16px">${t('share.management_title')}</h3>
        <div id="sharesListContent" style="color:var(--text-secondary);padding:20px;text-align:center">${t('common.loading')}</div>
        <div class="modal-actions" style="margin-top:16px">
            <button class="btn btn-ghost" onclick="window._closeModal()">${t('common.close')}</button>
        </div>`);
    await _loadSharesList();
};

async function _loadSharesList() {
    const container = document.getElementById('sharesListContent');
    try {
        const shares = await API.listShares();
        if (shares.length === 0) {
            container.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text-secondary)">${t('share.no_shares')}<br><span style="font-size:12px">${t('share.no_shares_hint')}</span></div>`;
            return;
        }
        const now = new Date();
        let html = '<div style="max-height:55vh;overflow-y:auto">';
        for (const s of shares) {
            const name = s.path.split('/').pop() || s.path;
            const url = window.location.origin + '/s/' + s.token;
            const created = formatDate(s.created_at);
            const isDir = s.is_directory;
            const hasPw = !!s.password_hash;
            const expired = s.expires_at && new Date(s.expires_at + 'Z') < now;
            const expiryText = s.expires_at
                ? (expired ? `<span style="color:var(--danger)">${t('share.expired')}</span>` : t('share.expiry') + formatDate(s.expires_at))
                : t('share.permanent');

            html += `<div style="padding:12px 0;border-bottom:1px solid var(--border)">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                    <span style="font-size:15px">${isDir ? '&#128193;' : '&#128196;'}</span>
                    <span style="font-weight:500;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(s.path)}">${escapeHtml(name)}</span>
                    ${hasPw ? `<span style="font-size:11px;background:var(--bg-tertiary);padding:2px 8px;border-radius:4px;color:var(--text-secondary)">${t('share.password_tag')}</span>` : ''}
                    ${expired ? `<span style="font-size:11px;background:var(--danger-dim);padding:2px 8px;border-radius:4px;color:var(--danger)">${t('share.expired')}</span>` : ''}
                    <button class="btn-icon" onclick="window._deleteShare('${s.id}')" title="${t('common.delete')}" style="flex-shrink:0">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
                    <input type="text" value="${escapeHtml(url)}" readonly style="flex:1;padding:6px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--accent);font-family:monospace;font-size:12px;outline:none">
                    <button class="btn btn-ghost" style="padding:6px 10px;font-size:12px" onclick="navigator.clipboard.writeText('${escapeHtml(url)}');window._toast('${t('common.copied')}')">${t('share.copy')}</button>
                </div>
                <div style="font-size:12px;color:var(--text-secondary);display:flex;gap:12px;flex-wrap:wrap">
                    <span>${t('share.created')}${created}</span>
                    <span>${expiryText}</span>
                    <span>${t('share.downloads', {n: s.download_count})}</span>
                </div>
            </div>`;
        }
        html += '</div>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="color:var(--danger)">${t('common.error_load')}</div>`;
    }
}

window._deleteShare = async (id) => {
    try {
        await API.deleteShare(id);
        toast(t('share.deleted'));
        await _loadSharesList();
    } catch (e) { toast(e.message, 'error'); }
};

// --- Share Dialog ---
function openShareDialog(item) {
    showModal(`
        <h3>${t('share.title', {name: escapeHtml(item.name)})}</h3>
        <div class="toggle-row">
            <span>${t('share.password_toggle')}</span>
            <div class="toggle" id="sharePwToggle" onclick="this.classList.toggle('on');document.getElementById('sharePwInput').style.display=this.classList.contains('on')?'block':'none'"></div>
        </div>
        <input type="password" id="sharePwInput" placeholder="${t('share.password_placeholder')}" style="display:none">
        <div class="toggle-row">
            <span>${t('share.expiry_toggle')}</span>
            <div class="toggle" id="shareExpToggle" onclick="this.classList.toggle('on');document.getElementById('shareExpInput').style.display=this.classList.contains('on')?'block':'none'"></div>
        </div>
        <input type="text" id="shareExpInput" placeholder="${t('share.expiry_placeholder')}" style="display:none">
        <div class="share-link-box" id="shareLinkBox" style="display:none">
            <input type="text" id="shareLinkInput" readonly>
            <button class="btn btn-primary" onclick="navigator.clipboard.writeText(document.getElementById('shareLinkInput').value);window._toast('${t('common.copied')}')">${t('share.copy')}</button>
        </div>
        <div class="modal-actions">
            <button class="btn btn-ghost" onclick="window._closeModal()">${t('common.close')}</button>
            <button class="btn btn-primary" id="shareCreateBtn" onclick="window._doCreateShare()">${t('share.create_link')}</button>
        </div>`);
    window._shareItem = item;
}

window._toast = toast;

window._doCreateShare = async () => {
    const item = window._shareItem;
    const usePw = document.getElementById('sharePwToggle').classList.contains('on');
    const useExp = document.getElementById('shareExpToggle').classList.contains('on');
    const pw = usePw ? document.getElementById('sharePwInput').value : null;
    let expAt = null;
    if (useExp) {
        const hours = parseFloat(document.getElementById('shareExpInput').value);
        if (hours > 0) {
            expAt = new Date(Date.now() + hours * 3600000).toISOString();
        }
    }
    try {
        const result = await API.createShare(item.path, pw, expAt);
        const url = window.location.origin + result.url;
        document.getElementById('shareLinkBox').style.display = 'flex';
        document.getElementById('shareLinkInput').value = url;
        document.getElementById('shareCreateBtn').style.display = 'none';
        toast(t('share.link_created'), 'success');
    } catch (e) { toast(e.message, 'error'); }
};

// --- Upload ---
const CHUNK_THRESHOLD = 20 * 1024 * 1024;
const UPLOAD_SETTINGS_KEY = 'nk_upload_settings';
const DEFAULT_UPLOAD_SETTINGS = { chunkMb: 20, concurrency: 3 };

function getUploadSettings() {
    try {
        const raw = JSON.parse(localStorage.getItem(UPLOAD_SETTINGS_KEY) || '{}');
        return {
            chunkMb: [5, 10, 20, 50, 100].includes(raw.chunkMb) ? raw.chunkMb : DEFAULT_UPLOAD_SETTINGS.chunkMb,
            concurrency: [1, 2, 3, 4, 6].includes(raw.concurrency) ? raw.concurrency : DEFAULT_UPLOAD_SETTINGS.concurrency,
        };
    } catch { return { ...DEFAULT_UPLOAD_SETTINGS }; }
}

function saveUploadSettings(s) {
    localStorage.setItem(UPLOAD_SETTINGS_KEY, JSON.stringify(s));
}

window._openUploadSettings = () => {
    const s = getUploadSettings();
    const chunkOpts = [5, 10, 20, 50, 100].map(v =>
        `<option value="${v}" ${v === s.chunkMb ? 'selected' : ''}>${v} MB</option>`).join('');
    const concOpts = [1, 2, 3, 4, 6].map(v =>
        `<option value="${v}" ${v === s.concurrency ? 'selected' : ''}>${v}</option>`).join('');
    showModal(`
        <h3>${t('settings.upload_title')}</h3>
        <div class="settings-row">
            <div>
                <label>${t('settings.chunk_size')}</label>
                <div class="hint">${t('settings.chunk_size_hint')}</div>
            </div>
            <select id="setChunk">${chunkOpts}</select>
        </div>
        <div class="settings-row">
            <div>
                <label>${t('settings.concurrency')}</label>
                <div class="hint">${t('settings.concurrency_hint')}</div>
            </div>
            <select id="setConc">${concOpts}</select>
        </div>
        <div class="modal-actions">
            <button class="btn btn-ghost" onclick="window._closeModal()">${t('common.cancel')}</button>
            <button class="btn btn-primary" onclick="window._saveUploadSettings()">${t('common.save')}</button>
        </div>`);
};

window._saveUploadSettings = () => {
    const chunkMb = parseInt(document.getElementById('setChunk').value, 10);
    const concurrency = parseInt(document.getElementById('setConc').value, 10);
    saveUploadSettings({ chunkMb, concurrency });
    toast(t('settings.saved'), 'success');
    closeModal();
};

function initUpload() {
    const body = document.body;
    const overlay = document.getElementById('dropOverlay');
    let dragCounter = 0;

    body.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCounter++;
        overlay.classList.add('visible');
    });
    body.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dragCounter--;
        if (dragCounter <= 0) { overlay.classList.remove('visible'); dragCounter = 0; }
    });
    body.addEventListener('dragover', (e) => e.preventDefault());
    body.addEventListener('drop', async (e) => {
        e.preventDefault();
        dragCounter = 0;
        overlay.classList.remove('visible');
        const items = e.dataTransfer.items;
        // Use webkitGetAsEntry to detect folders; fall back to plain files
        if (items && items.length && typeof items[0].webkitGetAsEntry === 'function') {
            const expanded = await expandDropEntries(items);
            if (expanded.length > 0) uploadFiles(expanded);
        } else if (e.dataTransfer.files.length > 0) {
            uploadFiles(e.dataTransfer.files);
        }
    });

    document.getElementById('uploadBtn').addEventListener('click', () => {
        const input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.addEventListener('change', () => {
            if (input.files.length > 0) uploadFiles(input.files);
        });
        input.click();
    });
}

// Expand DataTransferItems (dropped folders) into a flat file list with relativePath.
async function expandDropEntries(items) {
    const out = [];
    const entries = [];
    for (let i = 0; i < items.length; i++) {
        const entry = items[i].webkitGetAsEntry && items[i].webkitGetAsEntry();
        if (entry) entries.push(entry);
    }
    await Promise.all(entries.map(e => walkEntry(e, '', out)));
    return out;
}

function walkEntry(entry, prefix, out) {
    return new Promise((resolve) => {
        if (entry.isFile) {
            entry.file(f => {
                // File is immutable; wrap to attach relativePath
                out.push(Object.assign(f, { _relPath: prefix + entry.name }));
                resolve();
            }, () => resolve());
        } else if (entry.isDirectory) {
            const reader = entry.createReader();
            const all = [];
            const readBatch = () => {
                reader.readEntries(chunk => {
                    if (chunk.length === 0) {
                        Promise.all(all.map(c => walkEntry(c, prefix + entry.name + '/', out))).then(resolve);
                    } else {
                        all.push(...chunk);
                        readBatch();
                    }
                }, () => resolve());
            };
            readBatch();
        } else {
            resolve();
        }
    });
}

// Compute target directory for a file, mkdir-ing ancestor dirs as needed.
const _ensuredDirs = new Set();
async function ensureDirPath(dirPath) {
    if (!dirPath || dirPath === '/' || _ensuredDirs.has(dirPath)) return;
    // Ensure parents first
    const parent = dirPath.replace(/\/[^/]+$/, '') || '/';
    await ensureDirPath(parent);
    try {
        await API.mkdir(dirPath);
    } catch (e) {
        // 409 Already exists is fine
        if (!/already exists|409/i.test(e.message || '')) throw e;
    }
    _ensuredDirs.add(dirPath);
}

async function uploadFiles(fileList) {
    const panel = document.getElementById('uploadPanel');
    const list = document.getElementById('uploadList');
    panel.classList.add('visible');
    _ensuredDirs.clear();

    for (const file of fileList) {
        const relPath = file._relPath || file.name;
        const relDir = relPath.includes('/') ? relPath.replace(/\/[^/]+$/, '') : '';
        const targetDir = relDir
            ? (state.currentPath.replace(/\/$/, '') + '/' + relDir)
            : state.currentPath;

        const itemEl = document.createElement('div');
        itemEl.className = 'upload-item';
        itemEl.innerHTML = `
            <div class="upload-name">${escapeHtml(relPath)}</div>
            <div class="upload-bar"><div class="upload-fill" style="width:0%"></div></div>
            <div class="upload-status">${t('files.upload_waiting')}</div>`;
        list.appendChild(itemEl);
        const fill = itemEl.querySelector('.upload-fill');
        const status = itemEl.querySelector('.upload-status');

        try {
            if (relDir) await ensureDirPath(targetDir);
            if (file.size > CHUNK_THRESHOLD) {
                await uploadChunked(file, fill, status, targetDir);
            } else {
                await uploadSimple(file, fill, status, targetDir);
            }
            itemEl.classList.add('done');
            status.textContent = t('files.upload_done');
        } catch (e) {
            itemEl.classList.add('error');
            status.textContent = t('files.upload_fail') + e.message;
        }
    }

    navigateTo(state.currentPath);
    setTimeout(() => {
        panel.classList.remove('visible');
        list.innerHTML = '';
    }, 2000);
}

async function uploadSimple(file, fillEl, statusEl, targetDir) {
    return new Promise((resolve, reject) => {
        const fd = new FormData();
        fd.append('path', targetDir || state.currentPath);
        fd.append('files', file);

        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/files/upload');
        xhr.setRequestHeader('X-CSRF-Token', API._getCsrfToken());
        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
                const pct = Math.round(e.loaded / e.total * 100);
                fillEl.style.width = pct + '%';
                statusEl.textContent = `${formatSize(e.loaded)} / ${formatSize(e.total)}`;
            }
        });
        xhr.addEventListener('load', () => {
            if (xhr.status >= 200 && xhr.status < 300) resolve();
            else reject(new Error(`HTTP ${xhr.status}`));
        });
        xhr.addEventListener('error', () => reject(new Error(t('common.error_network'))));
        xhr.send(fd);
    });
}

async function uploadChunked(file, fillEl, statusEl, targetDir) {
    const settings = getUploadSettings();
    const chunkSize = settings.chunkMb * 1024 * 1024;
    const concurrency = settings.concurrency;
    const totalChunks = Math.ceil(file.size / chunkSize);

    // /upload/init reserves quota and hands back a server-tracked session id.
    // Previously the client picked a random uuid and started streaming chunks
    // directly, which let any caller fill disk without ever completing.
    const initFd = new FormData();
    initFd.append('path', targetDir || state.currentPath);
    initFd.append('filename', file.name);
    initFd.append('total_bytes', file.size);
    initFd.append('total_chunks', totalChunks);
    const initRes = await fetch('/api/files/upload/init', {
        method: 'POST',
        body: initFd,
        headers: { 'X-CSRF-Token': API._getCsrfToken() },
    });
    if (!initRes.ok) {
        let detail = '';
        try { detail = (await initRes.json()).detail || ''; } catch {}
        throw new Error(detail || t('files.upload_init_fail') || `HTTP ${initRes.status}`);
    }
    const { upload_id: uploadId } = await initRes.json();

    let uploadedBytes = 0;
    let nextIndex = 0;
    let firstError = null;
    let completed = 0;

    const uploadOne = async (i) => {
        const start = i * chunkSize;
        const end = Math.min(start + chunkSize, file.size);
        const blob = file.slice(start, end);
        const fd = new FormData();
        fd.append('upload_id', uploadId);
        fd.append('chunk_index', i);
        fd.append('total_chunks', totalChunks);
        fd.append('filename', file.name);
        fd.append('file', blob);
        const res = await fetch('/api/files/upload/chunk', {
            method: 'POST',
            body: fd,
            headers: { 'X-CSRF-Token': API._getCsrfToken() },
        });
        if (!res.ok) throw new Error(t('files.upload_chunk_fail', { i }));
        uploadedBytes += (end - start);
        completed++;
        const pct = Math.round(uploadedBytes / file.size * 100);
        fillEl.style.width = pct + '%';
        statusEl.textContent = `${formatSize(uploadedBytes)} / ${formatSize(file.size)} (${t('files.upload_chunk', { i: completed, total: totalChunks })})`;
    };

    const worker = async () => {
        while (true) {
            if (firstError) return;
            const i = nextIndex++;
            if (i >= totalChunks) return;
            try { await uploadOne(i); }
            catch (e) { if (!firstError) firstError = e; return; }
        }
    };

    const workerCount = Math.max(1, Math.min(concurrency, totalChunks));
    await Promise.all(Array.from({ length: workerCount }, worker));
    if (firstError) throw firstError;

    const fd = new FormData();
    fd.append('upload_id', uploadId);
    fd.append('total_chunks', totalChunks);
    fd.append('filename', file.name);
    fd.append('path', targetDir || state.currentPath);
    const res = await fetch('/api/files/upload/complete', {
        method: 'POST',
        body: fd,
        headers: { 'X-CSRF-Token': API._getCsrfToken() },
    });
    if (!res.ok) throw new Error(t('files.upload_complete_fail'));
}

// --- Search ---
let searchTimeout = null;
function initSearch() {
    const input = document.getElementById('searchInput');
    const results = document.getElementById('searchResults');

    input.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        const q = input.value.trim();
        if (q.length < 2) { results.classList.remove('visible'); return; }
        searchTimeout = setTimeout(async () => {
            try {
                const data = await API.search(q);
                if (data.results.length === 0) {
                    results.innerHTML = `<div class="search-result-item" style="color:var(--text-secondary)">${t('files.search_no_result')}</div>`;
                } else {
                    results.innerHTML = data.results.map(r => `
                        <div class="search-result-item" data-path="${escapeHtml(r.path)}" data-dir="${r.is_dir}">
                            <div>${r.is_dir ? '&#128193;' : ''} ${escapeHtml(r.name)}<br><span class="sr-path">${escapeHtml(r.path)}</span></div>
                        </div>`).join('');
                    results.querySelectorAll('.search-result-item').forEach(el => {
                        el.addEventListener('click', () => {
                            results.classList.remove('visible');
                            input.value = '';
                            if (el.dataset.dir === 'true') navigateTo(el.dataset.path);
                            else {
                                const dir = el.dataset.path.substring(0, el.dataset.path.lastIndexOf('/')) || '/';
                                navigateTo(dir);
                            }
                        });
                    });
                }
                results.classList.add('visible');
            } catch { results.classList.remove('visible'); }
        }, 300);
    });

    input.addEventListener('blur', () => setTimeout(() => results.classList.remove('visible'), 200));
    input.addEventListener('keydown', (e) => { if (e.key === 'Escape') { input.value = ''; results.classList.remove('visible'); } });
}

async function loadStats() {
    try {
        const stats = await API.getStats();
        state.stats = stats;
        const el = document.getElementById('diskInfo');
        if (stats.total) {
            el.textContent = t('files.stats', {used: formatSize(stats.used), total: formatSize(stats.total)});
        } else {
            el.textContent = t('files.stats_used', {used: formatSize(stats.used)});
        }
    } catch { /* ignore */ }
}

window._newFolder = () => {
    showModal(`
        <h3>${t('modal.new_folder_title')}</h3>
        <input type="text" id="newFolderInput" placeholder="${t('modal.new_folder_placeholder')}">
        <div class="modal-actions">
            <button class="btn btn-ghost" onclick="window._closeModal()">${t('common.cancel')}</button>
            <button class="btn btn-primary" onclick="window._doNewFolder()">${t('modal.new_folder_create')}</button>
        </div>`);
    const inp = document.getElementById('newFolderInput');
    inp.focus();
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') window._doNewFolder(); });
};

window._doNewFolder = async () => {
    const name = document.getElementById('newFolderInput').value.trim();
    if (!name) return;
    const fullPath = state.currentPath === '/' ? `/${name}` : `${state.currentPath}/${name}`;
    try {
        await API.mkdir(fullPath);
        closeModal();
        toast(t('modal.new_folder_created'));
        navigateTo(state.currentPath);
    } catch (e) { toast(e.message, 'error'); }
};

window._toolbarDownload = () => {
    const paths = [...state.selected];
    if (paths.length === 0) return;
    if (paths.length === 1) {
        const item = state.items.find(i => i.path === paths[0]);
        if (item?.is_dir) window.open(API.downloadZipUrl(paths[0]), '_blank');
        else window.open(API.downloadUrl(paths[0]), '_blank');
        return;
    }
    // Multi-select: one streaming zip beats N popups (browser blocks them).
    window.open(API.downloadBatchUrl(paths), '_blank');
};

window._toolbarMove = () => openMoveDialog([...state.selected]);
window._toolbarDelete = () => confirmDelete([...state.selected]);

window._selectAll = () => {
    if (state.selected.size === state.items.length) {
        state.selected.clear();
    } else {
        state.items.forEach(i => state.selected.add(i.path));
    }
    renderFiles();
};

window._setView = (mode) => {
    state.viewMode = mode;
    localStorage.setItem('nkcloud_view', mode);
    document.getElementById('listViewBtn').classList.toggle('active', mode === 'list');
    document.getElementById('gridViewBtn').classList.toggle('active', mode === 'grid');
    renderFiles();
};

window._setSort = (by) => {
    if (state.sortBy === by) state.sortAsc = !state.sortAsc;
    else { state.sortBy = by; state.sortAsc = true; }
    localStorage.setItem('nkcloud_sort', by);
    renderFiles();
};


// --- User Context ---
async function loadUserContext() {
    try {
        const me = await API.getMe();
        state.user = me;
        // Show user badge
        const badge = document.getElementById('userBadge');
        const roleLabels = { owner: t('admin.role_owner'), admin: t('admin.role_admin'), user: t('admin.role_user') };
        badge.innerHTML = `${escapeHtml(me.username)} <span class="role-tag">${roleLabels[me.role] || me.role}</span>`;
        // Show admin button for owner/admin
        if (me.role === 'owner' || me.role === 'admin') {
            document.getElementById('adminBtn').style.display = '';
        }
        updatePermissionUI();
    } catch { /* ignore */ }
}

function updatePermissionUI() {
    // Hide upload/new folder buttons if user can't write to current path
    // This is a UI hint; the server enforces actual permissions
    // For regular users this will be called after navigateTo
}

// --- Admin Panel ---
window._openAdmin = async () => {
    if (!state.user || (state.user.role !== 'owner' && state.user.role !== 'admin')) return;
    const isOwner = state.user.role === 'owner';
    let tabsHtml = `
        <button class="admin-tab active" onclick="window._adminTab('invites')">${t('admin.tab_invites')}</button>`;
    if (isOwner) {
        tabsHtml += `
        <button class="admin-tab" onclick="window._adminTab('users')">${t('admin.tab_users')}</button>
        <button class="admin-tab" onclick="window._adminTab('audit')">${t('admin.tab_audit')}</button>
        <button class="admin-tab" onclick="window._adminTab('security')">${t('admin.tab_security')}</button>`;
    }
    const u = state.user || {};
    const roleLabels = { owner: t('admin.role_owner'), admin: t('admin.role_admin'), user: t('admin.role_user') };
    const lastLogin = u.last_login_at ? new Date(u.last_login_at * 1000).toLocaleString() : '-';
    const sessionBar = `
        <div class="admin-session-bar">
            <span>${t('admin.session_user')}</span><span class="val">${escapeHtml(u.username || '')}</span>
            <span class="role-tag">${roleLabels[u.role] || u.role || ''}</span>
            <span class="sep">·</span>
            <span>${t('admin.session_ip')}</span><span class="val">${escapeHtml(u.current_ip || '-')}</span>
            <span class="sep">·</span>
            <span>${t('admin.session_last_login')}</span><span class="val">${escapeHtml(lastLogin)}</span>
        </div>`;
    showModal(`
        <h3>${t('admin.title')}</h3>
        ${sessionBar}
        <div class="admin-tabs" id="adminTabs">${tabsHtml}</div>
        <div id="adminContent" class="admin-body">${t('common.loading')}</div>
        <div class="modal-actions" style="margin-top:12px;flex-shrink:0">
            <button class="btn btn-ghost" onclick="window._closeModal()">${t('common.close')}</button>
        </div>`, 'modal-admin');
    window._adminTab('invites');
};

window._adminTab = async (tab) => {
    // Stop any prior security polling when switching tabs
    if (window._securityPollTimer) { clearInterval(window._securityPollTimer); window._securityPollTimer = null; }
    // Update tab active state
    document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.admin-tab[onclick*="${tab}"]`)?.classList.add('active');
    const content = document.getElementById('adminContent');
    if (!content) return;

    // Fade out, render, fade in
    content.classList.add('fading');
    await new Promise(r => setTimeout(r, 140));
    content.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-secondary)">${t('common.loading')}</div>`;

    if (tab === 'invites') await _renderInvitesTab(content);
    else if (tab === 'users') await _renderUsersTab(content);
    else if (tab === 'audit') await _renderAuditTab(content);
    else if (tab === 'security') await _renderSecurityTab(content);

    // Fade back in
    requestAnimationFrame(() => content.classList.remove('fading'));
};

async function _renderInvitesTab(container) {
    try {
        const invites = await API.listInvites();
        let html = `<div style="margin-bottom:12px">
            <button class="btn btn-primary" onclick="window._createInvite()" style="font-size:13px;padding:8px 16px">${t('admin.create_invite')}</button>
        </div>`;
        if (invites.length === 0) {
            html += `<div style="padding:16px;text-align:center;color:var(--text-secondary)">${t('admin.no_invites')}</div>`;
        } else {
            html += `<table class="admin-table"><thead><tr><th>${t('admin.invite_link')}</th><th></th><th>${t('admin.invite_creator')}</th><th>${t('admin.invite_status')}</th><th></th></tr></thead><tbody>`;
            for (const inv of invites) {
                const url = window.location.origin + '/invite/' + inv.token;
                const isUsed = !!inv.used_at;
                const isExpired = inv.expires_at && new Date(inv.expires_at + 'Z') < new Date();
                let statusHtml = `<span style="color:var(--success)">${t('admin.invite_valid')}</span>`;
                if (isUsed) statusHtml = `<span style="color:var(--text-secondary)">${t('admin.invite_used')}</span>`;
                else if (isExpired) statusHtml = `<span style="color:var(--danger)">${t('admin.invite_expired')}</span>`;
                const safeUrl = escapeHtml(url).replace(/'/g, '&#39;');
                html += `<tr>
                    <td><input type="text" value="${escapeHtml(url)}" readonly style="width:200px;padding:4px 8px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:4px;color:var(--accent);font-family:monospace;font-size:11px;outline:none" onclick="this.select()"></td>
                    <td><button class="copy-btn" onclick="window._copyToClipboard('${safeUrl}', this)">${t('share.copy')}</button></td>
                    <td>${escapeHtml(inv.created_by_name || '-')}</td>
                    <td>${statusHtml}</td>
                    <td>${!isUsed ? `<button class="btn-icon" onclick="window._deleteInvite('${inv.id}')" title="${t('admin.invite_revoke')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>` : ''}</td>
                </tr>`;
            }
            html += '</tbody></table>';
        }
        container.innerHTML = html;
    } catch (e) { container.innerHTML = `<div style="color:var(--danger)">${t('common.error_load')}: ${escapeHtml(e.message)}</div>`; }
}

async function _renderUsersTab(container) {
    try {
        const users = await API.listUsers();
        let html = `<table class="admin-table"><thead><tr><th>${t('admin.user_name')}</th><th>${t('admin.user_role')}</th><th>${t('admin.user_usage')}</th><th>${t('admin.user_quota')}</th><th>${t('admin.user_status')}</th><th></th></tr></thead><tbody>`;
        for (const u of users) {
            const isOwner = u.role === 'owner';
            const isSelf = u.id === state.user.id;
            const roleOptions = isOwner ? `<span style="color:var(--accent)">${t('admin.role_owner')}</span>`
                : `<select class="role-select" onchange="window._changeRole('${u.id}',this.value)" ${isSelf ? 'disabled' : ''}>
                    <option value="user" ${u.role === 'user' ? 'selected' : ''}>${t('admin.role_user')}</option>
                    <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>${t('admin.role_admin')}</option>
                </select>`;
            const quotaDisplay = u.quota_bytes > 0 ? formatSize(u.quota_bytes) : t('admin.user_unlimited');
            const usedDisplay = formatSize(u.used_bytes);
            const statusHtml = u.is_disabled
                ? `<span class="status-dot disabled"></span>${t('admin.user_disabled')}`
                : `<span class="status-dot active"></span>${t('admin.user_active')}`;
            let actions = '';
            if (!isOwner && !isSelf) {
                actions += `<button class="btn-icon" onclick="window._toggleUser('${u.id}',${u.is_disabled ? 0 : 1})" title="${u.is_disabled ? t('admin.user_active') : t('admin.user_disabled')}" style="margin-right:4px">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${u.is_disabled ? 'var(--success)' : 'var(--text-secondary)'}" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>
                </button>`;
                actions += `<button class="btn-icon" onclick="window._promptDeleteUser('${u.id}','${escapeHtml(u.username)}')" title="${t('common.delete')}">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>`;
            }
            // Quota edit for non-owner users
            let quotaHtml = quotaDisplay;
            if (!isOwner && !isSelf) {
                const qgb = u.quota_bytes > 0 ? (u.quota_bytes / (1024*1024*1024)).toFixed(1) : '';
                quotaHtml = `<input class="quota-input" type="text" value="${qgb}" placeholder="GB" onchange="window._changeQuota('${u.id}',this.value)">
                <span style="font-size:11px;color:var(--text-secondary)">GB</span>`;
            }
            html += `<tr>
                <td><strong>${escapeHtml(u.username)}</strong></td>
                <td>${roleOptions}</td>
                <td>${usedDisplay}</td>
                <td>${quotaHtml}</td>
                <td>${statusHtml}</td>
                <td>${actions}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (e) { container.innerHTML = `<div style="color:var(--danger)">${t('common.error_load')}: ${escapeHtml(e.message)}</div>`; }
}

async function _renderAuditTab(container) {
    try {
        const data = await API.getAuditLog({ limit: 50 });
        if (data.entries.length === 0) {
            container.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-secondary)">${t('admin.audit_empty')}</div>`;
            return;
        }
        let html = `<table class="admin-table"><thead><tr><th>${t('admin.audit_time')}</th><th>${t('admin.audit_user')}</th><th>${t('admin.audit_action')}</th><th>${t('admin.audit_path')}</th></tr></thead><tbody>`;
        const actionLabels = {
            login: t('admin.action_login'), logout: t('admin.action_logout'), setup: t('admin.action_setup'), register: t('admin.action_register'),
            upload: t('admin.action_upload'), delete: t('admin.action_delete'), move: t('admin.action_move'), rename: t('admin.action_rename'), mkdir: t('admin.action_mkdir'),
            share_create: t('admin.action_share_create'), share_delete: t('admin.action_share_delete'),
            invite_create: t('admin.action_invite_create'), invite_delete: t('admin.action_invite_delete'),
            user_update: t('admin.action_user_update'), user_delete: t('admin.action_user_delete'),
        };
        for (const e of data.entries) {
            html += `<tr>
                <td style="white-space:nowrap;font-size:12px">${formatDate(e.timestamp)}</td>
                <td>${escapeHtml(e.username || '-')}</td>
                <td>${actionLabels[e.action] || e.action}</td>
                <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(e.target_path || '')}">${escapeHtml(e.target_path || '-')}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        if (data.total > 50) {
            html += `<div style="text-align:center;padding:8px;font-size:12px;color:var(--text-secondary)">${t('admin.audit_showing', {n: 50, total: data.total})}</div>`;
        }
        container.innerHTML = html;
    } catch (e) { container.innerHTML = `<div style="color:var(--danger)">${t('common.error_load')}: ${escapeHtml(e.message)}</div>`; }
}

async function _renderSecurityTab(container) {
    const SHELL = `
        <div class="security-bar">
            <div class="security-stats" id="secStats"></div>
            <label class="security-filter">
                <input type="checkbox" id="secOnlyFailed" onchange="window._securityToggleFilter(this.checked)">
                <span>${t('admin.sec_only_failed')}</span>
            </label>
            <span class="security-live"><span class="live-dot"></span>${t('admin.sec_live')}</span>
        </div>
        <div id="secList" class="security-list"></div>`;
    container.innerHTML = SHELL;

    const state = { lastId: 0, onlyFailed: false, rows: [] };
    window._securityState = state;

    const renderRows = () => {
        const list = document.getElementById('secList');
        if (!list) return;
        if (state.rows.length === 0) {
            list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-secondary)">${t('admin.sec_empty')}</div>`;
            return;
        }
        let html = `<table class="admin-table"><thead><tr>
            <th style="width:18px"></th>
            <th>${t('admin.sec_time')}</th>
            <th>${t('admin.sec_result')}</th>
            <th>${t('admin.sec_username')}</th>
            <th>${t('admin.sec_ip')}</th>
        </tr></thead><tbody>`;
        for (const r of state.rows) {
            const date = new Date(r.attempted_at * 1000);
            const ts = date.toLocaleString();
            const ok = r.success === 1;
            const cls = ok ? 'sec-ok' : 'sec-fail';
            const label = ok ? t('admin.sec_success') : t('admin.sec_fail');
            const flash = r._fresh ? ' sec-flash' : '';
            const rid = `sec-row-${r.id}`;
            html += `<tr class="sec-row ${cls}${flash}" onclick="window._toggleSecDetail(${r.id})">
                <td style="color:var(--text-secondary)"><span id="${rid}-caret">&#9656;</span></td>
                <td style="white-space:nowrap;font-size:12px">${escapeHtml(ts)}</td>
                <td><span class="sec-badge sec-badge-${ok ? 'ok' : 'fail'}">${label}</span></td>
                <td>${escapeHtml(r.username || '-')}</td>
                <td style="font-family:monospace;font-size:12px">${escapeHtml(r.ip || '-')}</td>
            </tr>
            <tr class="sec-detail-row" id="${rid}-detail" style="display:none"><td colspan="5">
                <div><span class="detail-label">${t('admin.sec_ua')}:</span> <span class="detail-val">${escapeHtml(r.user_agent || '-')}</span></div>
            </td></tr>`;
        }
        html += '</tbody></table>';
        list.innerHTML = html;
    };

    const renderStats = (data) => {
        const el = document.getElementById('secStats');
        if (el) el.innerHTML = `${t('admin.sec_total', {n: data.total})} · <span style="color:var(--danger)">${t('admin.sec_fails_24h', {n: data.fails_24h})}</span>`;
    };

    const fetchInitial = async () => {
        const data = await API.getLoginAttempts({ limit: 200, only_failed: state.onlyFailed });
        state.rows = data.entries;
        if (state.rows.length) state.lastId = Math.max(...state.rows.map(r => r.id));
        renderStats(data);
        renderRows();
    };

    const fetchDelta = async () => {
        // Stop polling if the modal/tab is gone
        if (!document.getElementById('secList')) {
            if (window._securityPollTimer) { clearInterval(window._securityPollTimer); window._securityPollTimer = null; }
            return;
        }
        try {
            const data = await API.getLoginAttempts({ limit: 200, since_id: state.lastId, only_failed: state.onlyFailed });
            renderStats(data);
            if (data.entries.length === 0) return;
            for (const r of data.entries) r._fresh = true;
            state.rows = [...data.entries, ...state.rows].slice(0, 200);
            state.lastId = Math.max(state.lastId, ...data.entries.map(r => r.id));
            renderRows();
            // Strip _fresh after flash animation
            setTimeout(() => {
                state.rows.forEach(r => { delete r._fresh; });
            }, 1500);
        } catch (e) { /* swallow transient errors */ }
    };

    try {
        await fetchInitial();
        window._securityPollTimer = setInterval(fetchDelta, 3000);
    } catch (e) {
        container.innerHTML = `<div style="color:var(--danger)">${escapeHtml(e.message)}</div>`;
    }
}

window._toggleSecDetail = (id) => {
    const row = document.getElementById(`sec-row-${id}-detail`);
    const caret = document.getElementById(`sec-row-${id}-caret`);
    if (!row) return;
    const open = row.style.display !== 'none';
    row.style.display = open ? 'none' : 'table-row';
    if (caret) caret.innerHTML = open ? '&#9656;' : '&#9662;';
};

window._securityToggleFilter = (checked) => {
    if (!window._securityState) return;
    window._securityState.onlyFailed = checked;
    window._securityState.lastId = 0;
    window._securityState.rows = [];
    // Re-fetch via tab reload
    window._adminTab('security');
};

window._createInvite = async () => {
    try {
        const result = await API.createInvite();
        toast(t('admin.invite_created'), 'success');
        window._adminTab('invites');
    } catch (e) { toast(e.message, 'error'); }
};

window._deleteInvite = async (id) => {
    try {
        await API.deleteInvite(id);
        toast(t('admin.invite_revoked'));
        window._adminTab('invites');
    } catch (e) { toast(e.message, 'error'); }
};

window._changeRole = async (userId, role) => {
    try {
        await API.updateUser(userId, { role });
        toast(t('admin.role_updated'), 'success');
    } catch (e) { toast(e.message, 'error'); window._adminTab('users'); }
};

window._changeQuota = async (userId, gbStr) => {
    const gb = parseFloat(gbStr);
    const bytes = gb > 0 ? Math.round(gb * 1024 * 1024 * 1024) : 0;
    try {
        await API.updateUser(userId, { quota_bytes: bytes });
        toast(t('admin.quota_updated'), 'success');
    } catch (e) { toast(e.message, 'error'); window._adminTab('users'); }
};

window._toggleUser = async (userId, disable) => {
    try {
        await API.updateUser(userId, { is_disabled: !!disable });
        toast(disable ? t('admin.user_disabled_msg') : t('admin.user_enabled_msg'), 'success');
        window._adminTab('users');
    } catch (e) { toast(e.message, 'error'); }
};

window._promptDeleteUser = (userId, username) => {
    const content = document.getElementById('adminContent');
    content.innerHTML = `
        <div style="padding:20px;text-align:center">
            <h3 style="margin-bottom:12px">${t('admin.delete_user_title', {name: escapeHtml(username)})}</h3>
            <p style="color:var(--text-secondary);margin-bottom:16px">${t('admin.delete_user_warning')}</p>
            <label style="display:block;margin-bottom:16px;cursor:pointer">
                <input type="checkbox" id="deleteFilesCheck"> ${t('admin.delete_user_files')}
            </label>
            <div style="display:flex;gap:8px;justify-content:center">
                <button class="btn btn-ghost" onclick="window._adminTab('users')">${t('common.cancel')}</button>
                <button class="btn btn-danger" onclick="window._doDeleteUser('${userId}')">${t('admin.delete_user_confirm')}</button>
            </div>
        </div>`;
};

window._doDeleteUser = async (userId) => {
    const deleteFiles = document.getElementById('deleteFilesCheck')?.checked || false;
    try {
        await API.deleteUser(userId, deleteFiles);
        toast(t('admin.user_deleted'), 'success');
        window._adminTab('users');
    } catch (e) { toast(e.message, 'error'); }
};


// --- Language Switcher ---
window._switchLang = async (lang) => {
    await setLang(lang);
    // Re-render dynamic content
    loadStats();
    renderBreadcrumbs();
    if (state.items.length > 0) renderFiles();
};

// --- Init (modules are deferred, DOM is ready when this runs) ---
applyI18n();
document.getElementById('langSelector').innerHTML = langSelectorHtml();
initUpload();
initSearch();
loadStats();
loadUserContext();

window._toggleHeaderMenu = (e) => {
    e.stopPropagation();
    document.getElementById('headerSecondary').classList.toggle('open');
};

document.addEventListener('click', (e) => {
    document.getElementById('contextMenu').classList.remove('visible');
    const menu = document.getElementById('headerSecondary');
    const toggle = document.getElementById('headerOverflowToggle');
    if (menu && menu.classList.contains('open')) {
        if (toggle.contains(e.target)) return;
        if (!menu.contains(e.target)) { menu.classList.remove('open'); return; }
        if (e.target.closest('.btn, .btn-icon')) menu.classList.remove('open');
    }
});

document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    // Modal/viewer open: only handle Escape; bail out of nav handlers
    const modalOpen = document.getElementById('modalOverlay').classList.contains('visible');
    const viewerOpen = document.getElementById('viewerOverlay').classList.contains('visible');
    if (modalOpen || viewerOpen) return;

    if (e.key === 'Delete' && state.selected.size > 0) {
        confirmDelete([...state.selected]);
        return;
    }
    if (e.key === 'a' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        window._selectAll();
        return;
    }
    if (e.key === 'Escape') {
        state.selected.clear();
        state.cursorIdx = -1;
        renderFiles();
        return;
    }

    // Keyboard navigation
    const sorted = sortItems(state.items);
    if (sorted.length === 0) return;
    const isGrid = state.viewMode === 'grid';
    const cols = isGrid ? getGridColumns() : 1;

    if (e.key === 'ArrowDown') {
        e.preventDefault();
        moveCursor(isGrid ? cols : 1);
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        moveCursor(isGrid ? -cols : -1);
    } else if (e.key === 'ArrowRight' && isGrid) {
        e.preventDefault();
        moveCursor(1);
    } else if (e.key === 'ArrowLeft' && isGrid) {
        e.preventDefault();
        moveCursor(-1);
    } else if (e.key === 'Home') {
        e.preventDefault();
        state.cursorIdx = 0;
        renderFiles();
        scrollCursorIntoView();
    } else if (e.key === 'End') {
        e.preventDefault();
        state.cursorIdx = sorted.length - 1;
        renderFiles();
        scrollCursorIntoView();
    } else if (e.key === 'Enter') {
        e.preventDefault();
        activateCursor();
    } else if (e.key === 'Backspace') {
        e.preventDefault();
        const parent = state.currentPath === '/' ? null
            : (state.currentPath.replace(/\/[^/]+$/, '') || '/');
        if (parent !== null) navigateTo(parent);
    } else if (e.key === ' ' && state.cursorIdx >= 0) {
        // Space toggles selection on cursored item
        e.preventDefault();
        const item = sorted[state.cursorIdx];
        if (item) toggleSelect(item.path);
    }
});

const hash = window.location.hash.slice(1) || '/';
navigateTo(hash);

window.addEventListener('hashchange', () => {
    const newPath = window.location.hash.slice(1) || '/';
    if (newPath !== state.currentPath) navigateTo(newPath);
});

document.getElementById('listViewBtn').classList.toggle('active', state.viewMode === 'list');
document.getElementById('gridViewBtn').classList.toggle('active', state.viewMode === 'grid');

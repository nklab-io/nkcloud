/**
 * NkCloud i18n — lightweight translation system.
 *
 * Usage:
 *   import { t, setLang, getLang, LANGS, applyI18n } from '/static/js/i18n.js';
 *   t('login.title')            → "Login" / "登入"
 *   t('files.selected', {n: 3}) → "3 items selected"
 *   applyI18n()                 → translates all [data-i18n] in the DOM
 */

const LANGS = {
    'en':    'English',
    'zh-TW': '繁體中文',
    'zh-CN': '简体中文',
    'ja':    '日本語',
};

const FALLBACK = 'en';
let _lang = '';
let _strings = {};
let _fallbackStrings = {};

function _detectLang() {
    // 1) localStorage
    const stored = localStorage.getItem('nkcloud_lang');
    if (stored && LANGS[stored]) return stored;
    // 2) browser
    for (const bl of navigator.languages || [navigator.language]) {
        const norm = bl.replace('_', '-');
        if (LANGS[norm]) return norm;
        // zh-Hant → zh-TW, zh-Hans → zh-CN
        if (norm.startsWith('zh-Hant') || norm === 'zh-HK') return 'zh-TW';
        if (norm.startsWith('zh-Hans') || norm === 'zh') return 'zh-CN';
        const prefix = norm.split('-')[0];
        if (LANGS[prefix]) return prefix;
    }
    return FALLBACK;
}

async function _load(lang) {
    const res = await fetch(`/static/lang/${lang}.json`);
    if (!res.ok) throw new Error(`Lang ${lang} not found`);
    return res.json();
}

function _resolve(obj, key) {
    return key.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), obj);
}

/**
 * Translate a key with optional interpolation.
 * t('files.selected', {n: 3}) replaces {n} → 3
 */
function t(key, params) {
    let val = _resolve(_strings, key) ?? _resolve(_fallbackStrings, key) ?? key;
    if (params && typeof val === 'string') {
        for (const [k, v] of Object.entries(params)) {
            val = val.replaceAll(`{${k}}`, v);
        }
    }
    return val;
}

function getLang() { return _lang; }

async function setLang(lang) {
    if (!LANGS[lang]) lang = FALLBACK;
    _lang = lang;
    localStorage.setItem('nkcloud_lang', lang);
    document.documentElement.lang = lang;
    try {
        _strings = await _load(lang);
        if (lang !== FALLBACK) {
            _fallbackStrings = await _load(FALLBACK);
        }
    } catch {
        if (lang !== FALLBACK) {
            _strings = await _load(FALLBACK);
        }
    }
    applyI18n();
}

/**
 * Apply translations to all elements with [data-i18n] attribute.
 * data-i18n="key"                → sets textContent
 * data-i18n-placeholder="key"    → sets placeholder
 * data-i18n-title="key"          → sets title
 */
function applyI18n(root) {
    const container = root || document;
    container.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (key) el.textContent = t(key);
    });
    container.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        if (key) el.placeholder = t(key);
    });
    container.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        if (key) el.title = t(key);
    });
}

/**
 * Initialize i18n — call once at page load. Returns when strings are ready.
 */
async function initI18n(forceLang) {
    const lang = forceLang || _detectLang();
    await setLang(lang);
    return lang;
}

/**
 * Render a language selector dropdown (returns HTML string).
 */
function langSelectorHtml() {
    let html = '<select class="lang-select" onchange="window._switchLang(this.value)">';
    for (const [code, name] of Object.entries(LANGS)) {
        const sel = code === _lang ? ' selected' : '';
        html += `<option value="${code}"${sel}>${name}</option>`;
    }
    html += '</select>';
    return html;
}

export { t, getLang, setLang, initI18n, applyI18n, LANGS, langSelectorHtml };

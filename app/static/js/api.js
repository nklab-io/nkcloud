// NkCloud API client
const API = {
    _getCsrfToken() {
        const match = document.cookie.split('; ').find(c => c.startsWith('nkcloud_csrf='));
        return match ? match.split('=')[1] : '';
    },

    async _fetch(url, opts = {}) {
        // Add CSRF header for mutating requests
        if (opts.method && opts.method !== 'GET') {
            opts.headers = opts.headers || {};
            opts.headers['X-CSRF-Token'] = this._getCsrfToken();
        }
        const res = await fetch(url, opts);
        if (res.status === 401) {
            window.location.href = '/login';
            throw new Error('Unauthorized');
        }
        return res;
    },

    // --- Files ---

    async listFiles(path = '/') {
        const res = await this._fetch(`/api/files?path=${encodeURIComponent(path)}`);
        return res.json();
    },

    async mkdir(path) {
        const res = await this._fetch('/api/files/mkdir', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async rename(path, newName) {
        const res = await this._fetch('/api/files/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, new_name: newName }),
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async move(paths, destination) {
        const res = await this._fetch('/api/files/move', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths, destination }),
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async deleteFiles(paths) {
        const res = await this._fetch('/api/files', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths }),
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    // --- Trash ---

    async listTrash() {
        const res = await this._fetch('/api/files/trash');
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async restoreTrash(ids) {
        const res = await this._fetch('/api/files/trash/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async purgeTrash(ids) {
        const res = await this._fetch('/api/files/trash', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async emptyTrash() {
        const res = await this._fetch('/api/files/trash/empty', { method: 'POST' });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    // --- Text preview ---

    async getText(path) {
        const res = await this._fetch(`/api/files/text?path=${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async search(q, path = '/') {
        const res = await this._fetch(`/api/search?q=${encodeURIComponent(q)}&path=${encodeURIComponent(path)}`);
        return res.json();
    },

    async getStats() {
        const res = await this._fetch('/api/stats');
        return res.json();
    },

    // --- Shares ---

    async listShares() {
        const res = await this._fetch('/api/shares');
        return res.json();
    },

    async createShare(path, password = null, expiresAt = null, type = 'file_download') {
        const body = { path, type };
        if (password) body.password = password;
        if (expiresAt) body.expires_at = expiresAt;
        const res = await this._fetch('/api/shares', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async deleteShare(id) {
        const res = await this._fetch(`/api/shares/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    // --- Users ---

    async getMe() {
        const res = await this._fetch('/api/users/me');
        return res.json();
    },

    async listUsers() {
        const res = await this._fetch('/api/users');
        return res.json();
    },

    async updateUser(userId, data) {
        const res = await this._fetch(`/api/users/${userId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async deleteUser(userId, deleteFiles = false) {
        const res = await this._fetch(`/api/users/${userId}?delete_files=${deleteFiles}`, {
            method: 'DELETE',
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    // --- Invites ---

    async createInvite(expiresHours = null) {
        const body = {};
        if (expiresHours) body.expires_hours = expiresHours;
        const res = await this._fetch('/api/invites', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    async listInvites() {
        const res = await this._fetch('/api/invites');
        return res.json();
    },

    async deleteInvite(id) {
        const res = await this._fetch(`/api/invites/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error((await res.json()).detail);
        return res.json();
    },

    // --- Audit ---

    async getAuditLog(params = {}) {
        const qs = new URLSearchParams(params).toString();
        const res = await this._fetch(`/api/audit?${qs}`);
        return res.json();
    },

    async getLoginAttempts(params = {}) {
        const qs = new URLSearchParams(params).toString();
        const res = await this._fetch(`/api/security/login-attempts?${qs}`);
        return res.json();
    },

    // --- Session ---

    async getSession() {
        const res = await this._fetch('/api/session');
        return res.json();
    },

    async logout() {
        await this._fetch('/api/logout', { method: 'POST' });
        window.location.href = '/login';
    },

    // --- URLs ---

    downloadUrl(path) {
        return `/api/files/download?path=${encodeURIComponent(path)}`;
    },

    downloadZipUrl(path) {
        return `/api/files/download-zip?path=${encodeURIComponent(path)}`;
    },

    downloadBatchUrl(paths) {
        const qs = paths.map(p => `paths=${encodeURIComponent(p)}`).join('&');
        return `/api/files/download-batch?${qs}`;
    },

    streamUrl(path) {
        return `/api/files/stream?path=${encodeURIComponent(path)}`;
    },

    thumbUrl(path) {
        return `/api/thumb?path=${encodeURIComponent(path)}`;
    },

    previewUrl(path) {
        return `/api/thumb?path=${encodeURIComponent(path)}&size=preview`;
    },
};

export default API;

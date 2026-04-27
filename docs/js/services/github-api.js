// docs/js/github-api.js
class GitHubAPI {
    constructor(token, owner, repo) {
        this.token = token;
        this.owner = owner;
        this.repo = repo;
        this.baseUrl = `https://api.github.com/repos/${owner}/${repo}`;
    }

    get headers() {
        return {
            'Authorization': `Bearer ${this.token}`,
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        };
    }

    async getFile(path) {
        const res = await fetch(`${this.baseUrl}/contents/${path}`, { headers: this.headers });
        if (!res.ok) {
            let errMsg = res.statusText;
            try {
                const errData = await res.json();
                if (errData.message) errMsg = errData.message;
            } catch(e) {}
            throw new Error(`[${res.status}] ${errMsg}`);
        }
        const data = await res.json();
        // Base64 decode utf-8
        const content = decodeURIComponent(escape(atob(data.content.replace(/\n/g, ''))));
        return { content, sha: data.sha };
    }

    async updateFile(path, content, message, sha) {
        const encoded = btoa(unescape(encodeURIComponent(content)));
        const payload = {
            message,
            content: encoded,
            sha
        };
        const res = await fetch(`${this.baseUrl}/contents/${path}`, {
            method: 'PUT',
            headers: this.headers,
            body: JSON.stringify(payload)
        });
        if (res.status === 409) {
            throw new Error('Conflict: 파일이 그 사이에 다른 곳에서 변경되었습니다. 다시 불러오기 후 진행해주세요.');
        }
        if (!res.ok) {
            const errData = await res.json();
            throw new Error(`Failed to update file: ${errData.message}`);
        }
        return await res.json();
    }
}
window.GitHubAPI = GitHubAPI;

export class ApiClient {
  constructor(appKey, userEmail, userName, userHash, apiUrl) {
    this.appKey = appKey;
    this.userEmail = userEmail;
    this.userName = userName;
    this.userHash = userHash;
    // Strip trailing slash
    this.apiUrl = apiUrl.replace(/\/+$/, "");
  }

  get isAuthenticated() {
    return !!(this.userEmail && this.userHash);
  }

  authHeaders() {
    const headers = { "X-App-Key": this.appKey };
    if (this.isAuthenticated) {
      headers["X-User-Email"] = this.userEmail;
      headers["X-User-Name"] = this.userName;
      headers["X-User-Hash"] = this.userHash;
    }
    return headers;
  }

  headers() {
    return {
      "Content-Type": "application/json",
      ...this.authHeaders(),
    };
  }

  async request(method, path, body) {
    const res = await fetch(`${this.apiUrl}${path}`, {
      method,
      headers: this.headers(),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`API error ${res.status}: ${text}`);
    }
    return res.json();
  }

  async listTickets() {
    return this.request("GET", "/widget/tickets/");
  }

  async getTicket(id) {
    return this.request("GET", `/widget/tickets/${id}/`);
  }

  async createTicket(subject, body) {
    return this.request("POST", "/widget/tickets/", { subject, body });
  }

  buildContext() {
    return {
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      screen: `${window.screen.width}x${window.screen.height}`,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      referrer: document.referrer,
      platform: (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform,
      console_errors: this.consoleErrors || [],
      app_context: this.appContext || {},
    };
  }

  async getConfig() {
    const res = await fetch(
      `${this.apiUrl}/widget/config/?key=${encodeURIComponent(this.appKey)}`,
      { headers: { "X-App-Key": this.appKey } }
    );
    if (!res.ok) return null;
    return res.json();
  }

  async createTicketWithData({ subject, body, screenshot, submitterEmail, submitterName }) {
    const formData = new FormData();
    formData.append("subject", subject);
    formData.append("body", body);
    formData.append("page_url", window.location.href);
    formData.append("user_agent", navigator.userAgent);
    formData.append("context", JSON.stringify(this.buildContext()));

    if (screenshot) {
      formData.append("screenshot", screenshot, "screenshot.png");
    }
    if (submitterEmail) {
      formData.append("submitter_email", submitterEmail);
    }
    if (submitterName) {
      formData.append("submitter_name", submitterName);
    }

    // No Content-Type header — let browser set multipart boundary
    const res = await fetch(`${this.apiUrl}/widget/tickets/`, {
      method: "POST",
      headers: this.authHeaders(),
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`API error ${res.status}: ${text}`);
    }
    return res.json();
  }

  async addComment(ticketId, body) {
    return this.request("POST", `/widget/tickets/${ticketId}/reply/`, {
      body,
    });
  }
}

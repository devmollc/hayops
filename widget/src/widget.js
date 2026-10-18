import { ApiClient } from "./api";
import { setLinkifiedText } from "./linkify";

const ICONS = {
  chat: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/></svg>`,
  close: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>`,
  back: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>`,
  add: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>`,
  camera: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z"/><path fill="currentColor" d="M9 2 7.17 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2h-3.17L15 2H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z"/></svg>`,
};

// SECURITY: This helper injects ONLY the trusted, hardcoded SVG icon
// constants defined in the ICONS object above — never user-supplied data.
// All user-provided text is rendered via textContent, or via setLinkifiedText
// from ./linkify, which builds anchor nodes without innerHTML.
function setStaticIcon(el, icon) {
  while (el.firstChild) el.removeChild(el.firstChild);
  const template = document.createElement("template");
  template.innerHTML = icon; // safe: icon is always a hardcoded SVG constant from ICONS above
  el.appendChild(template.content.cloneNode(true));
}

function clearElement(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

export class Widget {
  constructor(host, api) {
    this.api = api;
    this.host = host;
    this.root = host.attachShadow({ mode: "open" });
    // A constructed sheet, not a <style>: CSP style-src doesn't govern it, so the
    // widget stays styled on hosts that forbid inline styles.
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(window.__HELPDESK_WIDGET_CSS__ || "");
    this.root.adoptedStyleSheets = [sheet];
    this.state = "closed";
    this.currentTicket = null;
    this.screenshotBlob = null;
    this.productName = null;
    this.render();
  }

  setState(newState) {
    this.state = newState;
    this.render();
  }

  applyBranding(config) {
    if (config.primary_color) {
      this.host.style.setProperty("--hd-primary", config.primary_color);
    }
    this.productName = config.product_name || null;
    this.render();
  }

  render() {
    clearElement(this.root);

    this.renderLauncher();

    if (this.state !== "closed") {
      this.renderPanel();
    }
  }

  renderLauncher() {
    const btn = document.createElement("button");
    btn.className = "hw-launcher";
    setStaticIcon(btn, this.state === "closed" ? ICONS.chat : ICONS.close);
    btn.addEventListener("click", () => {
      if (this.state !== "closed") {
        this.setState("closed");
      } else if (this.api.isAuthenticated) {
        this.setState("list");
        this.loadTickets();
      } else {
        // Without HMAC identity the list is always empty -- opening on a
        // permanently empty "Your Tickets" reads as broken. Go straight to the form.
        this.setState("create");
      }
    });
    this.root.appendChild(btn);
  }

  renderPanel() {
    const panel = document.createElement("div");
    panel.className = "hw-panel";

    const header = this.buildHeader();
    panel.appendChild(header);

    const content = document.createElement("div");
    content.className = "hw-content";
    panel.appendChild(content);

    switch (this.state) {
      case "list":
        this.showMessage(content, "hw-loading", "Loading tickets...");
        break;
      case "detail":
        if (this.currentTicket) {
          this.renderDetail(content, panel);
        }
        break;
      case "create":
        this.renderCreateForm(content);
        break;
    }

    this.root.appendChild(panel);
  }

  buildHeader() {
    const header = document.createElement("div");
    header.className = "hw-header";

    const title = document.createElement("h3");
    title.className = "hw-header-title";

    const actions = document.createElement("div");
    actions.className = "hw-header-actions";

    switch (this.state) {
      case "list":
        title.textContent = this.productName ? `${this.productName} Support` : "Your Tickets";
        const newBtn = document.createElement("button");
        setStaticIcon(newBtn, ICONS.add);
        newBtn.title = "New Ticket";
        newBtn.addEventListener("click", () => this.setState("create"));
        actions.appendChild(newBtn);
        break;
      case "detail":
        title.textContent = "Ticket Details";
        // Back leads to the ticket list, which only exists with HMAC identity.
        if (this.api.isAuthenticated) {
          const backBtn = document.createElement("button");
          setStaticIcon(backBtn, ICONS.back);
          backBtn.title = "Back to list";
          backBtn.addEventListener("click", () => {
            this.currentTicket = null;
            this.setState("list");
            this.loadTickets();
          });
          actions.appendChild(backBtn);
        }
        break;
      case "create":
        title.textContent = "New Ticket";
        if (this.api.isAuthenticated) {
          const backBtn2 = document.createElement("button");
          setStaticIcon(backBtn2, ICONS.back);
          backBtn2.title = "Back to list";
          backBtn2.addEventListener("click", () => {
            this.screenshotBlob = null;
            this.setState("list");
            this.loadTickets();
          });
          actions.appendChild(backBtn2);
        }
        break;
    }

    header.appendChild(title);
    header.appendChild(actions);
    return header;
  }

  showMessage(parent, className, text) {
    const el = document.createElement("div");
    el.className = className;
    el.textContent = text;
    parent.appendChild(el);
  }

  async loadTickets() {
    const content = this.root.querySelector(".hw-content");
    if (!content) return;

    try {
      const tickets = await this.api.listTickets();
      if (this.state !== "list") return;

      if (tickets.length === 0) {
        content.textContent = "";
        const empty = document.createElement("div");
        empty.className = "hw-empty";
        const p = document.createElement("p");
        p.textContent = "No tickets yet.";
        empty.appendChild(p);
        const createBtn = document.createElement("button");
        createBtn.className = "hw-btn hw-btn-primary";
        createBtn.textContent = "Create a Ticket";
        createBtn.addEventListener("click", () => this.setState("create"));
        empty.appendChild(createBtn);
        content.appendChild(empty);
        return;
      }

      this.renderTicketList(content, tickets);
    } catch {
      if (this.state !== "list") return;
      content.textContent = "";
      this.showMessage(content, "hw-error", "Failed to load tickets. Please try again.");
    }
  }

  renderTicketList(content, tickets) {
    const list = document.createElement("ul");
    list.className = "hw-ticket-list";

    for (const ticket of tickets) {
      const item = document.createElement("li");
      item.className = "hw-ticket-item";

      const subjectDiv = document.createElement("div");
      subjectDiv.className = "hw-ticket-subject";
      subjectDiv.textContent = ticket.subject;
      item.appendChild(subjectDiv);

      const metaDiv = document.createElement("div");
      metaDiv.className = "hw-ticket-meta";

      const badge = document.createElement("span");
      badge.className = "hw-status-badge";
      badge.textContent = ticket.status;
      metaDiv.appendChild(badge);

      const stamp = this.formatDate(ticket.updated_at);
      if (stamp) metaDiv.appendChild(document.createTextNode(` \u00b7 ${stamp}`));
      item.appendChild(metaDiv);

      item.addEventListener("click", () => this.openTicket(ticket.id));
      list.appendChild(item);
    }

    content.textContent = "";
    content.appendChild(list);
  }

  async openTicket(id) {
    this.state = "detail";
    this.currentTicket = null;
    this.render();

    const content = this.root.querySelector(".hw-content");
    if (!content) return;
    content.textContent = "";
    this.showMessage(content, "hw-loading", "Loading ticket...");

    try {
      this.currentTicket = await this.api.getTicket(id);
      if (this.state !== "detail") return;
      this.render();
    } catch {
      if (this.state !== "detail") return;
      content.textContent = "";
      this.showMessage(content, "hw-error", "Failed to load ticket.");
    }
  }

  renderDetail(content, panel) {
    const ticket = this.currentTicket;

    const detail = document.createElement("div");
    detail.className = "hw-detail";

    const subjectEl = document.createElement("h4");
    subjectEl.className = "hw-detail-subject";
    subjectEl.textContent = ticket.subject;
    detail.appendChild(subjectEl);

    const metaEl = document.createElement("div");
    metaEl.className = "hw-detail-meta";
    const badge = document.createElement("span");
    badge.className = "hw-status-badge";
    badge.textContent = ticket.status;
    metaEl.appendChild(badge);
    const created = this.formatDate(ticket.created_at);
    if (created) metaEl.appendChild(document.createTextNode(` \u00b7 Created ${created}`));
    detail.appendChild(metaEl);

    const bodyEl = document.createElement("div");
    bodyEl.className = "hw-detail-body";
    setLinkifiedText(bodyEl, ticket.body);
    detail.appendChild(bodyEl);

    if (ticket.comments && ticket.comments.length > 0) {
      const commentsTitle = document.createElement("div");
      commentsTitle.className = "hw-comments-title";
      commentsTitle.textContent = "Comments";
      detail.appendChild(commentsTitle);

      for (const comment of ticket.comments) {
        const el = document.createElement("div");
        el.className = `hw-comment${comment.is_agent ? " hw-agent" : ""}`;

        const authorEl = document.createElement("div");
        authorEl.className = "hw-comment-author";
        authorEl.textContent = comment.author;
        el.appendChild(authorEl);

        const bodyDiv = document.createElement("div");
        bodyDiv.className = "hw-comment-body";
        setLinkifiedText(bodyDiv, comment.body);
        el.appendChild(bodyDiv);

        const dateEl = document.createElement("div");
        dateEl.className = "hw-comment-date";
        dateEl.textContent = this.formatDate(comment.created_at);
        el.appendChild(dateEl);

        detail.appendChild(el);
      }
    }

    content.appendChild(detail);

    // Reply form at bottom of panel
    const replyForm = document.createElement("div");
    replyForm.className = "hw-reply-form";

    const textarea = document.createElement("textarea");
    textarea.placeholder = "Write a reply...";
    textarea.rows = 1;
    replyForm.appendChild(textarea);

    const sendBtn = document.createElement("button");
    sendBtn.className = "hw-btn hw-btn-primary";
    sendBtn.textContent = "Send";
    replyForm.appendChild(sendBtn);

    sendBtn.addEventListener("click", async () => {
      const body = textarea.value.trim();
      if (!body) return;

      sendBtn.setAttribute("disabled", "true");
      sendBtn.textContent = "...";

      try {
        await this.api.addComment(ticket.id, body);
        await this.openTicket(ticket.id);
      } catch {
        sendBtn.removeAttribute("disabled");
        sendBtn.textContent = "Send";
      }
    });

    panel.appendChild(replyForm);
  }

  renderCreateForm(content) {
    const form = document.createElement("div");
    form.className = "hw-form";

    // Anonymous mode: show name/email fields when not authenticated
    let nameInput = null;
    let emailInput = null;

    if (!this.api.isAuthenticated) {
      const nameLabel = document.createElement("label");
      nameLabel.textContent = "Name";
      form.appendChild(nameLabel);

      nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.placeholder = "Your name";
      // Prefilled when the host page knows who is signed in but isn't running
      // HMAC identity -- saves the retyping without claiming the user is verified.
      nameInput.value = this.api.userName || "";
      form.appendChild(nameInput);

      const emailLabel = document.createElement("label");
      emailLabel.textContent = "Email";
      form.appendChild(emailLabel);

      emailInput = document.createElement("input");
      emailInput.type = "email";
      emailInput.placeholder = "your@email.com";
      emailInput.value = this.api.userEmail || "";
      form.appendChild(emailInput);
    }

    const subjectLabel = document.createElement("label");
    subjectLabel.textContent = "Subject";
    form.appendChild(subjectLabel);

    const subjectInput = document.createElement("input");
    subjectInput.type = "text";
    subjectInput.placeholder = "Brief summary of your issue";
    form.appendChild(subjectInput);

    const bodyLabel = document.createElement("label");
    bodyLabel.textContent = "Description";
    form.appendChild(bodyLabel);

    const bodyInput = document.createElement("textarea");
    bodyInput.placeholder = "Describe your issue in detail...";
    form.appendChild(bodyInput);

    // Screenshot button
    const screenshotBtn = document.createElement("button");
    screenshotBtn.className = "hw-btn hw-screenshot-btn";
    screenshotBtn.type = "button";
    setStaticIcon(screenshotBtn, ICONS.camera);
    const screenshotLabel = document.createElement("span");
    screenshotLabel.textContent = " Take Screenshot";
    screenshotBtn.appendChild(screenshotLabel);
    form.appendChild(screenshotBtn);

    // Screenshot preview container
    const previewContainer = document.createElement("div");
    previewContainer.className = "hw-screenshot-preview";
    previewContainer.style.display = "none";
    form.appendChild(previewContainer);

    // Restore existing screenshot if present
    if (this.screenshotBlob) {
      this.showScreenshotPreview(previewContainer, screenshotBtn);
    }

    screenshotBtn.addEventListener("click", () => {
      this.captureScreenshot(previewContainer, screenshotBtn);
    });

    const submitBtn = document.createElement("button");
    submitBtn.className = "hw-btn hw-btn-primary hw-btn-block";
    submitBtn.textContent = "Submit Ticket";
    form.appendChild(submitBtn);

    const errorEl = document.createElement("div");
    errorEl.className = "hw-error";
    errorEl.style.display = "none";
    errorEl.style.marginTop = "8px";
    form.appendChild(errorEl);

    submitBtn.addEventListener("click", async () => {
      const subject = subjectInput.value.trim();
      const body = bodyInput.value.trim();

      if (!subject || !body) {
        errorEl.textContent = "Please fill in both subject and description.";
        errorEl.style.display = "block";
        return;
      }

      if (!this.api.isAuthenticated && emailInput && !emailInput.value.trim()) {
        errorEl.textContent = "Please provide your email address.";
        errorEl.style.display = "block";
        return;
      }

      errorEl.style.display = "none";
      submitBtn.setAttribute("disabled", "true");
      submitBtn.textContent = "Submitting...";

      try {
        const ticket = await this.api.createTicketWithData({
          subject,
          body,
          screenshot: this.screenshotBlob,
          submitterEmail: emailInput ? emailInput.value.trim() : undefined,
          submitterName: nameInput ? nameInput.value.trim() : undefined,
        });
        this.screenshotBlob = null;
        this.currentTicket = ticket;
        this.setState("detail");
      } catch {
        errorEl.textContent = "Failed to create ticket. Please try again.";
        errorEl.style.display = "block";
        submitBtn.removeAttribute("disabled");
        submitBtn.textContent = "Submit Ticket";
      }
    });

    content.appendChild(form);
  }

  async loadMarkerJs() {
    if (window.markerjs2) return;
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = window.__HELPDESK_WIDGET_VENDOR__ + "markerjs2.js";
      script.onload = resolve;
      script.onerror = () => reject(new Error("Failed to load markerjs2"));
      document.head.appendChild(script);
    });
  }

  openAnnotationOverlay(previewContainer, screenshotBtn) {
    // A modal <dialog>, not a stacked overlay. The top layer puts it above every
    // z-index on the host page and makes that page inert, so there is exactly one
    // window the user can act on -- issue #7 was two live-looking copies of the
    // same screenshot with no way to tell which was the app.
    const dialog = document.createElement("dialog");
    dialog.className = "hw-annotate-dialog";
    dialog.setAttribute("aria-label", "Annotate screenshot");

    // Toolbar
    const toolbar = document.createElement("div");
    toolbar.className = "hw-annotate-toolbar";

    const title = document.createElement("span");
    title.textContent = "Annotate screenshot";
    toolbar.appendChild(title);

    const actions = document.createElement("div");
    actions.className = "hw-annotate-toolbar-actions";

    const close = () =>
      this.closeAnnotationOverlay(dialog, previewContainer, screenshotBtn);

    const skipBtn = document.createElement("button");
    skipBtn.className = "hw-btn hw-btn-secondary";
    skipBtn.type = "button";
    skipBtn.textContent = "Skip";
    skipBtn.addEventListener("click", close);
    actions.appendChild(skipBtn);

    const doneBtn = document.createElement("button");
    doneBtn.className = "hw-btn hw-btn-primary";
    doneBtn.type = "button";
    doneBtn.textContent = "Done";
    doneBtn.addEventListener("click", () => {
      if (this._markerArea) {
        this._markerArea.startRenderAndClose();
      } else {
        close();
      }
    });
    actions.appendChild(doneBtn);

    toolbar.appendChild(actions);
    dialog.appendChild(toolbar);

    // markerjs draws its own copy of the screenshot. This one exists only so it
    // has something to measure, and never paints -- see .hw-annotate-source.
    const img = document.createElement("img");
    img.className = "hw-annotate-source";
    img.alt = "";
    img.src = URL.createObjectURL(this.screenshotBlob);
    dialog.appendChild(img);

    this.root.appendChild(dialog);
    dialog.showModal();

    // Escape is the same decision as Skip: keep the screenshot, drop the markup.
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close();
    });

    // markerjs measures the target on show(). Called before the blob decodes it
    // reads 0x0 and lays the editor out against nothing.
    img.decode().then(() => {
      if (dialog.isConnected) this.startMarkerArea(dialog, toolbar, img, close);
    }, close);
  }

  startMarkerArea(dialog, toolbar, img, close) {
    try {
      const markerjs2 = window.markerjs2;
      // markerjs2 appends an empty <style> and fills it via insertRule; without the
      // host's nonce a strict CSP blocks it and the sheet is null, so init throws.
      const root = this.root;
      const nonce = window.__HELPDESK_WIDGET_NONCE__;
      markerjs2.Style.styleSheetRoot = {
        appendChild(el) {
          if (nonce) el.nonce = nonce;
          return root.appendChild(el);
        },
        removeChild: (el) => root.removeChild(el),
      };

      const markerArea = new markerjs2.MarkerArea(img);
      // Inside the dialog. Pointed at the shadow root, markerjs's absolutely
      // positioned editor resolved against the document instead and landed
      // offset from the screenshot it was supposed to sit on (issue #7).
      markerArea.targetRoot = dialog;
      // Popup mode sizes and centres the editor against the viewport itself, so
      // there is no alignment between its layout and ours left to drift.
      markerArea.settings.displayMode = "popup";
      // Its chrome insets by this much, which keeps it clear of our toolbar.
      markerArea.settings.popupMargin = toolbar.offsetHeight;
      markerArea.renderAtNaturalSize = true;
      markerArea.uiStyleSettings.zIndex = 1000000;
      markerArea.availableMarkerTypes = [
        markerjs2.ArrowMarker,
        markerjs2.FrameMarker,
        markerjs2.HighlightMarker,
        markerjs2.FreehandMarker,
        markerjs2.TextMarker,
        markerjs2.CalloutMarker,
      ];

      // startRenderAndClose fires render and then close, so the close handler
      // would otherwise tear the dialog down while the annotated blob is still
      // in flight and put the unannotated screenshot in the preview.
      let rendering = false;

      markerArea.addEventListener("render", (event) => {
        rendering = true;
        // Convert the annotated dataUrl to a blob and replace the screenshot
        fetch(event.dataUrl)
          .then((res) => res.blob())
          .then((blob) => {
            this.screenshotBlob = blob;
            close();
          })
          .catch(close);
      });

      markerArea.addEventListener("close", () => {
        // User closed without rendering — keep unannotated screenshot
        if (!rendering) close();
      });

      this._markerArea = markerArea;
      markerArea.show();
    } catch (err) {
      console.error("[HayOps Widget] markerjs2 init failed:", err);
      close();
    }
  }

  closeAnnotationOverlay(dialog, previewContainer, screenshotBtn) {
    if (!dialog.isConnected) return;
    this._markerArea = null;

    const img = dialog.querySelector(".hw-annotate-source");
    if (img) URL.revokeObjectURL(img.src);

    dialog.close();
    dialog.remove();

    this.showScreenshotPreview(previewContainer, screenshotBtn);
  }

  async captureScreenshot(previewContainer, screenshotBtn) {
    screenshotBtn.setAttribute("disabled", "true");
    screenshotBtn.textContent = "Capturing...";

    try {
      // Hide the widget host so it doesn't appear in the screenshot
      this.host.style.display = "none";

      // html2canvas-pro, not html2canvas. Stock html2canvas 1.4.1 is unmaintained
      // and throws on modern CSS colour functions -- `oklch()` is Tailwind v4's
      // entire default palette, so capture failed outright on every Tailwind v4
      // host. The fork parses oklch/lab/color-mix and exports the same global.
      if (!window.html2canvas) {
        await new Promise((resolve, reject) => {
          const script = document.createElement("script");
          script.src = window.__HELPDESK_WIDGET_VENDOR__ + "html2canvas-pro.min.js";
          script.onload = resolve;
          script.onerror = () => reject(new Error("Failed to load html2canvas-pro"));
          document.head.appendChild(script);
        });
      }

      // Capture what the user is actually looking at. Left to itself html2canvas
      // renders the whole scroll height -- on a long page that was a 3.7MB PNG of
      // mostly off-screen content, when the bug being reported is on screen.
      const canvas = await window.html2canvas(document.body, {
        x: window.scrollX,
        y: window.scrollY,
        width: window.innerWidth,
        height: window.innerHeight,
        windowWidth: document.documentElement.clientWidth,
        windowHeight: document.documentElement.clientHeight,
        useCORS: true,
        logging: false,
      });

      // Show the widget host again
      this.host.style.display = "";

      // Convert canvas to blob
      this.screenshotBlob = await new Promise((resolve) => {
        canvas.toBlob(resolve, "image/png");
      });

      // Try to open annotation overlay, fall back to plain preview
      try {
        await this.loadMarkerJs();
        this.openAnnotationOverlay(previewContainer, screenshotBtn);
      } catch {
        this.showScreenshotPreview(previewContainer, screenshotBtn);
      }
    } catch (err) {
      this.host.style.display = "";
      console.error("[HayOps Widget] Screenshot failed:", err);
      screenshotBtn.removeAttribute("disabled");
      clearElement(screenshotBtn);
      setStaticIcon(screenshotBtn, ICONS.camera);
      const label = document.createElement("span");
      label.textContent = " Take Screenshot";
      screenshotBtn.appendChild(label);

      // Say so. This used to fail into console.error alone: the widget blinked
      // out while the host was hidden, came back unchanged, and the user was
      // left thinking the button was dead.
      clearElement(previewContainer);
      previewContainer.style.display = "block";
      const failed = document.createElement("div");
      failed.className = "hw-error";
      failed.textContent = "Couldn't capture the page. You can still send the ticket without a screenshot.";
      previewContainer.appendChild(failed);
    }
  }

  showScreenshotPreview(previewContainer, screenshotBtn) {
    clearElement(previewContainer);
    previewContainer.style.display = "block";

    const img = document.createElement("img");
    img.src = URL.createObjectURL(this.screenshotBlob);
    img.alt = "Screenshot preview";
    previewContainer.appendChild(img);

    const removeBtn = document.createElement("button");
    removeBtn.className = "hw-screenshot-remove";
    removeBtn.textContent = "\u00d7";
    removeBtn.title = "Remove screenshot";
    removeBtn.addEventListener("click", () => {
      URL.revokeObjectURL(img.src);
      this.screenshotBlob = null;
      previewContainer.style.display = "none";
      clearElement(previewContainer);
      screenshotBtn.removeAttribute("disabled");
      clearElement(screenshotBtn);
      setStaticIcon(screenshotBtn, ICONS.camera);
      const label = document.createElement("span");
      label.textContent = " Take Screenshot";
      screenshotBtn.appendChild(label);
      screenshotBtn.style.display = "";
    });
    previewContainer.appendChild(removeBtn);

    // Hide the screenshot button while preview is shown
    screenshotBtn.style.display = "none";
  }

  formatDate(dateStr) {
    // Returns "" for anything unparseable rather than the string "Invalid Date".
    // The old try/catch never fired: new Date(undefined) doesn't throw, it yields
    // an invalid Date whose toLocaleDateString() cheerfully returns "Invalid Date",
    // which is how a renamed API field reached the panel as user-visible text.
    const date = new Date(dateStr);
    if (!dateStr || Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }
}

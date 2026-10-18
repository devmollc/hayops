import { ApiClient } from "./api";
import { Widget } from "./widget";
import css from "./styles.css?inline";

// Console-error ring buffer — installed before anything else so it can
// catch errors thrown during the rest of widget init.
const MAX_CONSOLE_ERRORS = 10;
const MAX_CONSOLE_ERROR_LEN = 500;
const consoleErrors = [];
function recordConsoleError(message) {
  consoleErrors.push({
    time: new Date().toISOString(),
    message: String(message).slice(0, MAX_CONSOLE_ERROR_LEN),
  });
  if (consoleErrors.length > MAX_CONSOLE_ERRORS) consoleErrors.shift();
}
window.addEventListener("error", (e) => recordConsoleError(e.message));
window.addEventListener("unhandledrejection", (e) => recordConsoleError(`Unhandled rejection: ${e.reason}`));
const originalConsoleError = console.error.bind(console);
console.error = (...args) => {
  recordConsoleError(args.map(String).join(" "));
  originalConsoleError(...args);
};

(function () {
  // Find the current script tag to read data attributes
  const scriptTag =
    document.currentScript ||
    document.querySelector("script[data-app-key]");

  if (!scriptTag) {
    console.error("[HayOps Widget] Script tag with data-app-key not found.");
    return;
  }

  const appKey = scriptTag.getAttribute("data-app-key") || "";
  const userEmail = scriptTag.getAttribute("data-user-email") || "";
  const userName = scriptTag.getAttribute("data-user-name") || "";
  const userHash = scriptTag.getAttribute("data-user-hash") || "";
  const apiUrl =
    scriptTag.getAttribute("data-api-url") || window.location.origin;
  const offsetBottom = scriptTag.getAttribute("data-offset-bottom") || "";
  const offsetRight = scriptTag.getAttribute("data-offset-right") || "";

  if (!appKey) {
    console.error("[HayOps Widget] data-app-key is required.");
    return;
  }

  // Store CSS for the shadow DOM to pick up
  window.__HELPDESK_WIDGET_CSS__ = css;
  // A host whose CSP has no style-src 'unsafe-inline' passes its nonce on our
  // script tag; markerjs2 needs it for the <style> it injects.
  window.__HELPDESK_WIDGET_NONCE__ = scriptTag.nonce || "";
  // Screenshot/annotate libraries are served next to this bundle (pinned in
  // package.json), so host pages allow only the helpdesk origin in their CSP.
  window.__HELPDESK_WIDGET_VENDOR__ = new URL("vendor/", scriptTag.src).href;

  // Create a host element for the shadow DOM
  const host = document.createElement("div");
  host.id = "helpdesk-widget-root";
  // Only accept plain CSS lengths -- these land in a style property, and the
  // script tag is the host page's, so a bad value should be ignored, not injected.
  const CSS_LENGTH = /^\d+(\.\d+)?(px|rem|em|vh|vw)$/;
  if (CSS_LENGTH.test(offsetBottom)) host.style.setProperty("--hd-offset-bottom", offsetBottom);
  if (CSS_LENGTH.test(offsetRight)) host.style.setProperty("--hd-offset-right", offsetRight);
  document.body.appendChild(host);

  // Create the API client and widget
  const api = new ApiClient(appKey, userEmail, userName, userHash, apiUrl);
  api.consoleErrors = consoleErrors;
  api.appContext = {};
  const widget = new Widget(host, api);

  const MAX_CONTEXT_BYTES = 4000;
  window.HelpdeskWidget = {
    setContext(obj) {
      if (!obj || typeof obj !== "object") return;
      const merged = { ...api.appContext, ...obj };
      if (JSON.stringify(merged).length > MAX_CONTEXT_BYTES) {
        console.warn("[HayOps Widget] setContext ignored: payload exceeds 4KB limit.");
        return;
      }
      api.appContext = merged;
    },
  };

  // Fetch branding async — the widget renders with defaults first.
  api.getConfig().then((config) => {
    if (config) widget.applyBranding(config);
  }).catch(() => {});
})();

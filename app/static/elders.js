/* Elders paste, access URL, and the first-visit bookmark note.
   User-facing sentences stay in data-copy-* attributes. This file does not assemble paste_body. */

function classifyPaste(status, data) {
  var code = data && data.code;
  if (status === 0 || status === null || typeof status === "undefined") {
    return { toast: false, retry: true, kind: "network" };
  }
  if (status >= 500) return { toast: false, retry: true, kind: "busy" };
  if (status === 404 && !code) return { toast: false, retry: true, kind: "rolling" };
  if (status === 422) return { toast: false, retry: true, kind: "validation" };
  if (status === 403) return { toast: false, retry: false, kind: "forbidden", textKey: "err_403" };
  if (status === 400 && code === "access_url_required") {
    return { toast: false, retry: false, kind: "empty", textKey: "empty_url_hint" };
  }
  if (status === 400 && code === "invite_expired") {
    return { toast: false, retry: false, kind: "expired", textKey: "inv_expired_sub" };
  }
  if (status === 400 && code === "access_url_invalid") {
    return { toast: false, retry: false, kind: "invalid_url", textKey: "url_help" };
  }
  if (status === 400 && code) return { toast: false, retry: false, kind: "bad" };
  if (status === 404) return { toast: false, retry: false, kind: "missing" };
  if (status === 200 && data && typeof data.paste_body === "string" && data.paste_body) {
    return { toast: true, retry: false, kind: "ok" };
  }
  return { toast: false, retry: true, kind: "retry" };
}

function pasteOutcome(status, data, dismissed, wrote) {
  if (dismissed) return { toast: false, ignored: true };
  var decision = classifyPaste(status, data);
  if (decision.toast && wrote) return { toast: true, kind: "ok" };
  var out = { toast: false, kind: decision.kind, retry: !!decision.retry };
  if (decision.textKey) out.textKey = decision.textKey;
  return out;
}

function bookmarkTipKey(ua) {
  return String(ua || "").indexOf("MicroMessenger") === -1 ? "tip_safari" : "tip_wechat";
}

function bookmarkStorageKey(familyId, userId) {
  return "elders.bookmark.dismissed." + familyId + "." + userId;
}

function bookmarkVisible(stored, role) {
  return role === "viewer" && stored !== "1";
}

function bootElders() {
  bootBookmark();
  bootAccessUrl();
  bootSlip();
}

function bootBookmark() {
  var node = document.getElementById("elders-bookmark");
  if (!node) return;
  var familyId = node.getAttribute("data-family-id");
  var userId = node.getAttribute("data-user-id");
  if (!familyId || !userId) return;
  var key = bookmarkStorageKey(familyId, userId);
  var stored = "";
  try {
    stored = localStorage.getItem(key) || "";
  } catch (err) {
    stored = "";
  }
  if (!bookmarkVisible(stored, "viewer")) return;
  var tipKey = bookmarkTipKey(navigator.userAgent || "");
  var attr = tipKey === "tip_wechat" ? "data-copy-tip-wechat" : "data-copy-tip-safari";
  var line = node.querySelector("[data-bookmark-text]");
  if (line) line.textContent = node.getAttribute(attr) || "";
  node.hidden = false;
  var ok = node.querySelector("[data-bookmark-ok]");
  if (!ok) return;
  ok.addEventListener("click", function () {
    try {
      localStorage.setItem(key, "1");
    } catch (err) {
      /* hide for this view even if the browser refuses storage */
    }
    node.hidden = true;
  });
}

function showEldersToast(text, top) {
  if (!text) return;
  var old = document.getElementById("toast");
  if (old) old.remove();
  var host = document.querySelector(".app") || document.body;
  var el = document.createElement("div");
  el.className = top ? "toast top" : "toast";
  el.id = "toast";
  el.textContent = text;
  host.appendChild(el);
  setTimeout(function () {
    if (el.parentNode) el.remove();
  }, 2000);
}

function bootAccessUrl() {
  var form = document.getElementById("access-url-form");
  if (!form) return;
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var input = form.querySelector("input[name=family_access_url]");
    var error = document.getElementById("access-url-error");
    var button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    fetch("/api/families/current", {
      method: "PATCH",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ family_access_url: input ? input.value : "" }),
    }).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (err) { data = null; }
        }
        if (button) button.disabled = false;
        if (res.status === 200) {
          showEldersToast(form.getAttribute("data-copy-url-saved"), true);
          return;
        }
        if (!error) return;
        error.hidden = false;
        if (res.status === 403) error.textContent = form.getAttribute("data-copy-err-403") || "";
        else error.textContent = form.getAttribute("data-copy-url-help") || (data && data.detail) || "";
      });
    }, function () {
      if (button) button.disabled = false;
    });
  });
}

var eldersEpoch = 0;

function slipMask() {
  return document.getElementById("elders-mask");
}

function slipQuery(mask, selector) {
  return mask.querySelector(selector);
}

function setSlipText(mask, selector, text) {
  var node = slipQuery(mask, selector);
  if (node) node.textContent = text || "";
}

function showBlock(mask, selector, on) {
  mask.querySelectorAll(selector).forEach(function (node) {
    node.hidden = !on;
  });
}

function closeSlip() {
  var mask = slipMask();
  eldersEpoch += 1;
  if (!mask) return;
  mask.hidden = true;
}

function paintEmpty(mask, empty) {
  var slip = slipQuery(mask, ".slip");
  if (slip) slip.classList.toggle("disabled", empty);
  showBlock(mask, "[data-slip-empty]", empty);
  showBlock(mask, "[data-slip-ready]", !empty);
  showBlock(mask, "[data-slip-to]", !empty);
  showBlock(mask, "[data-slip-go]", empty);
  var cta = slipQuery(mask, "[data-slip-copy]");
  if (cta) cta.disabled = empty;
}

function openSlip(button) {
  var mask = slipMask();
  if (!mask || !button) return;
  mask.hidden = false;
  mask.setAttribute("data-kind", button.getAttribute("data-kind") || "");
  mask.setAttribute("data-target-id", button.getAttribute("data-target-id") || "");
  mask.setAttribute("data-has-url", button.getAttribute("data-has-url") || "0");
  setSlipText(mask, "[data-slip-to]", button.getAttribute("data-greeting"));
  setSlipText(mask, "[data-slip-family]", button.getAttribute("data-family"));
  setSlipText(mask, "[data-slip-entry]", button.getAttribute("data-entry"));
  var invite = button.getAttribute("data-invite") || "";
  setSlipText(mask, "[data-slip-invite]", invite);
  showBlock(mask, "[data-slip-invite-block]", !!invite);
  var warn = slipQuery(mask, "[data-slip-warn]");
  if (warn) {
    warn.hidden = true;
    warn.textContent = "";
  }
  var fallback = document.getElementById("elders-fallback");
  if (fallback) {
    fallback.hidden = true;
    fallback.value = "";
  }
  paintEmpty(mask, mask.getAttribute("data-has-url") !== "1");
}

function warnText(mask, decision, data) {
  if (decision.kind === "expired") {
    return (mask.getAttribute("data-copy-inv-expired-title") || "") + "\n" + (mask.getAttribute("data-copy-inv-expired-sub") || "");
  }
  if (decision.kind === "forbidden") return mask.getAttribute("data-copy-err-403") || "";
  if (decision.kind === "empty") return mask.getAttribute("data-copy-empty-url-hint") || "";
  if (decision.kind === "missing" || decision.kind === "bad") return (data && data.detail) || "";
  return "";
}

function showDecision(mask, decision, data) {
  if (decision.kind === "empty") paintEmpty(mask, true);
  var text = warnText(mask, decision, data);
  var warn = slipQuery(mask, "[data-slip-warn]");
  if (!warn) return;
  if (!text) {
    warn.hidden = true;
    warn.textContent = "";
    return;
  }
  warn.hidden = false;
  warn.textContent = text;
}

function fallbackCopy(text) {
  var area = document.getElementById("elders-fallback");
  if (!area) return false;
  area.hidden = false;
  area.value = text;
  area.focus();
  area.select();
  try {
    return document.execCommand("copy");
  } catch (err) {
    return false;
  }
}

function writeClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(function () {
      return true;
    }, function () {
      return fallbackCopy(text);
    });
  }
  return Promise.resolve(fallbackCopy(text));
}

function copySlip() {
  var mask = slipMask();
  if (!mask || mask.hidden) return;
  if (mask.getAttribute("data-has-url") !== "1") return;
  var cta = slipQuery(mask, "[data-slip-copy]");
  if (cta && cta.disabled) return;
  var kind = mask.getAttribute("data-kind");
  var id = Number(mask.getAttribute("data-target-id"));
  if (!id) return;
  var body = {};
  if (kind === "invite") body.invite_id = id;
  else body.member_user_id = id;
  var ticket = eldersEpoch;
  if (cta) cta.disabled = true;
  fetch("/api/families/elders-paste", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(function (res) {
    return res.text().then(function (text) {
      var data = null;
      if (text) {
        try { data = JSON.parse(text); } catch (err) { data = null; }
      }
      if (ticket !== eldersEpoch) return;
      var decision = classifyPaste(res.status, data);
      if (!decision.toast || !data) {
        showDecision(mask, decision, data);
        if (cta) cta.disabled = false;
        return;
      }
      writeClipboard(data.paste_body).then(function (ok) {
        if (ticket !== eldersEpoch) return;
        var outcome = pasteOutcome(res.status, data, false, ok);
        if (outcome.toast) {
          var area = document.getElementById("elders-fallback");
          if (area) area.hidden = true;
          showEldersToast(mask.getAttribute("data-copy-toast-copied"), false);
        } else {
          fallbackCopy(data.paste_body);
        }
        if (cta) cta.disabled = false;
      });
    });
  }, function () {
    if (ticket !== eldersEpoch) return;
    if (cta) cta.disabled = false;
    showDecision(mask, classifyPaste(0, null), null);
  });
}

function bootSlip() {
  document.querySelectorAll("[data-elders-open]").forEach(function (button) {
    button.addEventListener("click", function () { openSlip(button); });
  });
  var mask = slipMask();
  if (!mask) return;
  var cancel = slipQuery(mask, "[data-slip-cancel]");
  if (cancel) cancel.addEventListener("click", closeSlip);
  var copy = slipQuery(mask, "[data-slip-copy]");
  if (copy) copy.addEventListener("click", copySlip);
  mask.addEventListener("click", function (event) {
    if (event.target === mask) closeSlip();
  });
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", bootElders);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    classifyPaste: classifyPaste,
    pasteOutcome: pasteOutcome,
    bookmarkTipKey: bookmarkTipKey,
    bookmarkStorageKey: bookmarkStorageKey,
    bookmarkVisible: bookmarkVisible,
  };
}

(function () {
  var toast = document.getElementById("toast");
  if (toast) {
    setTimeout(function () { toast.remove(); }, 2000);
  }

  function showToast(text) {
    if (!text) return;
    var old = document.getElementById("toast");
    if (old) old.remove();
    var host = document.querySelector(".app") || document.body;
    var el = document.createElement("div");
    el.className = "toast";
    el.id = "toast";
    el.textContent = text;
    host.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, 2000);
  }

  document.body.addEventListener("htmx:responseError", function (event) {
    var xhr = event.detail && event.detail.xhr;
    if (xhr && xhr.status >= 500) {
      var next = location.pathname + location.search;
      window.location.href = "/error?next=" + encodeURIComponent(next);
    }
  });

  var blobUrl = null;
  var current = null;
  var ZIP_NAME = "kids-shiguangji-my-photos.zip";
  // Headers only arrive after the server finishes the temp zip (up to 120s).
  var HEADER_WAIT_MS = 130000;
  // After headers, fail when no new bytes arrive for this long.
  var READ_STALL_MS = 15000;

  function showTemplate(id) {
    var target = document.getElementById("export-sheet");
    var tpl = document.getElementById(id);
    if (!target || !tpl || !tpl.content) return false;
    target.replaceChildren(tpl.content.cloneNode(true));
    return true;
  }

  function swapSuccess() {
    var target = document.getElementById("export-sheet");
    if (!target || !window.htmx) return Promise.reject(new Error("sheet"));
    return window.htmx.ajax("GET", "/fragments/export/sheet?state=success", {
      target: target,
      swap: "innerHTML"
    });
  }

  function readZip(response) {
    if (!response.ok) return Promise.reject(new Error("status"));
    var rawLength = response.headers.get("content-length");
    var expectedSize = rawLength === null ? null : Number(rawLength);
    if (!response.body || !response.body.getReader) {
      return response.blob().then(function (blob) {
        if (!blob || (expectedSize !== null && blob.size !== expectedSize)) throw new Error("body");
        return blob;
      });
    }
    var reader = response.body.getReader();
    var parts = [];
    var received = 0;
    function pump() {
      return new Promise(function (resolve, reject) {
        var timer = setTimeout(function () { reject(new Error("stall")); }, READ_STALL_MS);
        reader.read().then(function (step) {
          clearTimeout(timer);
          resolve(step);
        }, function (err) {
          clearTimeout(timer);
          reject(err);
        });
      }).then(function (step) {
        if (step.done) {
          if (!received || (expectedSize !== null && received !== expectedSize)) throw new Error("short");
          return new Blob(parts, { type: "application/zip" });
        }
        parts.push(step.value);
        received += step.value.byteLength || step.value.length || 0;
        return pump();
      });
    }
    return pump();
  }

  function failSheet() {
    if (blobUrl) {
      URL.revokeObjectURL(blobUrl);
      blobUrl = null;
    }
    showTemplate("export-failure-tpl");
  }

  function pack() {
    if (current) current.abort();
    var controller = new AbortController();
    current = controller;
    var mode = "open";
    var headerTimer = 0;

    function cleanup() {
      clearTimeout(headerTimer);
      window.removeEventListener("offline", onOffline);
    }

    function finishFail() {
      cleanup();
      if (current !== controller || mode === "done" || mode === "fail") return;
      mode = "fail";
      try { controller.abort(); } catch (e) { /* already aborted */ }
      failSheet();
    }

    function onOffline() {
      finishFail();
    }

    window.addEventListener("offline", onOffline);
    if (!showTemplate("export-packing-tpl")) {
      finishFail();
      return;
    }
    headerTimer = setTimeout(finishFail, HEADER_WAIT_MS);
    fetch("/api/export/my-photos.zip", {
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal
    }).then(function (response) {
      clearTimeout(headerTimer);
      if (current !== controller || mode !== "open") return;
      return readZip(response);
    }).then(function (blob) {
      if (current !== controller || mode !== "open") return;
      if (!blob) throw new Error("body");
      mode = "done";
      cleanup();
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      blobUrl = URL.createObjectURL(blob);
      return swapSuccess().catch(function () { failSheet(); });
    }).catch(function () {
      finishFail();
    });
  }

  function saveZip(filename) {
    if (!blobUrl) return;
    var link = document.createElement("a");
    link.href = blobUrl;
    link.download = filename || ZIP_NAME;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  document.body.addEventListener("click", function (event) {
    var mask = document.getElementById("export-mask");
    if (mask && event.target === mask) {
      window.location.href = "/family/settings";
      return;
    }

    var actionNode = event.target.closest("[data-export-action]");
    if (actionNode) {
      var action = actionNode.getAttribute("data-export-action");
      if (action === "start" || action === "retry") {
        event.preventDefault();
        pack();
        return;
      }
    }

    var save = event.target.closest("[data-export-save]");
    if (save) {
      event.preventDefault();
      saveZip(save.getAttribute("data-filename"));
      return;
    }

    var more = event.target.closest("[data-photo-more]");
    var menu = document.getElementById("photo-menu");
    if (more && menu) {
      event.preventDefault();
      var willOpen = menu.hasAttribute("hidden");
      if (willOpen) menu.removeAttribute("hidden");
      else menu.setAttribute("hidden", "");
      more.setAttribute("aria-expanded", willOpen ? "true" : "false");
      return;
    }
    if (menu && !menu.hasAttribute("hidden") && !event.target.closest("#photo-menu")) {
      menu.setAttribute("hidden", "");
      var opener = document.querySelector("[data-photo-more]");
      if (opener) opener.setAttribute("aria-expanded", "false");
    }
    var original = event.target.closest("[data-save-original]");
    if (original) {
      if (menu) menu.setAttribute("hidden", "");
      showToast(original.getAttribute("data-save-original"));
    }
  });
})();

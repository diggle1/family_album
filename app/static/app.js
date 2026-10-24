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

  function swapSheet(state) {
    var target = document.getElementById("export-sheet");
    if (!target || !window.htmx) return Promise.reject(new Error("sheet"));
    return window.htmx.ajax("GET", "/fragments/export/sheet?state=" + encodeURIComponent(state), {
      target: target,
      swap: "innerHTML"
    });
  }

  function readZip(response) {
    if (!response.ok) return Promise.reject(new Error("status"));
    if (!response.body || !response.body.getReader) {
      return response.blob().then(function (blob) {
        if (!blob) throw new Error("body");
        return blob;
      });
    }
    var reader = response.body.getReader();
    var parts = [];
    function pump() {
      return reader.read().then(function (step) {
        if (step.done) return new Blob(parts, { type: "application/zip" });
        parts.push(step.value);
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
    return swapSheet("failure");
  }

  function pack() {
    if (current) current.abort();
    var controller = new AbortController();
    current = controller;
    var timer = setTimeout(function () { controller.abort(); }, 130000);
    swapSheet("packing").then(function () {
      return fetch("/api/export/my-photos.zip", {
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal
      });
    }).then(readZip).then(function (blob) {
      clearTimeout(timer);
      if (current !== controller) return;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      blobUrl = URL.createObjectURL(blob);
      return swapSheet("success");
    }).catch(function () {
      clearTimeout(timer);
      if (current !== controller) return;
      return failSheet();
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

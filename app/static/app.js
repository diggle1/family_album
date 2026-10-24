(function () {
  var toast = document.getElementById("toast");
  if (toast && !toast.classList.contains("top")) {
    setTimeout(function () { toast.remove(); }, 2000);
  }

  function showToast(text, ms, top, onClick) {
    if (!text) return;
    var old = document.getElementById("toast");
    if (old) old.remove();
    var host = document.querySelector(".app") || document.body;
    var el = document.createElement("div");
    el.className = top ? "toast top" : "toast";
    el.id = "toast";
    el.textContent = text;
    if (onClick) {
      el.addEventListener("click", function () {
        onClick();
        if (el.parentNode) el.remove();
      });
    }
    host.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, ms || 2000);
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
  var phase = "confirm";
  var userStop = false;
  var sawHeaders = false;
  var receipt = null;
  var allMissingBody = null;
  var lastProgress = null;
  var iframeEl = null;
  var activeDl = "";
  var trapped = false;
  var ignorePop = false;
  var bigTimers = [];
  // Headers only arrive after the server finishes the temp zip (up to 120s).
  var HEADER_WAIT_MS = 130000;
  // After headers, fail when no new bytes arrive for this long.
  var READ_STALL_MS = 15000;
  var BLOB_MAX_BYTES = 209715200;

  function maskEl() {
    return document.getElementById("export-mask");
  }

  function copy(name) {
    var mask = maskEl();
    if (!mask || !name) return "";
    return mask.getAttribute("data-copy-" + name) || "";
  }

  function headerWaitMs() {
    var mask = maskEl();
    var raw = mask && mask.getAttribute("data-header-wait-ms");
    var n = raw ? Number(raw) : HEADER_WAIT_MS;
    if (!isFinite(n) || n <= 0) return HEADER_WAIT_MS;
    return n;
  }

  function blobMaxBytes() {
    var mask = maskEl();
    var raw = mask && mask.getAttribute("data-blob-max-bytes");
    var n = raw ? Number(raw) : BLOB_MAX_BYTES;
    if (!isFinite(n) || n < 0) return BLOB_MAX_BYTES;
    return n;
  }

  function maskVisible() {
    var mask = maskEl();
    return !!(mask && !mask.hasAttribute("hidden"));
  }

  function showMask() {
    var mask = maskEl();
    if (mask) mask.removeAttribute("hidden");
  }

  function hideMask() {
    var mask = maskEl();
    if (mask) mask.setAttribute("hidden", "");
  }

  function showTemplate(id) {
    var target = document.getElementById("export-sheet");
    var tpl = document.getElementById(id);
    if (!target || !tpl || !tpl.content) return false;
    target.replaceChildren(tpl.content.cloneNode(true));
    return true;
  }

  function localDate() {
    var now = new Date();
    var month = String(now.getMonth() + 1);
    var day = String(now.getDate());
    if (month.length < 2) month = "0" + month;
    if (day.length < 2) day = "0" + day;
    return now.getFullYear() + "-" + month + "-" + day;
  }

  function filenameFor(day) {
    return (copy("filename") || "kids-shiguangji-my-photos.zip").replace("{date}", day);
  }

  function isIos() {
    var ua = navigator.userAgent || "";
    if (/iPhone|iPad|iPod/.test(ua)) return true;
    return /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;
  }

  function isMobile() {
    var ua = navigator.userAgent || "";
    if (/Android|iPhone|iPad|iPod|Mobile/.test(ua)) return true;
    return /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;
  }

  function whereText() {
    return isMobile() ? copy("where-mobile") : copy("where-desktop");
  }

  function fmtMb(bytes) {
    var mb = (bytes || 0) / 1048576;
    if (mb >= 10) return String(Math.round(mb));
    return mb.toFixed(1);
  }

  function fmtSize(bytes) {
    var mb = (bytes || 0) / 1048576;
    if (mb >= 10) return String(Math.round(mb)) + " MB";
    if (mb >= 0.1) return mb.toFixed(1) + " MB";
    return String(Math.max(1, Math.round((bytes || 0) / 1024))) + " KB";
  }

  function setRow(kind, extra) {
    var node = document.getElementById("export-row-sub");
    if (!node) return;
    extra = extra || {};
    var key = {
      idle: "row-idle",
      closed: "row-closed",
      downloading: "row-downloading",
      done: "row-done",
      failed: "row-failed",
      pack: "row-failed-pack",
      pack_retry: "row-failed-pack-retry"
    }[kind];
    var text = copy(key);
    if (kind === "closed") text = text.replace("{md}", extra.md || "");
    if (kind === "downloading") text = text.replace("{p}", extra.p || "0");
    node.textContent = text;
    node.classList.remove("live", "done");
    if (kind === "done" || kind === "closed") node.classList.add("done");
    if (kind === "downloading" || kind === "failed" || kind === "pack" || kind === "pack_retry") {
      node.classList.add("live");
    }
  }

  function rememberDownload() {
    var mask = maskEl();
    if (!mask) return;
    var uid = mask.getAttribute("data-uid") || "";
    try {
      localStorage.setItem("fa.export.last." + uid, localDate());
    } catch (e) { /* private mode */ }
  }

  function readStoredDate(uid) {
    try {
      return localStorage.getItem("fa.export.last." + uid) || "";
    } catch (e) {
      return "";
    }
  }

  function applyStoredRow() {
    var mask = maskEl();
    if (!mask) return;
    var raw = readStoredDate(mask.getAttribute("data-uid") || "");
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) setRow("closed", { md: raw.slice(5) });
  }

  function bootSettingsRow() {
    var entry = document.getElementById("export-entry");
    if (!entry) return;
    var raw = readStoredDate(entry.getAttribute("data-uid") || "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return;
    var tpl = entry.getAttribute("data-copy-closed") || "";
    var small = entry.querySelector("small");
    if (!small) return;
    small.textContent = tpl.replace("{md}", raw.slice(5));
    small.classList.add("done");
  }

  function triggerDownload(url, name) {
    var link = document.createElement("a");
    link.href = url;
    link.download = name || "kids-shiguangji-my-photos.zip";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  function dropIframe() {
    bigTimers.forEach(function (id) { clearTimeout(id); clearInterval(id); });
    bigTimers = [];
    if (iframeEl && iframeEl.parentNode) iframeEl.parentNode.removeChild(iframeEl);
    iframeEl = null;
  }

  function invalidateDl() {
    activeDl = "";
    dropIframe();
  }

  function shake() {
    var sheet = document.querySelector("#export-sheet .sheet");
    if (!sheet) return;
    sheet.classList.remove("shake");
    void sheet.offsetWidth;
    sheet.classList.add("shake");
  }

  function armBack() {
    if (trapped) return;
    try {
      history.pushState({ exportPack: 1 }, "", location.href);
      trapped = true;
    } catch (e) { /* ignore */ }
  }

  function disarmBack() {
    if (!trapped) return;
    ignorePop = true;
    trapped = false;
    try { history.back(); } catch (e) { ignorePop = false; }
  }

  window.addEventListener("popstate", function () {
    if (ignorePop) {
      ignorePop = false;
      return;
    }
    if (phase === "packing" || phase === "stopping") {
      try { history.pushState({ exportPack: 1 }, "", location.href); } catch (e) { /* ignore */ }
      trapped = true;
      shake();
    }
  });

  window.addEventListener("pagehide", function () {
    if (blobUrl) {
      URL.revokeObjectURL(blobUrl);
      blobUrl = null;
    }
    dropIframe();
  });

  function skipLengthCheck(response) {
    var encoding = (response.headers.get("content-encoding") || "").toLowerCase();
    return encoding !== "" && encoding !== "identity";
  }

  function isBig(response) {
    if (skipLengthCheck(response)) return true;
    var raw = response.headers.get("content-length");
    if (raw === null || raw === "") return true;
    var length = Number(raw);
    if (!isFinite(length) || length <= 0) return true;
    return length > blobMaxBytes();
  }

  function readPacked(response) {
    var raw = response.headers.get("x-export-packed");
    if (raw && /^[1-9]\d*$/.test(raw)) return parseInt(raw, 10);
    return null;
  }

  function readMissing(response) {
    var raw = response.headers.get("x-export-missing");
    if (!raw) return null;
    try {
      var pad = raw.length % 4 === 0 ? "" : "====".slice(raw.length % 4);
      var b64 = raw.replace(/-/g, "+").replace(/_/g, "/") + pad;
      var binary = atob(b64);
      var bytes = new Uint8Array(binary.length);
      for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      var data = JSON.parse(new TextDecoder().decode(bytes));
      if (!data || !Array.isArray(data.items)) return null;
      return data;
    } catch (e) {
      return null;
    }
  }

  function updateProgress(received, total) {
    var pct = 0;
    if (total && total > 0) pct = Math.min(100, Math.round(received * 100 / total));
    var text = copy("dl-meta").replace("{a}", fmtMb(received)).replace("{b}", fmtMb(total || 0));
    text = text + " \u00b7 " + pct + "%";
    lastProgress = { text: text, pct: pct };
    var node = document.querySelector("[data-export-dl-meta]");
    if (node) node.textContent = text;
    var bar = document.querySelector("[data-export-bar]");
    if (bar) bar.style.width = pct + "%";
    if (phase === "downloading") setRow("downloading", { p: String(pct) });
  }

  function applyProgress() {
    if (!lastProgress) return;
    var node = document.querySelector("[data-export-dl-meta]");
    var bar = document.querySelector("[data-export-bar]");
    if (node) node.textContent = lastProgress.text;
    if (bar) bar.style.width = lastProgress.pct + "%";
  }

  function fillMissing(host, data) {
    if (!host || !data) return;
    host.replaceChildren();
    var items = Array.isArray(data.items) ? data.items.slice(0, 20) : [];
    var total = typeof data.total === "number" ? data.total : items.length;
    var more = Math.max(0, total - items.length);
    var box = document.createElement("div");
    box.className = "notice";
    var title = document.createElement("b");
    title.textContent = copy("miss-head").replace("{k}", String(total));
    var body = document.createElement("div");
    body.textContent = copy("miss-body");
    var details = document.createElement("details");
    if (items.length <= 3) details.open = true;
    var summary = document.createElement("summary");
    summary.textContent = copy("miss-toggle");
    details.appendChild(summary);
    items.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "miss";
      var thumb = document.createElement("div");
      thumb.className = "th";
      var label = document.createElement("div");
      label.textContent = (item.date || "") + " \u00b7 " + (item.owner || "");
      var small = document.createElement("small");
      small.textContent = copy("miss-item-sub");
      label.appendChild(small);
      row.appendChild(thumb);
      row.appendChild(label);
      details.appendChild(row);
    });
    if (more) {
      var extra = document.createElement("div");
      extra.className = "miss";
      extra.textContent = copy("miss-more").replace("{m}", String(more));
      details.appendChild(extra);
    }
    box.appendChild(title);
    box.appendChild(body);
    box.appendChild(details);
    host.appendChild(box);
  }

  function renderSuccess() {
    if (!showTemplate("export-success-tpl") || !receipt) return;
    var title = document.querySelector("[data-export-ok-title]");
    var sub = document.querySelector("[data-export-ok-sub]");
    var missing = receipt.missing;
    var packed = receipt.packed;
    if (title) {
      if (missing && packed) title.textContent = copy("ok-title-partial").replace("{n}", String(packed));
      else title.textContent = copy("ok-title");
    }
    if (sub) {
      if (!missing && packed) sub.textContent = copy("ok-sub").replace("{n}", String(packed));
      else sub.textContent = "";
    }
    var name = document.querySelector("[data-export-filename]");
    if (name) name.textContent = receipt.filename;
    var cell = document.querySelector("[data-export-count-cell]");
    var countNode = document.querySelector("[data-export-count]");
    if (packed && countNode && cell) {
      countNode.textContent = String(packed);
      cell.hidden = false;
    } else if (cell) {
      cell.hidden = true;
    }
    var size = document.querySelector("[data-export-size]");
    if (size) size.textContent = fmtSize(receipt.size);
    var where = document.querySelector("[data-export-where]");
    if (where) where.textContent = receipt.where;
    if (missing) {
      fillMissing(document.querySelector("[data-export-miss]"), missing);
      var note = document.querySelector("[data-export-miss-txt]");
      if (note) note.hidden = false;
    }
    var ios = document.querySelector("[data-export-ios]");
    if (ios && isIos()) ios.hidden = false;
  }

  function renderAllMissing() {
    if (!showTemplate("export-allmissing-tpl")) return;
    var sub = document.querySelector("[data-export-allmiss-sub]");
    var total = allMissingBody && typeof allMissingBody.total === "number" ? allMissingBody.total : 0;
    if (sub) sub.textContent = copy("allmiss-sub").replace("{n}", String(total));
    fillMissing(document.querySelector("[data-export-miss]"), allMissingBody || { total: total, items: [] });
  }

  function finishPackFailed() {
    if (phase === "pack_failed") return;
    phase = "pack_failed";
    userStop = false;
    sawHeaders = false;
    disarmBack();
    dropIframe();
    showMask();
    showTemplate("export-failure-tpl");
    setRow("pack_retry");
  }

  function finishFailed() {
    if (phase === "failed") return;
    phase = "failed";
    userStop = false;
    disarmBack();
    if (maskVisible()) {
      showTemplate("export-dlfail-tpl");
      applyProgress();
    }
    setRow("failed");
  }

  function readZip(response, controller) {
    var skip = skipLengthCheck(response);
    var rawLength = response.headers.get("content-length");
    var expectedSize = rawLength === null ? null : Number(rawLength);
    receipt = {
      packed: readPacked(response),
      missing: readMissing(response),
      size: 0,
      filename: filenameFor(localDate()),
      where: whereText()
    };
    if (!response.body || !response.body.getReader) {
      return response.blob().then(function (blob) {
        if (!blob || !blob.size) throw new Error("body");
        if (!skip && expectedSize !== null && blob.size !== expectedSize) throw new Error("body");
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
        if (current !== controller) throw new Error("stale");
        if (step.done) {
          if (!received) throw new Error("short");
          if (!skip && expectedSize !== null && received !== expectedSize) throw new Error("short");
          var blob = new Blob(parts, { type: "application/zip" });
          parts = null;
          return blob;
        }
        parts.push(step.value);
        received += step.value.byteLength || step.value.length || 0;
        updateProgress(received, skip ? null : expectedSize);
        return pump();
      });
    }
    updateProgress(0, skip ? null : expectedSize);
    return pump();
  }

  function finishBlob(blob) {
    if (!receipt) return;
    phase = receipt.missing ? "partial" : "success";
    disarmBack();
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    blobUrl = URL.createObjectURL(blob);
    receipt.size = blob.size;
    triggerDownload(blobUrl, receipt.filename);
    rememberDownload();
    setRow("done");
    if (maskVisible()) renderSuccess();
    else {
      showToast(copy("toast-bg-done").replace("{where}", whereText()), 4000, true, function () {
        showMask();
        renderSuccess();
      });
    }
  }

  function randomDl() {
    var bytes = new Uint8Array(16);
    if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(bytes);
    else for (var i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    var hex = "";
    for (var j = 0; j < bytes.length; j++) {
      var h = bytes[j].toString(16);
      hex += h.length === 1 ? "0" + h : h;
    }
    return hex;
  }

  function readIframeJson(frame) {
    try {
      var doc = frame.contentDocument;
      if (!doc || !doc.body) return null;
      var text = (doc.body.textContent || "").trim();
      if (!text) return null;
      return JSON.parse(text);
    } catch (e) {
      return null;
    }
  }

  function showBusy() {
    phase = "confirm";
    disarmBack();
    dropIframe();
    showMask();
    showTemplate("export-confirm-tpl");
    var node = document.querySelector("[data-export-busy]");
    if (node) {
      node.textContent = copy("busy");
      node.hidden = false;
    }
    showToast(copy("busy"), 3000, true);
  }

  function showAllMissing(body) {
    phase = "all_missing";
    allMissingBody = body || { total: 0, items: [] };
    disarmBack();
    dropIframe();
    setRow("pack");
    showMask();
    renderAllMissing();
  }

  function openIframe() {
    if (userStop || phase === "stopping" || phase === "confirm") return;
    dropIframe();
    var dl = randomDl();
    activeDl = dl;
    var frame = document.createElement("iframe");
    frame.hidden = true;
    frame.setAttribute("aria-hidden", "true");
    frame.src = "/api/export/my-photos.zip?dl=" + dl + "&d=" + encodeURIComponent(localDate());
    iframeEl = frame;
    var settled = false;
    function still() {
      return activeDl === dl;
    }
    function settleCookie() {
      if (!still() || settled) return;
      if (document.cookie.indexOf("fa_dl=" + dl) === -1) return;
      settled = true;
      dropTimersOnly();
      document.cookie = "fa_dl=; Path=/; Max-Age=0; SameSite=Strict";
      phase = "big";
      disarmBack();
      showMask();
      showTemplate("export-big-tpl");
      setRow("idle");
    }
    function dropTimersOnly() {
      bigTimers.forEach(function (id) { clearTimeout(id); clearInterval(id); });
      bigTimers = [];
    }
    function applyIframeBody() {
      if (!still() || settled) return;
      var data = readIframeJson(frame);
      if (!data || !data.detail) return false;
      settled = true;
      dropTimersOnly();
      if (data.detail === "no_photos") {
        window.location.reload();
        return true;
      }
      if (data.detail === "export_busy") {
        showBusy();
        return true;
      }
      if (data.detail === "all_missing") {
        showAllMissing(data);
        return true;
      }
      finishPackFailed();
      return true;
    }
    var poll = setInterval(settleCookie, 500);
    var timer = setTimeout(function () {
      if (!still() || settled) return;
      if (applyIframeBody()) return;
      settled = true;
      dropTimersOnly();
      finishPackFailed();
    }, headerWaitMs());
    bigTimers.push(poll, timer);
    frame.addEventListener("load", function () {
      applyIframeBody();
    });
    document.body.appendChild(frame);
  }

  function startBig(controller) {
    controller._ignore = true;
    try { controller.abort(); } catch (e) { /* already aborted */ }
    var timer = setTimeout(function () { finishPackFailed(); }, 5000);
    fetch("/api/export/cancel", { method: "POST", credentials: "same-origin" }).then(function (res) {
      return res.json();
    }).then(function (body) {
      clearTimeout(timer);
      if (userStop || phase === "stopping" || phase === "confirm") return;
      if (!body || body.stopped !== true) {
        finishPackFailed();
        return;
      }
      openIframe();
    }, function () {
      clearTimeout(timer);
      if (userStop || phase === "stopping" || phase === "confirm") return;
      finishPackFailed();
    });
  }

  function handleResponse(response, controller) {
    if (current !== controller || phase === "stopping") return;
    if (response.status === 409) {
      showBusy();
      return;
    }
    if (response.status === 422) {
      return response.json().then(function (body) {
        if (body && body.detail === "all_missing") showAllMissing(body);
        else finishPackFailed();
      }, function () { finishPackFailed(); });
    }
    if (response.status === 404) {
      window.location.reload();
      return;
    }
    if (!response.ok) {
      finishPackFailed();
      return;
    }
    if (isBig(response)) {
      startBig(controller);
      return;
    }
    sawHeaders = true;
    phase = "downloading";
    disarmBack();
    showMask();
    showTemplate("export-downloading-tpl");
    return readZip(response, controller).then(function (blob) {
      if (current !== controller || !blob) return;
      finishBlob(blob);
    });
  }

  function onOffline() {
    if (!current) return;
    if (phase === "packing") {
      current._ignore = true;
      try { current.abort(); } catch (e) { /* already aborted */ }
      finishPackFailed();
    } else if (phase === "downloading") {
      current._ignore = true;
      try { current.abort(); } catch (e) { /* already aborted */ }
      finishFailed();
    }
  }

  function pack() {
    if (!maskEl()) return;
    userStop = false;
    sawHeaders = false;
    lastProgress = null;
    if (blobUrl) {
      URL.revokeObjectURL(blobUrl);
      blobUrl = null;
    }
    dropIframe();
    if (current) {
      current._ignore = true;
      try { current.abort(); } catch (e) { /* already aborted */ }
    }
    var controller = new AbortController();
    current = controller;
    phase = "packing";
    showMask();
    if (!showTemplate("export-packing-tpl")) {
      finishPackFailed();
      return;
    }
    armBack();
    if (navigator.onLine === false) {
      finishPackFailed();
      return;
    }
    window.addEventListener("offline", onOffline);
    var headerTimer = setTimeout(function () {
      if (current !== controller || phase !== "packing") return;
      controller._ignore = true;
      try { controller.abort(); } catch (e) { /* already aborted */ }
      finishPackFailed();
    }, headerWaitMs());
    var url = "/api/export/my-photos.zip?d=" + encodeURIComponent(localDate());
    fetch(url, { credentials: "same-origin", cache: "no-store", signal: controller.signal }).then(function (response) {
      clearTimeout(headerTimer);
      window.removeEventListener("offline", onOffline);
      if (current !== controller) return;
      return handleResponse(response, controller);
    }).catch(function () {
      clearTimeout(headerTimer);
      window.removeEventListener("offline", onOffline);
      if (current !== controller || controller._ignore || userStop) return;
      if (phase === "stopping" || phase === "pack_failed" || phase === "failed" || phase === "big") return;
      if (sawHeaders) finishFailed();
      else finishPackFailed();
    });
  }

  function restoreConfirm() {
    phase = "confirm";
    userStop = false;
    sawHeaders = false;
    disarmBack();
    showMask();
    showTemplate("export-confirm-tpl");
    setRow("idle");
    showToast(copy("toast-cancelled"), 3000, true);
  }

  function cancelPack() {
    if (phase === "big") {
      invalidateDl();
      restoreConfirm();
      fetch("/api/export/cancel", { method: "POST", credentials: "same-origin" }).then(function () {}, function () {});
      return;
    }
    if (phase !== "packing") return;
    userStop = true;
    phase = "stopping";
    invalidateDl();
    var btn = document.querySelector("[data-export-action='cancel']");
    if (btn) {
      btn.textContent = copy("stopping");
      btn.disabled = true;
    }
    if (current) {
      current._ignore = true;
      try { current.abort(); } catch (e) { /* already aborted */ }
    }
    var timer = setTimeout(function () { finishPackFailed(); }, 5000);
    fetch("/api/export/cancel", { method: "POST", credentials: "same-origin" }).then(function (res) {
      return res.json();
    }).then(function (body) {
      clearTimeout(timer);
      if (body && body.stopped === true) {
        restoreConfirm();
      } else if (phase === "stopping") {
        finishPackFailed();
      }
    }, function () {
      clearTimeout(timer);
      finishPackFailed();
    });
  }

  function onRow() {
    if (phase === "packing" || phase === "stopping") {
      shake();
      return;
    }
    if (phase === "downloading") {
      showMask();
      showTemplate("export-downloading-tpl");
      applyProgress();
      return;
    }
    if ((phase === "success" || phase === "partial") && blobUrl) {
      showMask();
      renderSuccess();
      return;
    }
    if (phase === "failed" || phase === "pack_failed") {
      pack();
      return;
    }
    if (phase === "all_missing") {
      showMask();
      renderAllMissing();
      return;
    }
    phase = "confirm";
    showMask();
    showTemplate("export-confirm-tpl");
  }

  function bindChoose(form) {
    var btn = form.querySelector("#continue");
    if (!btn) return;
    function sync() {
      var on = false;
      form.querySelectorAll("input[name=kind]").forEach(function (radio) {
        if (radio.checked) on = true;
      });
      btn.disabled = !on;
    }
    form.querySelectorAll("input[name=kind]").forEach(function (radio) {
      radio.addEventListener("change", sync);
    });
    sync();
  }

  document.querySelectorAll("#choose").forEach(bindChoose);
  bootSettingsRow();
  applyStoredRow();

  document.body.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.getAttribute) return;
    if (form.id === "choose" && form.hasAttribute("data-inplace")) {
      event.preventDefault();
      var chosen = "";
      form.querySelectorAll("input[name=kind]").forEach(function (radio) {
        if (radio.checked) chosen = radio.value;
      });
      if (!chosen) return;
      var fileForm = document.getElementById("upload-file");
      if (!fileForm) return;
      var kindInput = fileForm.querySelector("[name=kind]");
      if (kindInput) kindInput.value = chosen;
      fileForm.querySelectorAll("[name=child_id]").forEach(function (field) {
        field.disabled = chosen === "self";
      });
      form.hidden = true;
      fileForm.hidden = false;
      return;
    }
    if (form.id === "upload-file") {
      event.preventDefault();
      var body = new FormData(form);
      fetch("/api/photos", { method: "POST", body: body, credentials: "same-origin" }).then(function (res) {
        if (res.status === 201) {
          window.location.reload();
          return;
        }
        var note = form.querySelector("[data-upload-failed]");
        if (note) note.hidden = false;
      }, function () {
        var note = form.querySelector("[data-upload-failed]");
        if (note) note.hidden = false;
      });
    }
  });

  document.body.addEventListener("click", function (event) {
    var uploadOpen = event.target.closest("[data-export-upload]");
    if (uploadOpen) {
      event.preventDefault();
      var uploadMask = document.getElementById("upload-mask");
      if (uploadMask) uploadMask.hidden = false;
      return;
    }
    var uploadClose = event.target.closest("[data-upload-close]");
    if (uploadClose) {
      event.preventDefault();
      var uploadSheet = document.getElementById("upload-mask");
      if (uploadSheet) uploadSheet.hidden = true;
      return;
    }

    var mask = maskEl();
    if (mask && event.target === mask) {
      if (phase === "packing" || phase === "stopping") {
        shake();
        return;
      }
      if (phase === "confirm") {
        window.location.href = "/family/settings";
        return;
      }
      if (phase === "downloading") {
        hideMask();
        return;
      }
      hideMask();
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
      if (action === "cancel") {
        event.preventDefault();
        cancelPack();
        return;
      }
    }

    var collapse = event.target.closest("[data-export-collapse]");
    if (collapse) {
      event.preventDefault();
      hideMask();
      return;
    }

    var close = event.target.closest("[data-export-close]");
    if (close) {
      event.preventDefault();
      hideMask();
      return;
    }

    var save = event.target.closest("[data-export-save]");
    if (save) {
      event.preventDefault();
      if (blobUrl) triggerDownload(blobUrl, receipt && receipt.filename);
      return;
    }

    var row = event.target.closest("[data-export-row]");
    if (row) {
      event.preventDefault();
      onRow();
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
      event.preventDefault();
      if (menu) menu.setAttribute("hidden", "");
      var fail = original.getAttribute("data-save-fail") || "";
      var href = original.getAttribute("href");
      var name = original.getAttribute("download") || "photo";
      if (navigator.onLine === false) {
        showToast(fail, 4000);
        return;
      }
      fetch(href, { credentials: "same-origin", cache: "no-store" }).then(function (res) {
        if (!res.ok) throw new Error("status");
        return res.blob();
      }).then(function (blob) {
        var url = URL.createObjectURL(blob);
        triggerDownload(url, name);
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
        showToast(original.getAttribute("data-save-original"), 4000);
      }).catch(function () {
        showToast(fail, 4000);
      });
    }
  });
})();

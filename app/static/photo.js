/* Photo film-strip client. User-facing sentences come from data-copy-* only. */
(function () {
  function boot() {
    var root = document.getElementById("photo-app");
    if (!root) return;
    var selectedNode = document.getElementById("photo-selected");
    var childrenNode = document.getElementById("photo-children");
    var selected = [];
    var children = [];
    try {
      selected = JSON.parse(selectedNode ? selectedNode.textContent || "[]" : "[]").map(String);
    } catch (err) {
      selected = [];
    }
    try {
      children = JSON.parse(childrenNode ? childrenNode.textContent || "[]" : "[]");
    } catch (err) {
      children = [];
    }
    var state = { file: null, xhr: null, aborted: false, busy: false, lastHint: "" };

    function copy(key) {
      return root.getAttribute("data-copy-" + key) || "";
    }
    function fill(key, map) {
      return copy(key).replace(/\{(\w+)\}/g, function (_, name) {
        return map && Object.prototype.hasOwnProperty.call(map, name) ? map[name] : "{" + name + "}";
      });
    }
    function retryText() {
      return root.getAttribute("data-retry-text") || "";
    }
    function kind() {
      return root.getAttribute("data-kind") || "";
    }
    function mode() {
      return root.getAttribute("data-mode") || "new";
    }
    function findChild(id) {
      for (var i = 0; i < children.length; i += 1) {
        if (String(children[i].id) === String(id)) return children[i];
      }
      return null;
    }
    function showError(text) {
      var node = document.getElementById("photo-error");
      if (!node) return;
      node.hidden = !text;
      node.textContent = text || "";
    }
    function paintHint() {
      var hint = document.getElementById("strip-hint");
      if (!hint || kind() !== "child") return;
      if (root.getAttribute("data-single") === "1") {
        hint.textContent = copy("strip_hint_single");
        return;
      }
      if (state.lastHint) {
        hint.textContent = state.lastHint;
        return;
      }
      if (!selected.length) {
        hint.textContent = copy("strip_hint_pick");
        return;
      }
      var named = findChild(selected[0]);
      var pre = root.getAttribute("data-pre-name") || (named ? named.name : "");
      hint.textContent = fill("strip_hint_pre", { name: pre || (named ? named.name : "") });
    }
    function paintStickers() {
      var row = document.getElementById("sticker-row");
      if (!row) return;
      var nodes = row.children || [];
      for (var i = 0; i < nodes.length; i += 1) {
        var node = nodes[i];
        if (!node.getAttribute) continue;
        var id = node.getAttribute("data-child-id");
        if (!id) continue;
        var on = selected.indexOf(String(id)) !== -1;
        node.className = on ? "sticker on" : "sticker off";
      }
    }
    function paintButton() {
      var btn = document.getElementById("photo-submit");
      if (!btn) return;
      var needsChild = kind() === "child";
      var needsFile = mode() !== "edit";
      var blocked = state.busy || (needsChild && !selected.length) || (needsFile && !state.file);
      btn.disabled = blocked;
      if (btn.classList) btn.classList.toggle("dis", blocked);
      if (!state.busy) {
        if (mode() === "edit") btn.textContent = copy("u_submit_edit");
        else if (kind() === "self") btn.textContent = copy("u_submit_self");
        else btn.textContent = copy("u_submit");
      }
    }
    function paintBar() {
      var bar = document.getElementById("redbar");
      if (!bar) return;
      var empty = kind() === "child" && !selected.length;
      bar.hidden = !empty;
      bar.textContent = empty ? copy("err_no_child") : "";
    }
    function paint() {
      paintHint();
      paintStickers();
      paintBar();
      paintButton();
    }
    function toggle(raw) {
      if (root.getAttribute("data-single") === "1") return;
      var id = String(raw);
      var index = selected.indexOf(id);
      state.lastHint = "";
      if (index === -1) selected.push(id);
      else if (selected.length <= 1) state.lastHint = copy("strip_hint_last");
      else selected.splice(index, 1);
      showError("");
      paint();
    }
    function shortDate(iso) {
      var parts = String(iso || "").split("-");
      if (parts.length !== 3) return "";
      return fill("date_short", {
        mo: String(parseInt(parts[1], 10)),
        d: String(parseInt(parts[2], 10)),
      });
    }
    function dateValue() {
      var input = document.getElementById("photo-date");
      if (input && input.value) return input.value;
      return root.getAttribute("data-today") || "";
    }
    function mapError(status, data) {
      if (status === 422) return { text: copy("err_no_child"), retry: true };
      if (!status || status >= 500) return { text: retryText(), retry: true };
      if (status === 404 && !(data && data.code)) return { text: retryText(), retry: true };
      var code = data && data.code;
      if (code === "date_before_birth" && data.detail) return { text: data.detail, retry: true };
      if (code === "photo_forbidden_role") return { text: copy("err_demoted"), retry: false };
      if (code === "photo_not_author") return { text: copy("e_403"), retry: false };
      if (code === "child_required") return { text: copy("err_no_child"), retry: true };
      if (code === "child_not_found" || code === "children_too_many") return { text: copy("e_kid_gone"), retry: true };
      if (status === 403 && !(data && data.code)) return { text: copy("e_removed"), retry: false, checkMe: true };
      return { text: (data && data.detail) || retryText(), retry: true };
    }
    function afterForbidden(mapped) {
      if (!mapped.checkMe) return;
      if (typeof fetch === "function") {
        fetch("/api/me", { credentials: "same-origin" }).catch(function () {});
      }
    }
    function succeed() {
      if (state.aborted) return;
      if (mode() === "edit") {
        window.location.href = "/photos/" + encodeURIComponent(root.getAttribute("data-photo-id") || "") + "?toast=photo_edited";
        return;
      }
      if (kind() === "self") {
        window.location.href = "/people/" + encodeURIComponent(root.getAttribute("data-uid") || "") + "/album?toast=album";
        return;
      }
      var stamp = shortDate(dateValue());
      var child = selected.length ? selected[0] : "";
      window.location.href = "/?child=" + encodeURIComponent(child) + "&toast=photo_posted&pdate=" + encodeURIComponent(stamp);
    }
    function finish(status, data) {
      state.busy = false;
      state.xhr = null;
      if (state.aborted) return;
      if (status >= 200 && status < 300) {
        succeed();
        return;
      }
      var mapped = mapError(status, data);
      showError(mapped.text);
      afterForbidden(mapped);
      paintButton();
    }
    function send(method, url, body, jsonBody) {
      state.aborted = false;
      state.busy = true;
      paintButton();
      var btn = document.getElementById("photo-submit");
      if (btn) btn.textContent = copy("u_sending");
      var xhr = new XMLHttpRequest();
      state.xhr = xhr;
      xhr.open(method, url);
      xhr.onload = function () {
        if (state.aborted) return;
        var data = {};
        try { data = JSON.parse(xhr.responseText || "{}"); } catch (err) { data = {}; }
        finish(xhr.status, data);
      };
      xhr.onerror = function () {
        if (state.aborted) return;
        finish(0, {});
      };
      xhr.onabort = function () {
        state.busy = false;
        state.xhr = null;
        if (!state.aborted) showError(retryText());
        paintButton();
      };
      if (jsonBody) {
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.send(JSON.stringify(body));
      } else {
        xhr.send(body);
      }
    }
    function submit() {
      if (state.busy) return;
      if (kind() === "child" && !selected.length) {
        showError(copy("err_no_child"));
        paint();
        return;
      }
      if (mode() === "edit") {
        send("PATCH", "/api/photos/" + encodeURIComponent(root.getAttribute("data-photo-id") || ""), {
          child_ids: selected.map(function (id) { return parseInt(id, 10); }),
        }, true);
        return;
      }
      if (!state.file) return;
      var form = new FormData();
      form.append("kind", kind());
      form.append("taken_on", dateValue());
      form.append("file", state.file);
      if (kind() === "child") {
        selected.forEach(function (id) { form.append("child_ids", id); });
      }
      send("POST", "/api/photos", form, false);
    }
    function abortInFlight() {
      state.aborted = true;
      if (state.xhr) {
        try { state.xhr.abort(); } catch (err) { /* already finished */ }
      }
    }
    function cancel() {
      abortInFlight();
      window.location.href = root.getAttribute("data-back") || "/";
    }
    var row = document.getElementById("sticker-row");
    if (row) {
      row.addEventListener("click", function (event) {
        var node = event.target;
        while (node && node !== row) {
          if (node.getAttribute && node.getAttribute("data-child-id")) {
            toggle(node.getAttribute("data-child-id"));
            return;
          }
          node = node.parentNode;
        }
      });
    }
    var preview = document.getElementById("photo-preview");
    var fileInput = document.getElementById("photo-file");
    if (preview && fileInput) {
      preview.addEventListener("click", function () { fileInput.click(); });
      fileInput.addEventListener("change", function () {
        state.file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
        var img = document.getElementById("photo-img");
        var badge = document.getElementById("picked-badge");
        var placeholder = document.getElementById("photo-ph");
        if (state.file && img) {
          img.hidden = false;
          img.src = URL.createObjectURL(state.file);
          if (placeholder) placeholder.hidden = true;
          if (badge) badge.hidden = false;
        }
        paintButton();
      });
    }
    var submitBtn = document.getElementById("photo-submit");
    if (submitBtn) submitBtn.addEventListener("click", submit);
    var cancelBtn = document.getElementById("photo-cancel");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", function (event) {
        if (event && event.preventDefault) event.preventDefault();
        cancel();
      });
    }
    var closeBtn = document.getElementById("photo-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", function (event) {
        if (event && event.preventDefault) event.preventDefault();
        cancel();
      });
    }
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") abortInFlight();
    });
    window.addEventListener("pagehide", function () { abortInFlight(); });
    window.addEventListener("beforeunload", function () { abortInFlight(); });
    paint();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();

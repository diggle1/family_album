/* Old-photo tray. Sentences come from data-copy-* only. One upload at a time. */
(function (global) {
  function batchBlock(count, totalBytes, limits) {
    if (!limits || !(count >= 1)) return "empty";
    if (count > limits.maxFiles) return "over50";
    if (totalBytes > limits.maxBatch) return "batch";
    return "";
  }

  function fileSkip(size, limits) {
    if (size > limits.maxFile) return "mb";
    return "";
  }

  function toggleChild(selected, id, single) {
    var next = selected.map(String);
    if (single) return next;
    var key = String(id);
    var index = next.indexOf(key);
    if (index === -1) {
      next.push(key);
      return next;
    }
    if (next.length <= 1) return next;
    next.splice(index, 1);
    return next;
  }

  function retryable(status, data) {
    if (!status || status >= 500) return true;
    if (status === 404 && !(data && data.code)) return true;
    return false;
  }

  function whyKey(code) {
    if (code === "legacy_import_file_too_large") return "fail_why_mb";
    if (code === "legacy_import_edge_too_large") return "fail_why_edge";
    if (code === "legacy_import_format") return "fail_why_fmt";
    if (code === "legacy_import_corrupt") return "fail_why_bad";
    return "";
  }

  function finishFrame(ok, failCount, total, cancelled) {
    if (!cancelled && failCount === 0 && total > 0 && ok === total) return "done_ok";
    return "done_partial";
  }

  function acceptLate(epoch, current) {
    return epoch === current;
  }

  function runSerial(items, worker) {
    var index = 0;
    function step() {
      if (index >= items.length) return Promise.resolve();
      var current = items[index];
      var place = index;
      index += 1;
      return Promise.resolve()
        .then(function () { return worker(current, place); })
        .then(step);
    }
    return step();
  }

  var api = {
    batchBlock: batchBlock,
    fileSkip: fileSkip,
    toggleChild: toggleChild,
    retryable: retryable,
    whyKey: whyKey,
    finishFrame: finishFrame,
    acceptLate: acceptLate,
    runSerial: runSerial
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (!global.document) return;

  function boot() {
    var root = document.getElementById("legacy-import");
    if (!root) return;
    var selectedNode = document.getElementById("li-selected");
    var initial = [];
    try {
      initial = JSON.parse(selectedNode ? selectedNode.textContent || "[]" : "[]").map(String);
    } catch (err) {
      initial = [];
    }
    var state = {
      files: [],
      urls: [],
      selected: initial.slice(),
      date: "",
      ok: 0,
      fails: [],
      cancelled: false,
      epoch: 1,
      xhr: null,
      running: false
    };

    function copy(key) {
      return root.getAttribute("data-copy-" + key) || "";
    }
    function fill(key, map) {
      return copy(key).replace(/\{(\w+)\}/g, function (_, name) {
        return map && Object.prototype.hasOwnProperty.call(map, name) ? map[name] : "";
      });
    }
    function limits() {
      return {
        maxFiles: Number(root.getAttribute("data-max-files")),
        maxFile: Number(root.getAttribute("data-max-file")),
        maxBatch: Number(root.getAttribute("data-max-batch")),
        concurrency: Number(root.getAttribute("data-concurrency"))
      };
    }
    function single() {
      return root.getAttribute("data-single") === "1";
    }
    function show(name) {
      var nodes = root.querySelectorAll("[data-frame]");
      for (var i = 0; i < nodes.length; i += 1) {
        nodes[i].hidden = nodes[i].getAttribute("data-frame") !== name;
      }
      var title = document.getElementById("li-title");
      if (!title) return;
      if (name === "confirm" || name === "err_nodate" || name === "err_child") title.textContent = copy("tray_title");
      else if (name === "done_ok" || name === "done_partial") title.textContent = copy("done_title");
      else if (name === "progress") title.textContent = copy("progress");
      else title.textContent = copy("tray_lab");
    }
    function clearUrls() {
      for (var i = 0; i < state.urls.length; i += 1) URL.revokeObjectURL(state.urls[i]);
      state.urls = [];
    }
    function paintTray() {
      var empty = document.getElementById("tray-empty");
      var grid = document.getElementById("tray-grid");
      var count = document.getElementById("tray-count");
      var next = document.getElementById("next-btn");
      var heading = document.getElementById("tray-heading");
      clearUrls();
      if (grid) grid.textContent = "";
      var has = state.files.length > 0;
      if (empty) empty.hidden = has;
      if (grid) grid.hidden = !has;
      if (count) {
        count.hidden = !has;
        count.textContent = String(state.files.length);
      }
      if (next) next.hidden = !has;
      if (heading) heading.textContent = has ? copy("tray_title") : copy("tray_lab");
      if (!grid) return;
      for (var i = 0; i < state.files.length; i += 1) {
        var img = document.createElement("img");
        var url = URL.createObjectURL(state.files[i]);
        state.urls.push(url);
        img.src = url;
        img.alt = "";
        grid.appendChild(img);
      }
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
        var on = state.selected.indexOf(String(id)) !== -1;
        node.className = on ? "sticker on" : "sticker off";
      }
    }
    function paintDate() {
      var chip = document.getElementById("date-chip");
      var mark = document.getElementById("date-mark");
      var text = document.getElementById("date-text");
      var empty = document.getElementById("date-empty");
      var picked = !!state.date;
      if (chip) chip.className = picked ? "date-chip picked" : "date-chip";
      if (mark) {
        mark.hidden = !picked;
        if (picked) mark.textContent = String(parseInt(state.date.slice(8, 10), 10));
      }
      if (text) text.textContent = picked ? copy("date_change") : copy("date_pick_go");
      if (empty) {
        empty.hidden = picked;
        empty.textContent = copy("date_empty");
      }
    }
    function sessionId() {
      if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
      return "s-" + String(Date.now());
    }
    function dayHref() {
      var date = state.date;
      if (state.selected.length === 1) {
        return "/children/" + encodeURIComponent(state.selected[0]) + "/days/" + encodeURIComponent(date);
      }
      return "/days/" + encodeURIComponent(date);
    }
    function resetBatch() {
      state.files = [];
      state.ok = 0;
      state.fails = [];
      state.cancelled = false;
      state.running = false;
      state.date = "";
      state.selected = initial.slice();
      var input = document.getElementById("on-date");
      if (input) input.value = "";
      paintTray();
      paintDate();
      paintStickers();
      show("tray");
    }
    function paintDone() {
      var frame = finishFrame(state.ok, state.fails.length, state.files.length, state.cancelled);
      var by = fill("by", { who: root.getAttribute("data-who") || "" });
      var href = dayHref();
      if (frame === "done_ok") {
        var all = document.getElementById("done-all");
        if (all) all.textContent = fill("done_all", { n: String(state.ok) });
        var byOk = document.getElementById("done-by-ok");
        if (byOk) byOk.textContent = by;
        var seeOk = document.getElementById("go-see-ok");
        if (seeOk) seeOk.setAttribute("href", href);
      } else {
        var okLine = document.getElementById("done-ok");
        if (okLine) okLine.textContent = fill("done_ok", { n: String(state.ok) });
        var failLine = document.getElementById("done-fail");
        if (failLine) {
          failLine.hidden = state.fails.length === 0;
          failLine.textContent = fill("done_fail", { n: String(state.fails.length) });
        }
        var list = document.getElementById("fail-list");
        if (list) {
          list.textContent = "";
          for (var i = 0; i < state.fails.length; i += 1) {
            var item = document.createElement("li");
            item.textContent = fill("fail_item", { name: state.fails[i].name, why: state.fails[i].why });
            list.appendChild(item);
          }
        }
        var byPart = document.getElementById("done-by-partial");
        if (byPart) byPart.textContent = by;
        var seePart = document.getElementById("go-see-partial");
        if (seePart) seePart.setAttribute("href", href);
      }
      show(frame);
    }
    function mapWhy(status, data) {
      var key = whyKey(data && data.code);
      if (key) return copy(key);
      if (data && data.detail) return data.detail;
      return copy("fail_why_bad");
    }
    function postOne(file, epoch, attempt) {
      return new Promise(function (resolve) {
        if (!acceptLate(epoch, state.epoch) || state.cancelled) {
          resolve({ ignored: true });
          return;
        }
        var body = new FormData();
        body.append("file", file, file.name || "photo");
        body.append("on_date", state.date);
        for (var i = 0; i < state.selected.length; i += 1) body.append("child_ids", state.selected[i]);
        body.append("import_session", state.session || "");
        body.append("client_name", file.name || "");
        var xhr = new XMLHttpRequest();
        state.xhr = xhr;
        xhr.open("POST", "/api/families/current/legacy-import/photos");
        xhr.onload = function () {
          if (!acceptLate(epoch, state.epoch)) {
            resolve({ ignored: true });
            return;
          }
          var data = {};
          try { data = JSON.parse(xhr.responseText || "{}"); } catch (err) { data = {}; }
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve({ ok: true });
            return;
          }
          if (retryable(xhr.status, data) && attempt < 1) {
            resolve(postOne(file, epoch, attempt + 1));
            return;
          }
          if (xhr.status === 403 && data && data.code === "legacy_import_readonly") {
            resolve({ stop: "err_perm" });
            return;
          }
          if (xhr.status === 403 && !(data && data.code)) {
            resolve({ stop: "deny" });
            return;
          }
          if (data && (data.code === "on_date_required" || data.code === "on_date_invalid")) {
            resolve({ stop: "err_nodate" });
            return;
          }
          if (data && (data.code === "child_required" || data.code === "child_invalid")) {
            resolve({ stop: "err_child" });
            return;
          }
          resolve({ fail: true, why: mapWhy(xhr.status, data) });
        };
        xhr.onerror = function () {
          if (!acceptLate(epoch, state.epoch)) {
            resolve({ ignored: true });
            return;
          }
          if (attempt < 1) {
            resolve(postOne(file, epoch, attempt + 1));
            return;
          }
          resolve({ fail: true, why: copy("fail_why_bad") });
        };
        xhr.onabort = function () {
          resolve({ ignored: true });
        };
        xhr.send(body);
      });
    }
    function paintProgress(settled) {
      var node = document.getElementById("progress-count");
      var bar = document.getElementById("progress-bar");
      var total = state.files.length;
      if (node) node.textContent = String(settled) + "/" + String(total);
      if (bar) bar.style.width = (total ? Math.round((100 * settled) / total) : 0) + "%";
    }
    function start() {
      if (state.running) return;
      if (!state.date) {
        show("err_nodate");
        return;
      }
      if (!state.selected.length) {
        show("err_child");
        return;
      }
      var gate = batchBlock(state.files.length, totalSize(state.files), limits());
      if (gate === "over50") {
        show("err_over50");
        return;
      }
      if (gate === "batch") {
        show("err_batch_mb");
        return;
      }
      if (limits().concurrency !== 1) return;
      state.running = true;
      state.cancelled = false;
      state.ok = 0;
      state.fails = [];
      state.session = sessionId();
      var epoch = state.epoch;
      show("progress");
      paintProgress(0);
      var settled = 0;
      runSerial(state.files, function (file) {
        if (!acceptLate(epoch, state.epoch) || state.cancelled) return null;
        if (fileSkip(file.size || 0, limits()) === "mb") {
          state.fails.push({ name: file.name || "", why: copy("fail_why_mb") });
          settled += 1;
          paintProgress(settled);
          return null;
        }
        return postOne(file, epoch, 0).then(function (result) {
          if (!result || result.ignored || !acceptLate(epoch, state.epoch)) return null;
          if (result.stop) {
            state.cancelled = true;
            show(result.stop);
            state.running = false;
            return null;
          }
          if (result.ok) state.ok += 1;
          else if (result.fail) state.fails.push({ name: file.name || "", why: result.why || "" });
          settled += 1;
          paintProgress(settled);
          return null;
        });
      }).then(function () {
        if (!acceptLate(epoch, state.epoch)) return;
        if (!state.running) return;
        state.epoch += 1;
        state.running = false;
        state.xhr = null;
        paintDone();
      });
    }
    function totalSize(files) {
      var sum = 0;
      for (var i = 0; i < files.length; i += 1) sum += Number(files[i].size) || 0;
      return sum;
    }
    function takeFiles(list) {
      var files = [];
      for (var i = 0; i < list.length; i += 1) files.push(list[i]);
      var gate = batchBlock(files.length, totalSize(files), limits());
      if (gate === "over50") {
        state.files = [];
        paintTray();
        show("err_over50");
        return;
      }
      if (gate === "batch") {
        state.files = [];
        paintTray();
        show("err_batch_mb");
        return;
      }
      if (!files.length) return;
      state.files = files;
      paintTray();
      show("tray");
    }
    var pick = document.getElementById("pick-file");
    var pickBtn = document.getElementById("pick-btn");
    if (pickBtn && pick) {
      pickBtn.addEventListener("click", function () { pick.click(); });
      pick.addEventListener("change", function () {
        var list = pick.files ? pick.files : [];
        takeFiles(list);
        pick.value = "";
      });
    }
    var nextBtn = document.getElementById("next-btn");
    if (nextBtn) nextBtn.addEventListener("click", function () { show("confirm"); });
    var dateInput = document.getElementById("on-date");
    if (dateInput) {
      dateInput.value = "";
      dateInput.addEventListener("change", function () {
        state.date = dateInput.value || "";
        paintDate();
      });
    }
    var chip = document.getElementById("date-chip");
    if (chip && dateInput) {
      chip.addEventListener("click", function () {
        if (dateInput.showPicker) {
          try { dateInput.showPicker(); return; } catch (err) { /* fall through */ }
        }
        dateInput.click();
      });
    }
    var row = document.getElementById("sticker-row");
    if (row) {
      row.addEventListener("click", function (event) {
        var node = event.target.closest ? event.target.closest("[data-child-id]") : null;
        if (!node) return;
        state.selected = toggleChild(state.selected, node.getAttribute("data-child-id"), single());
        paintStickers();
      });
    }
    var submit = document.getElementById("submit-btn");
    if (submit) submit.addEventListener("click", start);
    function onClose() {
      if (state.running) {
        state.cancelled = true;
        state.epoch += 1;
        if (state.xhr) {
          try { state.xhr.abort(); } catch (err) { /* already finished */ }
        }
        state.running = false;
        state.xhr = null;
        paintDone();
        return;
      }
      var tray = root.querySelector('[data-frame="tray"]');
      if (tray && !tray.hidden) {
        global.location.href = "/";
        return;
      }
      show("tray");
    }
    var closeBtn = document.getElementById("li-close");
    if (closeBtn) closeBtn.addEventListener("click", onClose);
    var backs = root.querySelectorAll("[data-back]");
    for (var b = 0; b < backs.length; b += 1) {
      backs[b].addEventListener("click", function (event) {
        var target = event.currentTarget.getAttribute("data-back") || "tray";
        show(target);
      });
    }
    var reOk = document.getElementById("re-ok");
    var rePart = document.getElementById("re-partial");
    if (reOk) reOk.addEventListener("click", resetBatch);
    if (rePart) rePart.addEventListener("click", resetBatch);
    paintDate();
    paintStickers();
    show("tray");
  }

  if (global.document.readyState === "loading") global.document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(typeof window !== "undefined" ? window : globalThis);

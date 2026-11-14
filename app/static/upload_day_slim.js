/* Daily upload confirm step. Sentences come from data-copy-* only.
   One file is compressed and posted at a time. The hairline follows that file. */
(function (global) {
  var MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
  var SILENT_MAX_EDGE = 2560;
  var SILENT_TARGET_BYTES = 2 * 1024 * 1024;
  var SILENT_FALLBACK_EDGE = 1920;
  var DAILY_BATCH_MAX_FILES = 50;
  var DAILY_CONCURRENCY = 1;

  function encodeSteps() {
    return [
      { edge: SILENT_MAX_EDGE, quality: 0.82 },
      { edge: SILENT_MAX_EDGE, quality: 0.70 },
      { edge: SILENT_FALLBACK_EDGE, quality: 0.70 },
    ];
  }

  function batchBlock(count) {
    if (count > DAILY_BATCH_MAX_FILES) return "err_batch";
    return "";
  }

  function fileSizeBlock(size) {
    if (size > MAX_UPLOAD_BYTES) return "err_size";
    return "";
  }

  function allowedType(file) {
    var type = String((file && file.type) || "").toLowerCase();
    var name = String((file && file.name) || "").toLowerCase();
    if (type === "image/jpeg" || type === "image/jpg" || type === "image/png" || type === "image/webp") return true;
    if (/\.(jpe?g|png|webp)$/.test(name)) return true;
    if (!type && name.indexOf(".") === -1) return true;
    return false;
  }

  function classifyFiles(files) {
    var list = files || [];
    if (!list.length) return { block: "", items: [], bad: 0, over: 0, canSubmit: false };
    if (list.length > DAILY_BATCH_MAX_FILES) {
      return { block: "err_batch", items: [], bad: 0, over: 0, canSubmit: false };
    }
    var items = [];
    var bad = 0;
    var over = 0;
    for (var i = 0; i < list.length; i += 1) {
      var file = list[i];
      if (!allowedType(file)) {
        bad += 1;
        continue;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        over += 1;
        items.push({ over: true, size: file.size, name: file.name || "" });
        continue;
      }
      items.push({ over: false, size: file.size, name: file.name || "" });
    }
    var block = "";
    if (over) block = "err_size";
    else if (bad) block = "err_bad";
    var ready = false;
    for (var j = 0; j < items.length; j += 1) {
      if (!items[j].over) ready = true;
    }
    return { block: block, items: items, bad: bad, over: over, canSubmit: over === 0 && ready };
  }

  function progressWidth(loaded, total) {
    var done = Number(loaded);
    var all = Number(total);
    if (!isFinite(done) || !isFinite(all) || all <= 0) return 0;
    if (done < 0) done = 0;
    if (done > all) done = all;
    return (done / all) * 100;
  }

  function batchFillPercent(index, total) {
    var n = Number(total);
    var i = Number(index);
    if (!isFinite(n) || n <= 0) return 0;
    return (i / n) * 100;
  }

  function linePercent(fileIndex, fileCount, loaded, total) {
    return progressWidth(loaded, total);
  }

  function choosePayload(originalSize, edge, blobs) {
    if (originalSize <= SILENT_TARGET_BYTES && edge <= SILENT_MAX_EDGE) {
      return { action: "original" };
    }
    var produced = [];
    var list = blobs || [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i] && typeof list[i].size === "number") produced.push(list[i]);
    }
    if (!produced.length) {
      if (originalSize <= MAX_UPLOAD_BYTES) return { action: "original" };
      return { action: "fail" };
    }
    var chosen = produced[produced.length - 1];
    for (var j = 0; j < produced.length; j += 1) {
      chosen = produced[j];
      if (chosen.size <= SILENT_TARGET_BYTES) break;
    }
    if (chosen.size >= originalSize) {
      if (originalSize <= MAX_UPLOAD_BYTES) return { action: "original" };
      return { action: "fail" };
    }
    if (chosen.size > MAX_UPLOAD_BYTES) return { action: "fail" };
    return { action: "blob", size: chosen.size };
  }

  function retryable(status, data) {
    if (!status || status >= 500) return true;
    if (status === 404 && !(data && data.code)) return true;
    return false;
  }

  function createRun() {
    return { generation: 1, aborted: false, successes: [], failures: [], retry: false, childRequired: false };
  }

  function abortRun(run) {
    run.aborted = true;
    run.generation += 1;
    return run;
  }

  function noteSuccess(run, id, generation) {
    if (!run || run.aborted || generation !== run.generation) return false;
    run.successes.push(id);
    return true;
  }

  function outcome(run, total) {
    var ok = run.successes.length;
    var fail = Math.max(0, Number(total) - ok);
    return {
      redirect: !run.aborted && !run.retry && fail === 0 && ok > 0,
      failGo: ok > 0 && (run.aborted || run.retry || fail > 0),
      ok: ok,
      fail: fail,
    };
  }

  function anchorChild(selected, preselected) {
    var ids = (selected || []).map(String);
    var pre = preselected == null ? "" : String(preselected);
    if (pre && ids.indexOf(pre) !== -1) return pre;
    return ids.length ? ids[0] : "";
  }

  function landingUrl(kind, anchor, uid, takenOn, ids, today) {
    var list = (ids || []).map(function (id) { return String(id); }).join(",");
    var toast = takenOn === today ? "placed_today" : "placed_day";
    var query = "new=" + list + "&toast=" + toast;
    if (kind === "self") {
      return "/people/" + encodeURIComponent(uid) + "/album/days/" + takenOn + "?" + query;
    }
    return "/children/" + encodeURIComponent(anchor) + "/days/" + takenOn + "?" + query;
  }

  function jpegName(name) {
    var base = String(name || "photo").replace(/\.[^.]+$/, "");
    if (!base) base = "photo";
    return base + ".jpg";
  }

  function runSerial(items, worker) {
    var index = 0;
    var inflight = 0;
    var maxInflight = 0;
    function next() {
      if (index >= items.length) return Promise.resolve(maxInflight);
      var current = items[index];
      var place = index;
      index += 1;
      inflight += 1;
      if (inflight > maxInflight) maxInflight = inflight;
      return Promise.resolve()
        .then(function () { return worker(current, place); })
        .then(function () {
          inflight -= 1;
          return next();
        }, function () {
          inflight -= 1;
          return next();
        });
    }
    return next();
  }

  var api = {
    MAX_UPLOAD_BYTES: MAX_UPLOAD_BYTES,
    SILENT_MAX_EDGE: SILENT_MAX_EDGE,
    SILENT_TARGET_BYTES: SILENT_TARGET_BYTES,
    SILENT_FALLBACK_EDGE: SILENT_FALLBACK_EDGE,
    DAILY_BATCH_MAX_FILES: DAILY_BATCH_MAX_FILES,
    DAILY_CONCURRENCY: DAILY_CONCURRENCY,
    encodeSteps: encodeSteps,
    batchBlock: batchBlock,
    fileSizeBlock: fileSizeBlock,
    allowedType: allowedType,
    classifyFiles: classifyFiles,
    progressWidth: progressWidth,
    batchFillPercent: batchFillPercent,
    linePercent: linePercent,
    choosePayload: choosePayload,
    retryable: retryable,
    createRun: createRun,
    abortRun: abortRun,
    noteSuccess: noteSuccess,
    outcome: outcome,
    anchorChild: anchorChild,
    landingUrl: landingUrl,
    jpegName: jpegName,
    runSerial: runSerial,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.UploadDaySlim = api;
  if (!global.document) return;

  function boot() {
    var root = global.document.getElementById("photo-app");
    if (!root || root.getAttribute("data-mode") === "edit") return;
    var selectedNode = global.document.getElementById("photo-selected");
    var childrenNode = global.document.getElementById("photo-children");
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
    var state = {
      items: [],
      bad: 0,
      over: 0,
      busy: false,
      xhr: null,
      run: null,
      pickGen: 0,
      lastHint: "",
    };

    function copy(key) {
      return root.getAttribute("data-copy-" + key) || "";
    }
    function fill(key, map) {
      return copy(key).replace(/\{(\w+)\}/g, function (_, name) {
        return map && Object.prototype.hasOwnProperty.call(map, name) ? String(map[name]) : "{" + name + "}";
      });
    }
    function kind() {
      return root.getAttribute("data-kind") || "";
    }
    function findChild(id) {
      for (var i = 0; i < children.length; i += 1) {
        if (String(children[i].id) === String(id)) return children[i];
      }
      return null;
    }
    function dateValue() {
      var input = global.document.getElementById("photo-date");
      if (input && input.value) return input.value;
      return root.getAttribute("data-today") || "";
    }
    function chipText(iso) {
      var parts = String(iso || "").split("-");
      if (parts.length !== 3) return "";
      var mo = String(parseInt(parts[1], 10));
      var d = String(parseInt(parts[2], 10));
      var today = root.getAttribute("data-today") || "";
      if (iso === today) return fill("date_today", { mo: mo, d: d });
      return fill("date_only", { mo: mo, d: d });
    }
    function showHintText(text) {
      var node = global.document.getElementById("photo-hint");
      if (!node) return;
      node.hidden = !text;
      node.textContent = text || "";
    }
    function readyItems() {
      var list = [];
      for (var i = 0; i < state.items.length; i += 1) {
        if (!state.items[i].over && !state.items[i].sent) list.push(state.items[i]);
      }
      return list;
    }
    function paintHint() {
      var hint = global.document.getElementById("strip-hint");
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
      var row = global.document.getElementById("sticker-row");
      if (!row) return;
      var nodes = row.children || [];
      for (var i = 0; i < nodes.length; i += 1) {
        var node = nodes[i];
        if (!node.getAttribute) continue;
        var id = node.getAttribute("data-child-id");
        if (!id) continue;
        node.className = selected.indexOf(String(id)) !== -1 ? "sticker on" : "sticker off";
      }
    }
    function paintMinis() {
      var row = global.document.getElementById("pick-minis");
      if (!row) return;
      while (row.firstChild) row.removeChild(row.firstChild);
      for (var i = 0; i < state.items.length; i += 1) {
        var item = state.items[i];
        var mini = global.document.createElement("button");
        mini.type = "button";
        mini.className = item.over ? "mini over" : "mini";
        mini.setAttribute("data-mini", String(i));
        if (item.over) mini.setAttribute("data-over", "1");
        var frame = global.document.createElement("i");
        if (!item.over && item.file && global.URL && global.URL.createObjectURL) {
          if (!item.url) item.url = global.URL.createObjectURL(item.file);
          frame.style.backgroundImage = "url(\"" + item.url + "\")";
          frame.style.backgroundSize = "cover";
        }
        mini.appendChild(frame);
        row.appendChild(mini);
      }
      var count = global.document.getElementById("pick-count");
      if (count) count.textContent = fill("pick_n", { n: String(state.items.length) });
    }
    function paintDate() {
      var label = global.document.getElementById("date-chip-text");
      if (label) label.textContent = chipText(dateValue());
    }
    function paintBar() {
      var bar = global.document.getElementById("redbar");
      if (!bar) return;
      var empty = kind() === "child" && !selected.length;
      bar.hidden = !empty;
      bar.textContent = empty ? copy("err_no_child") : "";
    }
    function paintButton() {
      var btn = global.document.getElementById("photo-submit");
      if (!btn || state.busy) return;
      var needsChild = kind() === "child" && !selected.length;
      var blocked = needsChild || state.over > 0 || readyItems().length === 0;
      btn.disabled = blocked;
      btn.hidden = false;
      if (btn.classList) btn.classList.toggle("dis", blocked);
      btn.textContent = kind() === "self" ? copy("submit_self") : copy("submit");
    }
    function paintNotice() {
      if (state.busy) return;
      if (state.over > 0) showHintText(copy("err_size"));
      else if (state.bad > 0) showHintText(copy("err_bad"));
      else if (!state.run) showHintText("");
    }
    function paint() {
      paintHint();
      paintStickers();
      paintMinis();
      paintDate();
      paintBar();
      paintNotice();
      paintButton();
    }
    function toggle(raw) {
      if (root.getAttribute("data-single") === "1" || state.busy) return;
      var id = String(raw);
      var index = selected.indexOf(id);
      state.lastHint = "";
      if (index === -1) selected.push(id);
      else if (selected.length <= 1) state.lastHint = copy("strip_hint_last");
      else selected.splice(index, 1);
      paint();
    }
    function releaseItems(items) {
      for (var i = 0; i < items.length; i += 1) {
        if (items[i].url && global.URL && global.URL.revokeObjectURL) {
          try { global.URL.revokeObjectURL(items[i].url); } catch (err) { /* gone */ }
        }
      }
    }
    function closeBitmap(bitmap) {
      if (bitmap && typeof bitmap.close === "function") {
        try { bitmap.close(); } catch (err) { /* already closed */ }
      }
    }
    function loadImage(file) {
      return new Promise(function (resolve, reject) {
        var url = global.URL.createObjectURL(file);
        var img = new global.Image();
        img.onload = function () {
          global.URL.revokeObjectURL(url);
          resolve(img);
        };
        img.onerror = function () {
          global.URL.revokeObjectURL(url);
          reject(new Error("decode"));
        };
        img.src = url;
      });
    }
    function decodeOne(file) {
      if (typeof global.createImageBitmap === "function") {
        return global.createImageBitmap(file, { imageOrientation: "from-image" }).catch(function () {
          return loadImage(file);
        });
      }
      return loadImage(file);
    }
    function canDecode(file) {
      if (typeof global.createImageBitmap !== "function" && typeof global.Image !== "function") {
        return Promise.resolve(true);
      }
      return decodeOne(file).then(function (bitmap) {
        closeBitmap(bitmap);
        return true;
      }, function () {
        return false;
      });
    }
    function canvasBlob(source, width, height, maxEdge, quality) {
      var edge = Math.max(width, height) || 1;
      var scale = edge > maxEdge ? maxEdge / edge : 1;
      var w = Math.max(1, Math.round(width * scale));
      var h = Math.max(1, Math.round(height * scale));
      var canvas = global.document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      var ctx = canvas.getContext("2d");
      ctx.drawImage(source, 0, 0, w, h);
      return new Promise(function (resolve, reject) {
        if (!canvas.toBlob) {
          reject(new Error("blob"));
          return;
        }
        canvas.toBlob(function (blob) {
          if (!blob) reject(new Error("empty"));
          else resolve(blob);
        }, "image/jpeg", quality);
      });
    }
    function compressFile(file) {
      return decodeOne(file).then(function (bitmap) {
        var width = bitmap.width || bitmap.naturalWidth || 0;
        var height = bitmap.height || bitmap.naturalHeight || 0;
        var edge = Math.max(width, height);
        if (file.size <= SILENT_TARGET_BYTES && edge <= SILENT_MAX_EDGE) {
          closeBitmap(bitmap);
          return file;
        }
        var steps = encodeSteps();
        var blobs = [];
        var chain = Promise.resolve();
        steps.forEach(function (step) {
          chain = chain.then(function () {
            var last = blobs.length ? blobs[blobs.length - 1] : null;
            if (last && last.size <= SILENT_TARGET_BYTES) return null;
            return canvasBlob(bitmap, width, height, step.edge, step.quality).then(function (blob) {
              blobs.push(blob);
            }, function () {
              blobs.push(null);
            });
          });
        });
        return chain.then(function () {
          closeBitmap(bitmap);
          var decision = choosePayload(file.size, edge, blobs);
          if (decision.action === "fail") throw new Error("cap");
          if (decision.action === "original") return file;
          var chosen = null;
          for (var i = 0; i < blobs.length; i += 1) {
            if (blobs[i] && blobs[i].size === decision.size) {
              chosen = blobs[i];
              break;
            }
          }
          if (!chosen) return file;
          var name = jpegName(file.name);
          if (typeof File === "function") {
            try {
              return new File([chosen], name, { type: "image/jpeg" });
            } catch (err) {
              return chosen;
            }
          }
          return chosen;
        }, function (err) {
          closeBitmap(bitmap);
          throw err;
        });
      }, function () {
        if (file.size <= MAX_UPLOAD_BYTES) return file;
        throw new Error("decode");
      });
    }
    function acceptFiles(list) {
      var gen = state.pickGen + 1;
      state.pickGen = gen;
      releaseItems(state.items);
      state.items = [];
      state.bad = 0;
      state.over = 0;
      state.run = null;
      if (batchBlock(list.length)) {
        showHintText(copy("err_batch"));
        paintButton();
        paintMinis();
        return;
      }
      var index = 0;
      function step() {
        if (gen !== state.pickGen) return;
        if (index >= list.length) {
          paint();
          return;
        }
        var file = list[index];
        index += 1;
        if (!allowedType(file)) {
          state.bad += 1;
          step();
          return;
        }
        if (file.size > MAX_UPLOAD_BYTES) {
          state.over += 1;
          state.items.push({ file: file, over: true, url: "" });
          step();
          return;
        }
        canDecode(file).then(function (ok) {
          if (gen !== state.pickGen) return;
          if (!ok) state.bad += 1;
          else state.items.push({ file: file, over: false, url: "" });
          step();
        });
      }
      step();
    }
    function paintProgress(i, n, width) {
      var ticket = global.document.getElementById("progress-ticket");
      var text = global.document.getElementById("progress-text");
      var line = global.document.getElementById("progress-line");
      var btn = global.document.getElementById("photo-submit");
      if (ticket) {
        ticket.hidden = false;
        ticket.className = "ticket";
      }
      if (text) text.textContent = fill("progress", { i: String(i), n: String(n) });
      if (line) line.style.width = String(width) + "%";
      if (btn) btn.hidden = true;
      var go = global.document.getElementById("fail-go");
      if (go) go.hidden = true;
    }
    function destination(ids) {
      var anchor = anchorChild(selected, root.getAttribute("data-anchor") || "");
      return landingUrl(
        kind(),
        anchor,
        root.getAttribute("data-uid") || "",
        dateValue(),
        ids,
        root.getAttribute("data-today") || ""
      );
    }
    function showSettled(run, planned) {
      state.busy = false;
      var result = outcome(run, planned);
      if (result.redirect) {
        global.location.href = destination(run.successes);
        return;
      }
      var ticket = global.document.getElementById("progress-ticket");
      var text = global.document.getElementById("progress-text");
      var go = global.document.getElementById("fail-go");
      var btn = global.document.getElementById("photo-submit");
      if (ticket) {
        ticket.hidden = false;
        ticket.className = "ticket warn fail-done";
      }
      if (text) {
        text.textContent = fill("fail_one", { ok: String(result.ok), fail: String(result.fail) });
      }
      if (go) {
        if (result.failGo) {
          go.hidden = false;
          go.setAttribute("data-href", destination(run.successes));
        } else {
          go.hidden = true;
        }
      }
      if (run.childRequired && result.ok === 0) showHintText(copy("err_no_child"));
      else if (run.retry) showHintText(root.getAttribute("data-retry-text") || "");
      else showHintText("");
      if (btn) {
        var again = run.retry && !run.aborted;
        btn.hidden = !again;
        btn.disabled = !again;
        if (btn.classList) btn.classList.toggle("dis", !again);
        btn.textContent = kind() === "self" ? copy("submit_self") : copy("submit");
      }
    }
    function postOne(run, file, onProgress) {
      return new Promise(function (resolve) {
        var generation = run.generation;
        var xhr = new XMLHttpRequest();
        state.xhr = xhr;
        xhr.open("POST", "/api/photos");
        if (xhr.upload) {
          xhr.upload.onprogress = function (event) {
            if (run.aborted || generation !== run.generation) return;
            var total = event.lengthComputable ? event.total : 0;
            onProgress(event.loaded || 0, total);
          };
        }
        xhr.onload = function () {
          var data = {};
          try { data = JSON.parse(xhr.responseText || "{}"); } catch (err) { data = {}; }
          resolve({ status: xhr.status, data: data, generation: generation });
        };
        xhr.onerror = function () {
          resolve({ status: 0, data: {}, generation: generation });
        };
        xhr.onabort = function () {
          resolve({ status: 0, data: {}, generation: generation, aborted: true });
        };
        var form = new FormData();
        form.append("kind", kind());
        form.append("taken_on", dateValue());
        form.append("file", file, (file && file.name) || "photo.jpg");
        if (kind() === "child") {
          selected.forEach(function (id) { form.append("child_ids", id); });
        }
        xhr.send(form);
      });
    }
    function uploadAll() {
      if (state.busy) return;
      var ready = readyItems();
      if (batchBlock(state.items.length) || state.over || !ready.length) {
        paint();
        return;
      }
      if (kind() === "child" && !selected.length) {
        showHintText(copy("err_no_child"));
        paintBar();
        return;
      }
      state.busy = true;
      var run = createRun();
      state.run = run;
      var planned = ready.length;
      var index = 0;
      function next() {
        if (run.aborted) {
          showSettled(run, planned);
          return;
        }
        if (index >= ready.length) {
          showSettled(run, planned);
          return;
        }
        var place = index;
        var item = ready[place];
        index += 1;
        paintProgress(place + 1, planned, 0);
        compressFile(item.file).then(function (payload) {
          if (run.aborted) {
            showSettled(run, planned);
            return;
          }
          if (!payload || payload.size > MAX_UPLOAD_BYTES) {
            run.failures.push(place);
            next();
            return;
          }
          paintProgress(place + 1, planned, 0);
          postOne(run, payload, function (loaded, total) {
            if (!run.aborted) paintProgress(place + 1, planned, linePercent(place + 1, planned, loaded, total));
          }).then(function (result) {
            if (run.aborted || result.aborted || result.generation !== run.generation) {
              showSettled(run, planned);
              return;
            }
            if (retryable(result.status, result.data)) {
              run.retry = true;
              showSettled(run, planned);
              return;
            }
            if (result.status >= 200 && result.status < 300 && result.data && result.data.id != null) {
              if (noteSuccess(run, result.data.id, result.generation)) {
                item.sent = true;
                next();
                return;
              }
            }
            run.failures.push(place);
            if (result.data && result.data.code === "child_required") run.childRequired = true;
            next();
          });
        }, function () {
          run.failures.push(place);
          next();
        });
      }
      next();
    }
    function leaveOrSettle() {
      if (state.run && (state.busy || state.run.successes.length)) {
        abortRun(state.run);
        if (state.xhr) {
          try { state.xhr.abort(); } catch (err) { /* finished */ }
        }
        if (state.run.successes.length) {
          showSettled(state.run, readyItems().length || state.run.successes.length);
          return;
        }
      } else if (state.xhr) {
        if (state.run) abortRun(state.run);
        try { state.xhr.abort(); } catch (err) { /* finished */ }
      }
      global.location.href = root.getAttribute("data-back") || "/";
    }
    var row = global.document.getElementById("sticker-row");
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
    var pick = global.document.getElementById("pick-row");
    var fileInput = global.document.getElementById("photo-file");
    if (pick && fileInput) {
      pick.addEventListener("click", function (event) {
        var node = event.target;
        while (node && node !== pick) {
          if (node.getAttribute && node.getAttribute("data-mini") != null) {
            if (node.getAttribute("data-over") === "1" && !state.busy) {
              var at = parseInt(node.getAttribute("data-mini"), 10);
              if (state.items[at]) {
                releaseItems([state.items[at]]);
                state.items.splice(at, 1);
                state.over = 0;
                for (var i = 0; i < state.items.length; i += 1) {
                  if (state.items[i].over) state.over += 1;
                }
                paint();
              }
            }
            return;
          }
          node = node.parentNode;
        }
        if (!state.busy) fileInput.click();
      });
      fileInput.addEventListener("change", function () {
        var files = fileInput.files ? Array.prototype.slice.call(fileInput.files) : [];
        fileInput.value = "";
        acceptFiles(files);
      });
    }
    var dateInput = global.document.getElementById("photo-date");
    if (dateInput) dateInput.addEventListener("change", paintDate);
    var submitBtn = global.document.getElementById("photo-submit");
    if (submitBtn) submitBtn.addEventListener("click", uploadAll);
    var go = global.document.getElementById("fail-go");
    if (go) {
      go.addEventListener("click", function () {
        var href = go.getAttribute("data-href") || "";
        if (href) global.location.href = href;
      });
    }
    function onCancel(event) {
      if (event && event.preventDefault) event.preventDefault();
      leaveOrSettle();
    }
    var cancelBtn = global.document.getElementById("photo-cancel");
    if (cancelBtn) cancelBtn.addEventListener("click", onCancel);
    var closeBtn = global.document.getElementById("photo-close");
    if (closeBtn) closeBtn.addEventListener("click", onCancel);
    global.document.addEventListener("visibilitychange", function () {
      if (global.document.visibilityState === "hidden" && state.busy) {
        if (state.run) abortRun(state.run);
        if (state.xhr) {
          try { state.xhr.abort(); } catch (err) { /* finished */ }
        }
      }
    });
    global.addEventListener("pagehide", function () {
      if (state.run) abortRun(state.run);
      if (state.xhr) {
        try { state.xhr.abort(); } catch (err) { /* finished */ }
      }
    });
    paint();
  }

  if (global.document.readyState === "loading") global.document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(typeof window !== "undefined" ? window : globalThis);

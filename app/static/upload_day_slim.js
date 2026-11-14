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

  function lastTaggedKey(userId) {
    return "fa.last_tagged_child." + String(userId == null ? "" : userId);
  }

  function readLastTagged(storage, userId) {
    if (!storage || typeof storage.getItem !== "function") return "";
    try {
      var raw = storage.getItem(lastTaggedKey(userId));
      return raw == null ? "" : String(raw);
    } catch (err) {
      return "";
    }
  }

  function writeLastTagged(storage, userId, childId) {
    if (!storage || typeof storage.setItem !== "function") return false;
    if (childId == null || String(childId) === "") return false;
    try {
      storage.setItem(lastTaggedKey(userId), String(childId));
      return true;
    } catch (err) {
      return false;
    }
  }

  function resolveDefaultChild(opts) {
    var options = opts || {};
    var children = options.children || [];
    var ids = [];
    for (var i = 0; i < children.length; i += 1) ids.push(String(children[i].id));
    var pref = options.pref == null ? "" : String(options.pref);
    if (pref && ids.indexOf(pref) !== -1) return pref;
    var viewed = options.viewedId == null ? "" : String(options.viewedId);
    if (options.singleView && viewed && ids.indexOf(viewed) !== -1) return viewed;
    return ids.length ? ids[0] : "";
  }

  function shiftIso(iso, delta) {
    var parts = String(iso || "").split("-");
    if (parts.length !== 3) return "";
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10);
    var d = parseInt(parts[2], 10);
    if (!y || !m || !d) return "";
    var dt = new Date(Date.UTC(y, m - 1, d));
    dt.setUTCDate(dt.getUTCDate() + delta);
    var mm = dt.getUTCMonth() + 1;
    var dd = dt.getUTCDate();
    return dt.getUTCFullYear() + "-" + (mm < 10 ? "0" : "") + mm + "-" + (dd < 10 ? "0" : "") + dd;
  }

  function monthDay(iso) {
    var parts = String(iso || "").split("-");
    if (parts.length !== 3) return "";
    return String(parseInt(parts[1], 10)) + "月" + String(parseInt(parts[2], 10)) + "日";
  }

  function recentDays(todayIso, pastCount) {
    var count = pastCount == null ? 2 : Number(pastCount);
    if (!isFinite(count) || count < 0) count = 0;
    if (count > 3) count = 3;
    var days = [String(todayIso || "")];
    for (var i = 1; i <= count; i += 1) days.push(shiftIso(todayIso, -i));
    return days;
  }

  function chipLabel(opts) {
    var options = opts || {};
    var today = String(options.today || "");
    var taken = String(options.takenOn || today);
    var day = taken === today ? "今天" : monthDay(taken);
    if (options.self || options.solo || !options.name) return day;
    return day + " · " + options.name;
  }

  function submitKey(today, takenOn) {
    return String(takenOn || "") === String(today || "") ? "submit" : "submit_other";
  }

  function selectStartsUpload() {
    return false;
  }

  function dismissUploads() {
    return false;
  }

  function requestFieldNames(kind) {
    var names = ["kind", "taken_on", "file"];
    if (kind === "child") names.push("child_ids");
    return names;
  }

  function submitBlocked(opts) {
    var options = opts || {};
    if (options.batch) return true;
    if (options.over) return true;
    if (options.kind === "child" && !(options.selected && options.selected.length)) return true;
    if (!options.ready) return true;
    return false;
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
    lastTaggedKey: lastTaggedKey,
    readLastTagged: readLastTagged,
    writeLastTagged: writeLastTagged,
    resolveDefaultChild: resolveDefaultChild,
    shiftIso: shiftIso,
    monthDay: monthDay,
    recentDays: recentDays,
    chipLabel: chipLabel,
    submitKey: submitKey,
    selectStartsUpload: selectStartsUpload,
    dismissUploads: dismissUploads,
    requestFieldNames: requestFieldNames,
    submitBlocked: submitBlocked,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.UploadDaySlim = api;
  if (!global.document) return;

  function boot() {
    var document = global.document;
    var root = document.getElementById("upload-slip");
    if (!root) return;
    var childrenNode = document.getElementById("slip-children");
    var children = [];
    try {
      children = JSON.parse(childrenNode ? childrenNode.textContent || "[]" : "[]");
    } catch (err) {
      children = [];
    }
    var state = {
      items: [],
      bad: 0,
      over: 0,
      block: "",
      pickedCount: 0,
      busy: false,
      xhr: null,
      run: null,
      pickGen: 0,
      selected: [],
      anchorSeed: "",
      lifted: false,
      takenOn: root.getAttribute("data-today") || "",
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
      return state.takenOn || root.getAttribute("data-today") || "";
    }
    function browserStorage() {
      try { return global.localStorage || null; } catch (err) { return null; }
    }
    function showWarn(text) {
      var node = document.getElementById("slip-warn");
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
    function showDecision() {
      var dock = document.getElementById("upload-dock");
      var progress = document.getElementById("progress-dock");
      if (dock) dock.hidden = false;
      root.hidden = false;
      if (progress) progress.hidden = true;
    }
    function showProgress() {
      var dock = document.getElementById("upload-dock");
      var progress = document.getElementById("progress-dock");
      if (dock) dock.hidden = false;
      root.hidden = true;
      if (progress) progress.hidden = false;
    }
    function paintDays() {
      var row = document.getElementById("slip-days");
      if (!row) return;
      var today = root.getAttribute("data-today") || "";
      var taken = dateValue();
      var days = recentDays(today, 2);
      var pick = document.getElementById("slip-pick-day");
      var existing = row.querySelectorAll("[data-iso]");
      for (var i = existing.length - 1; i >= 0; i -= 1) {
        existing[i].parentNode.removeChild(existing[i]);
      }
      var preset = false;
      for (var j = 0; j < days.length; j += 1) {
        var iso = days[j];
        var btn = document.createElement("button");
        btn.type = "button";
        btn.setAttribute("data-iso", iso);
        btn.textContent = iso === today ? (copy("chip_today") || "今天") : monthDay(iso);
        btn.className = iso === taken ? "on" : "";
        if (iso === taken) preset = true;
        row.insertBefore(btn, pick);
      }
      if (pick) pick.className = preset ? "cal" : "cal on";
    }
    function paintFaces() {
      var row = document.getElementById("slip-faces");
      if (!row) return;
      var nodes = row.children || [];
      for (var i = 0; i < nodes.length; i += 1) {
        var node = nodes[i];
        if (!node.getAttribute) continue;
        var id = node.getAttribute("data-child-id");
        if (!id) continue;
        var on = state.selected.indexOf(String(id)) !== -1;
        node.className = on ? "facep on" : "facep";
        var mark = node.querySelector ? node.querySelector(".ck") : null;
        if (mark) mark.hidden = !on;
      }
    }
    function paintTinies() {
      var row = document.getElementById("slip-tinies");
      if (!row) return;
      while (row.firstChild) row.removeChild(row.firstChild);
      var shown = 0;
      for (var i = 0; i < state.items.length && shown < 3; i += 1) {
        var item = state.items[i];
        var frame = document.createElement("i");
        if (!item.over && item.file && global.URL && global.URL.createObjectURL) {
          if (!item.url) item.url = global.URL.createObjectURL(item.file);
          frame.style.backgroundImage = "url(\"" + item.url + "\")";
        }
        row.appendChild(frame);
        shown += 1;
      }
      var count = document.getElementById("slip-count");
      var n = state.block === "err_batch" ? state.pickedCount : state.items.length;
      if (count) count.textContent = fill("n_zhang", { n: String(n) });
    }
    function paintChip() {
      var label = document.getElementById("slip-chip-lab");
      var chip = document.getElementById("slip-chip");
      var today = root.getAttribute("data-today") || "";
      var solo = root.getAttribute("data-solo") === "1";
      var self = kind() === "self";
      var name = "";
      if (!self && !solo && state.selected.length) {
        var child = findChild(state.selected[0]);
        name = child && child.name ? child.name : "";
      }
      if (label) {
        var dayWord = dateValue() === today ? (copy("chip_today") || "今天") : monthDay(dateValue());
        label.textContent = (self || solo || !name) ? dayWord : dayWord + " · " + name;
      }
      if (chip && chip.classList) {
        chip.classList.toggle("open", state.lifted);
        chip.classList.toggle("self", self);
      }
      var lift = document.getElementById("slip-lift");
      if (lift) lift.hidden = !state.lifted;
    }
    function paintButton() {
      var btn = document.getElementById("photo-submit");
      if (!btn || state.busy) return;
      var today = root.getAttribute("data-today") || "";
      var blocked = submitBlocked({
        kind: kind(),
        selected: state.selected,
        over: state.over > 0,
        ready: readyItems().length > 0,
        batch: state.block === "err_batch",
      });
      btn.disabled = blocked;
      btn.hidden = false;
      if (btn.classList) {
        btn.classList.toggle("dis", blocked);
        btn.classList.toggle("family", kind() === "self");
      }
      btn.textContent = copy(submitKey(today, dateValue()));
    }
    function paintNotice() {
      if (state.busy) return;
      if (state.over > 0) showWarn(copy("err_size"));
      else if (state.block === "err_batch") showWarn(copy("err_batch"));
      else if (kind() === "child" && !state.selected.length && (state.items.length || state.bad)) showWarn(copy("err_no_child"));
      else if (state.bad > 0) showWarn(copy("err_bad"));
      else if (!state.run) showWarn("");
    }
    function paintSlip() {
      paintDays();
      paintFaces();
      paintTinies();
      paintChip();
      paintNotice();
      paintButton();
    }
    function applyDefaults() {
      state.takenOn = root.getAttribute("data-today") || "";
      state.lifted = false;
      var native = document.getElementById("slip-native-date");
      if (native) native.value = state.takenOn;
      if (kind() !== "child") {
        state.selected = [];
        state.anchorSeed = "";
        return;
      }
      var id = resolveDefaultChild({
        children: children,
        pref: readLastTagged(browserStorage(), root.getAttribute("data-uid") || ""),
        viewedId: root.getAttribute("data-viewed") || "",
        singleView: root.getAttribute("data-single-view") === "1",
      });
      state.selected = id ? [id] : [];
      state.anchorSeed = id;
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
      var canvas = document.createElement("canvas");
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
    function openWithFiles(list) {
      if (state.busy) return;
      var gen = state.pickGen + 1;
      state.pickGen = gen;
      releaseItems(state.items);
      state.items = [];
      state.bad = 0;
      state.over = 0;
      state.block = "";
      state.pickedCount = list.length;
      state.run = null;
      applyDefaults();
      showDecision();
      if (batchBlock(list.length)) {
        state.block = "err_batch";
        paintSlip();
        return;
      }
      var index = 0;
      function step() {
        if (gen !== state.pickGen) return;
        if (index >= list.length) {
          paintSlip();
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
      showProgress();
      var ticket = document.getElementById("progress-ticket");
      var text = document.getElementById("progress-text");
      var line = document.getElementById("progress-line");
      if (ticket) ticket.className = "ticket";
      if (text) text.textContent = fill("progress", { i: String(i), n: String(n) });
      if (line) line.style.width = String(width) + "%";
      var go = document.getElementById("fail-go");
      if (go) go.hidden = true;
      var retry = document.getElementById("slip-retry");
      if (retry) retry.hidden = true;
      var hint = document.getElementById("progress-hint");
      if (hint) hint.hidden = true;
    }
    function destination(ids) {
      var anchor = anchorChild(state.selected, state.anchorSeed);
      return landingUrl(
        kind(),
        anchor,
        root.getAttribute("data-uid") || "",
        dateValue(),
        ids,
        root.getAttribute("data-today") || ""
      );
    }
    function rememberAnchor(run) {
      if (kind() !== "child" || !run.successes.length) return;
      var anchor = anchorChild(state.selected, state.anchorSeed);
      writeLastTagged(browserStorage(), root.getAttribute("data-uid") || "", anchor);
    }
    function showSettled(run, planned) {
      state.busy = false;
      rememberAnchor(run);
      var result = outcome(run, planned);
      if (result.redirect) {
        global.location.href = destination(run.successes);
        return;
      }
      if (run.childRequired && result.ok === 0) {
        showDecision();
        showWarn(copy("err_no_child"));
        paintSlip();
        return;
      }
      showProgress();
      var ticket = document.getElementById("progress-ticket");
      var text = document.getElementById("progress-text");
      var go = document.getElementById("fail-go");
      var hint = document.getElementById("progress-hint");
      var retry = document.getElementById("slip-retry");
      if (ticket) ticket.className = "ticket warn fail-done";
      if (text) text.textContent = fill("fail_one", { ok: String(result.ok), fail: String(result.fail) });
      if (go) {
        if (result.failGo) {
          go.hidden = false;
          go.setAttribute("data-href", destination(run.successes));
        } else {
          go.hidden = true;
        }
      }
      var hintText = run.retry ? (root.getAttribute("data-retry-text") || "") : "";
      if (hint) {
        hint.hidden = !hintText;
        hint.textContent = hintText;
      }
      if (retry) {
        var again = !!(run.retry && !run.aborted);
        retry.hidden = !again;
        retry.disabled = !again;
        retry.textContent = copy(submitKey(root.getAttribute("data-today") || "", dateValue()));
        if (retry.classList) {
          retry.classList.toggle("family", kind() === "self");
          retry.classList.toggle("dis", !again);
        }
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
          state.selected.forEach(function (id) { form.append("child_ids", id); });
        }
        xhr.send(form);
      });
    }
    function uploadAll() {
      if (state.busy) return;
      var ready = readyItems();
      if (state.block === "err_batch" || state.over || !ready.length) {
        paintSlip();
        return;
      }
      if (kind() === "child" && !state.selected.length) {
        showWarn(copy("err_no_child"));
        paintButton();
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
    function abandon(event) {
      if (event && event.preventDefault) event.preventDefault();
      if (state.busy) return;
      releaseItems(state.items);
      state.items = [];
      state.bad = 0;
      state.over = 0;
      state.block = "";
      state.pickedCount = 0;
      state.lifted = false;
      state.selected = [];
      state.run = null;
      var dock = document.getElementById("upload-dock");
      if (dock) dock.hidden = true;
      root.hidden = false;
      var progress = document.getElementById("progress-dock");
      if (progress) progress.hidden = true;
    }
    function toggleChild(raw) {
      if (root.getAttribute("data-solo") === "1" || state.busy || kind() !== "child") return;
      var id = String(raw);
      var index = state.selected.indexOf(id);
      if (index === -1) state.selected.push(id);
      else state.selected.splice(index, 1);
      paintSlip();
    }
    function openNativeDate() {
      var input = document.getElementById("slip-native-date");
      if (!input) return;
      if (typeof input.showPicker === "function") {
        var picked = input.showPicker();
        if (picked && typeof picked.catch === "function") picked.catch(function () { input.focus(); });
        return;
      }
      input.focus();
      input.click();
    }
    var faces = document.getElementById("slip-faces");
    if (faces) {
      faces.addEventListener("click", function (event) {
        var node = event.target;
        while (node && node !== faces) {
          if (node.getAttribute && node.getAttribute("data-child-id")) {
            toggleChild(node.getAttribute("data-child-id"));
            return;
          }
          node = node.parentNode;
        }
      });
    }
    var days = document.getElementById("slip-days");
    if (days) {
      days.addEventListener("click", function (event) {
        var node = event.target;
        while (node && node !== days) {
          if (node.id === "slip-pick-day") {
            openNativeDate();
            return;
          }
          if (node.getAttribute && node.getAttribute("data-iso")) {
            state.takenOn = node.getAttribute("data-iso");
            var nativePick = document.getElementById("slip-native-date");
            if (nativePick) nativePick.value = state.takenOn;
            paintSlip();
            return;
          }
          node = node.parentNode;
        }
      });
    }
    var chip = document.getElementById("slip-chip");
    if (chip) {
      chip.addEventListener("click", function () {
        if (state.busy) return;
        state.lifted = !state.lifted;
        paintChip();
      });
    }
    var nativeDate = document.getElementById("slip-native-date");
    if (nativeDate) {
      nativeDate.addEventListener("change", function () {
        if (nativeDate.value) state.takenOn = nativeDate.value;
        paintSlip();
      });
    }
    var fileInput = document.getElementById("photo-file");
    if (fileInput) {
      fileInput.addEventListener("change", function () {
        var files = fileInput.files ? Array.prototype.slice.call(fileInput.files) : [];
        fileInput.value = "";
        if (!files.length) return;
        openWithFiles(files);
      });
    }
    document.addEventListener("click", function (event) {
      var pick = event.target && event.target.closest ? event.target.closest("[data-slip-pick]") : null;
      if (!pick || state.busy) return;
      event.preventDefault();
      var sheet = document.getElementById("fab-sheet");
      if (sheet) sheet.hidden = true;
      if (fileInput) fileInput.click();
    });
    var dismiss = document.getElementById("slip-dismiss");
    if (dismiss) dismiss.addEventListener("click", abandon);
    var submitBtn = document.getElementById("photo-submit");
    if (submitBtn) submitBtn.addEventListener("click", uploadAll);
    var retryBtn = document.getElementById("slip-retry");
    if (retryBtn) retryBtn.addEventListener("click", uploadAll);
    var go = document.getElementById("fail-go");
    if (go) {
      go.addEventListener("click", function () {
        var href = go.getAttribute("data-href") || "";
        if (href) global.location.href = href;
      });
    }
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && state.busy) {
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
    document.body.addEventListener("htmx:afterSwap", function () {
      if (kind() !== "child") return;
      var feed = document.querySelector(".feed");
      if (!feed) return;
      var family = feed.getAttribute("data-family-view") === "1";
      root.setAttribute("data-single-view", family ? "0" : "1");
      root.setAttribute("data-viewed", family ? "" : (feed.getAttribute("data-child-id") || ""));
    });
    paintDays();
  }

  if (global.document.readyState === "loading") global.document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(typeof window !== "undefined" ? window : globalThis);

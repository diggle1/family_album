/* Old-photo tray. Sentences come from data-copy-* only. One upload at a time. */
(function (global) {
  var PEEK_CAP = 3;
  var ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

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

  function isHandDate(value) {
    return ISO_DATE.test(value || "");
  }

  function peekLimit(raw) {
    var n = Number(raw);
    if (!n || n < 1) return PEEK_CAP;
    if (n > PEEK_CAP) return PEEK_CAP;
    return n;
  }

  function groupPiles(rows) {
    var days = {};
    var order = [];
    var nodate = 0;
    var skips = 0;
    var list = rows || [];
    for (var i = 0; i < list.length; i += 1) {
      var row = list[i] || {};
      if (row.skip) {
        skips += 1;
        continue;
      }
      if (!isHandDate(row.onDate)) {
        nodate += 1;
        continue;
      }
      if (!days[row.onDate]) {
        days[row.onDate] = [];
        order.push(row.onDate);
      }
      days[row.onDate].push(i);
    }
    order.sort();
    return { order: order, days: days, nodate: nodate, skips: skips };
  }

  function splitHead(grouped, handDate) {
    var dated = grouped.order.length;
    var pending = grouped.nodate > 0 && !isHandDate(handDate);
    if (dated < 1 && grouped.nodate < 1) return { key: "pile_cnt", map: { n: String(grouped.skips) } };
    if (pending && dated < 1) return { key: "split_lab_all", map: null };
    if (pending) return { key: "split_lab_mix", map: { a: String(dated), b: String(grouped.nodate) } };
    var piles = dated + (grouped.nodate > 0 ? 1 : 0);
    return { key: "split_lab", map: { n: String(piles) } };
  }

  function canSubmit(grouped, handDate, childCount) {
    if (grouped.nodate > 0 && !isHandDate(handDate)) return "nodate";
    if (!childCount) return "child";
    if (grouped.order.length + grouped.nodate + grouped.skips < 1) return "empty";
    return "";
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

  function mapLimit(items, limit, worker) {
    var next = 0;
    var active = 0;
    var results = new Array(items.length);
    var cap = peekLimit(limit);
    return new Promise(function (resolve) {
      function kick() {
        if (!items.length) {
          resolve(results);
          return;
        }
        if (next >= items.length && active === 0) {
          resolve(results);
          return;
        }
        while (active < cap && next < items.length) {
          (function (place) {
            active += 1;
            Promise.resolve()
              .then(function () { return worker(items[place], place); })
              .then(function (value) {
                results[place] = value;
                active -= 1;
                kick();
              }, function () {
                results[place] = null;
                active -= 1;
                kick();
              });
          })(next);
          next += 1;
        }
      }
      kick();
    });
  }

  var api = {
    batchBlock: batchBlock,
    fileSkip: fileSkip,
    toggleChild: toggleChild,
    retryable: retryable,
    whyKey: whyKey,
    finishFrame: finishFrame,
    acceptLate: acceptLate,
    runSerial: runSerial,
    isHandDate: isHandDate,
    peekLimit: peekLimit,
    groupPiles: groupPiles,
    splitHead: splitHead,
    canSubmit: canSubmit,
    mapLimit: mapLimit,
    PEEK_CAP: PEEK_CAP
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
      rows: [],
      urls: [],
      pileUrls: [],
      selected: initial.slice(),
      handDate: "",
      ok: 0,
      okDays: [],
      fails: [],
      cancelled: false,
      epoch: 1,
      xhr: null,
      peekers: [],
      running: false,
      peeking: false
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
    function attr(name) {
      return root.getAttribute(name) || "";
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
    function clearList(key) {
      var list = state[key] || [];
      for (var i = 0; i < list.length; i += 1) URL.revokeObjectURL(list[i]);
      state[key] = [];
    }
    function clearUrls() {
      clearList("urls");
      clearList("pileUrls");
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
    function dayNum(iso) {
      return String(parseInt(iso.slice(8, 10), 10));
    }
    function monthText(iso) {
      return String(parseInt(iso.slice(5, 7), 10)) + attr("data-mo-suffix");
    }
    function weekdayText(iso) {
      var year = parseInt(iso.slice(0, 4), 10);
      var month = parseInt(iso.slice(5, 7), 10);
      var day = parseInt(iso.slice(8, 10), 10);
      var names = attr("data-weekdays");
      var dt = new Date(year, month - 1, day);
      if (dt.getFullYear() !== year || dt.getMonth() !== month - 1 || dt.getDate() !== day) return "";
      var mark = names.charAt(dt.getDay());
      return mark ? attr("data-week-prefix") + mark : "";
    }
    function fullLabel(iso) {
      return iso.slice(0, 4) + attr("data-y-suffix") + String(parseInt(iso.slice(5, 7), 10)) + attr("data-mo-suffix") + dayNum(iso) + attr("data-d-suffix");
    }
    function el(tag, className, text) {
      var node = document.createElement(tag);
      if (className) node.className = className;
      if (text != null) node.textContent = text;
      return node;
    }
    function thumbsFor(indexes, cap) {
      var wrap = el("div", "thumbs sm");
      var shown = 0;
      for (var i = 0; i < indexes.length && shown < cap; i += 1) {
        var row = state.rows[indexes[i]];
        if (!row || !row.file) continue;
        var img = document.createElement("img");
        var url = URL.createObjectURL(row.file);
        state.pileUrls.push(url);
        img.src = url;
        img.alt = "";
        wrap.appendChild(img);
        shown += 1;
      }
      return wrap;
    }
    function openHandPicker() {
      var input = document.getElementById("on-date");
      if (!input) return;
      if (input.showPicker) {
        try { input.showPicker(); return; } catch (err) { /* fall through */ }
      }
      input.focus();
    }
    function paintConfirm() {
      var grouped = groupPiles(state.rows);
      var head = splitHead(grouped, state.handDate);
      var lab = document.getElementById("split-lab");
      if (lab) lab.textContent = head.map ? fill(head.key, head.map) : copy(head.key);
      var sum = document.getElementById("split-sum");
      if (sum) sum.textContent = attr("data-count-prefix") + fill("pile_cnt", { n: String(state.rows.length) });
      var list = document.getElementById("pile-list");
      clearList("pileUrls");
      if (list) list.textContent = "";
      if (list) {
        for (var d = 0; d < grouped.order.length; d += 1) {
          var iso = grouped.order[d];
          var indexes = grouped.days[iso];
          var pile = el("div", "pile");
          var headRow = el("div", "pile-head");
          var tag = el("div", "pile-tag");
          tag.appendChild(el("span", "mo", monthText(iso)));
          tag.appendChild(el("span", "dy", dayNum(iso)));
          tag.appendChild(el("span", "wd", weekdayText(iso)));
          var info = el("div", "pile-info");
          info.appendChild(el("div", "k", attr("data-shot-label")));
          info.appendChild(el("div", "v", fullLabel(iso)));
          info.appendChild(el("div", "cnt", fill("pile_cnt", { n: String(indexes.length) })));
          headRow.appendChild(tag);
          headRow.appendChild(info);
          pile.appendChild(headRow);
          pile.appendChild(thumbsFor(indexes, 4));
          list.appendChild(pile);
        }
        if (grouped.nodate > 0) {
          var needIndexes = [];
          for (var n = 0; n < state.rows.length; n += 1) {
            var item = state.rows[n];
            if (item && !item.skip && !isHandDate(item.onDate)) needIndexes.push(n);
          }
          var picked = isHandDate(state.handDate);
          var need = el("div", picked ? "pile" : "pile need");
          need.setAttribute("data-need", "1");
          var needHead = el("div", "pile-head");
          var needTag = el("div", "pile-tag");
          needTag.appendChild(el("span", "mo", attr("data-hand-mark")));
          if (picked) {
            needTag.appendChild(el("span", "dy", dayNum(state.handDate)));
            needTag.appendChild(el("span", "wd", weekdayText(state.handDate)));
          } else {
            needTag.appendChild(el("span", "dy", copy("date_pick_go")));
          }
          var needInfo = el("div", "pile-info");
          if (picked) {
            needInfo.appendChild(el("div", "k", attr("data-hand-label")));
            needInfo.appendChild(el("div", "v", fullLabel(state.handDate)));
          } else {
            needInfo.appendChild(el("div", "k", copy("pile_need")));
            needInfo.appendChild(el("div", "v", copy("pile_need_hint")));
          }
          needInfo.appendChild(el("div", "cnt", fill("pile_cnt", { n: String(grouped.nodate) })));
          var go = el("div", "pile-go", picked ? copy("date_change") : "\u203A");
          needHead.appendChild(needTag);
          needHead.appendChild(needInfo);
          needHead.appendChild(go);
          need.appendChild(needHead);
          need.appendChild(thumbsFor(needIndexes, 4));
          need.addEventListener("click", openHandPicker);
          list.appendChild(need);
        }
      }
      var hint = document.getElementById("no-today");
      if (hint) hint.hidden = grouped.nodate < 1 || isHandDate(state.handDate);
      var block = canSubmit(grouped, state.handDate, state.selected.length);
      var err = document.getElementById("confirm-error");
      if (err) {
        if (block === "nodate" || block === "child") {
          err.hidden = false;
          err.className = block === "child" ? "li-banner err" : "li-banner warn";
          err.textContent = copy(block === "child" ? "err_child" : "err_nodate");
        } else {
          err.hidden = true;
          err.textContent = "";
        }
      }
      var submit = document.getElementById("submit-btn");
      if (submit) submit.disabled = block !== "";
      paintStickers();
    }
    function sessionId() {
      if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
      return "s-" + String(Date.now());
    }
    function uniqueDays(list) {
      var out = [];
      for (var i = 0; i < list.length; i += 1) {
        if (list[i] && out.indexOf(list[i]) === -1) out.push(list[i]);
      }
      out.sort();
      return out;
    }
    function dayHref() {
      var days = uniqueDays(state.okDays);
      var date = days[0] || state.handDate || "";
      if (!date) return "/";
      if (state.selected.length === 1) {
        return "/children/" + encodeURIComponent(state.selected[0]) + "/days/" + encodeURIComponent(date);
      }
      return "/days/" + encodeURIComponent(date);
    }
    function paintDonePiles(nodeId) {
      var node = document.getElementById(nodeId);
      if (!node) return;
      node.textContent = "";
      var days = uniqueDays(state.okDays);
      var counts = {};
      for (var i = 0; i < state.okDays.length; i += 1) {
        counts[state.okDays[i]] = (counts[state.okDays[i]] || 0) + 1;
      }
      for (var d = 0; d < days.length; d += 1) {
        var iso = days[d];
        var row = el("div", "done-pile-row");
        var tag = el("div", "mini-tag");
        tag.appendChild(el("span", "mo", monthText(iso)));
        tag.appendChild(el("span", "dy", dayNum(iso)));
        var tx = el("div", "tx", fullLabel(iso));
        tx.appendChild(el("span", "", fill("pile_cnt", { n: String(counts[iso]) })));
        row.appendChild(tag);
        row.appendChild(tx);
        node.appendChild(row);
      }
    }
    function resetBatch() {
      state.files = [];
      state.rows = [];
      state.ok = 0;
      state.okDays = [];
      state.fails = [];
      state.cancelled = false;
      state.running = false;
      state.peeking = false;
      state.handDate = "";
      state.selected = initial.slice();
      var input = document.getElementById("on-date");
      if (input) input.value = "";
      paintTray();
      paintStickers();
      show("tray");
    }
    function paintDone() {
      var frame = finishFrame(state.ok, state.fails.length, state.rows.length, state.cancelled);
      var by = fill("by", { who: attr("data-who") });
      var href = dayHref();
      var days = uniqueDays(state.okDays);
      var split = days.length ? fill("done_split", { n: String(days.length) }) : "";
      if (frame === "done_ok") {
        var all = document.getElementById("done-all");
        if (all) all.textContent = fill("done_all", { n: String(state.ok) });
        var splitOk = document.getElementById("done-split-ok");
        if (splitOk) splitOk.textContent = split;
        paintDonePiles("done-piles-ok");
        var byOk = document.getElementById("done-by-ok");
        if (byOk) byOk.textContent = by;
        var seeOk = document.getElementById("go-see-ok");
        if (seeOk) seeOk.setAttribute("href", href);
      } else {
        var okLine = document.getElementById("done-ok");
        if (okLine) okLine.textContent = fill("done_ok", { n: String(state.ok) });
        var splitPart = document.getElementById("done-split-partial");
        if (splitPart) splitPart.textContent = split;
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
        paintDonePiles("done-piles-partial");
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
    function postOne(row, epoch, attempt) {
      return new Promise(function (resolve) {
        if (!acceptLate(epoch, state.epoch) || state.cancelled) {
          resolve({ ignored: true });
          return;
        }
        var file = row.file;
        var claimed = isHandDate(row.onDate) ? row.onDate : state.handDate;
        var body = new FormData();
        body.append("file", file, file.name || "photo");
        body.append("on_date", claimed || "");
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
            resolve({ ok: true, taken: data.taken_on || claimed || "" });
            return;
          }
          if (retryable(xhr.status, data) && attempt < 1) {
            resolve(postOne(row, epoch, attempt + 1));
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
            resolve(postOne(row, epoch, attempt + 1));
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
      var total = state.rows.length;
      if (node) node.textContent = String(settled) + "/" + String(total);
      if (bar) bar.style.width = (total ? Math.round((100 * settled) / total) : 0) + "%";
    }
    function start() {
      if (state.running || state.peeking) return;
      var grouped = groupPiles(state.rows);
      var block = canSubmit(grouped, state.handDate, state.selected.length);
      if (block === "nodate") {
        show("err_nodate");
        return;
      }
      if (block === "child" || block === "empty") {
        show("err_child");
        return;
      }
      var gate = batchBlock(state.rows.length, totalSize(state.files), limits());
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
      state.okDays = [];
      state.fails = [];
      state.session = sessionId();
      var epoch = state.epoch;
      show("progress");
      paintProgress(0);
      var settled = 0;
      runSerial(state.rows, function (row) {
        if (!acceptLate(epoch, state.epoch) || state.cancelled) return null;
        if (row.skip === "mb") {
          state.fails.push({ name: (row.file && row.file.name) || "", why: copy("fail_why_mb") });
          settled += 1;
          paintProgress(settled);
          return null;
        }
        return postOne(row, epoch, 0).then(function (result) {
          if (!result || result.ignored || !acceptLate(epoch, state.epoch)) return null;
          if (result.stop) {
            state.cancelled = true;
            state.epoch += 1;
            show(result.stop);
            state.running = false;
            return null;
          }
          if (result.ok) {
            state.ok += 1;
            if (isHandDate(result.taken)) state.okDays.push(result.taken);
          } else if (result.fail) {
            state.fails.push({ name: (row.file && row.file.name) || "", why: result.why || "" });
          }
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
    function abortPeekers() {
      var list = state.peekers.slice();
      state.peekers = [];
      for (var i = 0; i < list.length; i += 1) {
        try { list[i].abort(); } catch (err) { /* already finished */ }
      }
    }
    function peekOne(file, epoch) {
      return new Promise(function (resolve) {
        if (!acceptLate(epoch, state.epoch)) {
          resolve(null);
          return;
        }
        var body = new FormData();
        body.append("file", file, file.name || "photo");
        var xhr = new XMLHttpRequest();
        state.peekers.push(xhr);
        xhr.open("POST", "/api/families/current/legacy-import/peek-date");
        xhr.onload = function () {
          var data = {};
          try { data = JSON.parse(xhr.responseText || "{}"); } catch (err) { data = {}; }
          if (!acceptLate(epoch, state.epoch)) {
            resolve(null);
            return;
          }
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(isHandDate(data.on_date) ? data.on_date : null);
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
          if (retryable(xhr.status, data)) {
            resolve(null);
            return;
          }
          resolve(null);
        };
        xhr.onerror = function () { resolve(null); };
        xhr.onabort = function () { resolve(null); };
        xhr.send(body);
      });
    }
    function beginPeek() {
      if (state.running || state.peeking) return;
      var gate = batchBlock(state.files.length, totalSize(state.files), limits());
      if (gate === "over50") {
        show("err_over50");
        return;
      }
      if (gate === "batch") {
        show("err_batch_mb");
        return;
      }
      state.peeking = true;
      state.rows = [];
      var pending = [];
      for (var i = 0; i < state.files.length; i += 1) {
        var file = state.files[i];
        if (fileSkip(file.size || 0, limits()) === "mb") {
          state.rows.push({ file: file, onDate: null, skip: "mb" });
        } else {
          state.rows.push({ file: file, onDate: null, skip: "" });
          pending.push(i);
        }
      }
      var epoch = state.epoch;
      var jobs = pending.map(function (index) { return state.rows[index].file; });
      mapLimit(jobs, peekLimit(attr("data-peek-limit")), function (file) {
        return peekOne(file, epoch);
      }).then(function (results) {
        if (!acceptLate(epoch, state.epoch)) return;
        state.peeking = false;
        state.peekers = [];
        var stop = "";
        for (var r = 0; r < results.length; r += 1) {
          var value = results[r];
          if (value && value.stop) stop = value.stop;
        }
        if (stop) {
          show(stop);
          return;
        }
        for (var p = 0; p < pending.length; p += 1) {
          var valueAt = results[p];
          state.rows[pending[p]].onDate = isHandDate(valueAt) ? valueAt : null;
        }
        paintConfirm();
        show("confirm");
      });
    }
    function takeFiles(list) {
      var files = [];
      for (var i = 0; i < list.length; i += 1) files.push(list[i]);
      var gate = batchBlock(files.length, totalSize(files), limits());
      if (gate === "over50") {
        state.files = [];
        state.rows = [];
        paintTray();
        show("err_over50");
        return;
      }
      if (gate === "batch") {
        state.files = [];
        state.rows = [];
        paintTray();
        show("err_batch_mb");
        return;
      }
      if (!files.length) return;
      state.files = files;
      state.rows = [];
      state.handDate = "";
      var input = document.getElementById("on-date");
      if (input) input.value = "";
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
    if (nextBtn) nextBtn.addEventListener("click", beginPeek);
    var dateInput = document.getElementById("on-date");
    if (dateInput) {
      dateInput.value = "";
      dateInput.addEventListener("change", function () {
        state.handDate = dateInput.value || "";
        paintConfirm();
      });
    }
    var row = document.getElementById("sticker-row");
    if (row) {
      row.addEventListener("click", function (event) {
        var node = event.target.closest ? event.target.closest("[data-child-id]") : null;
        if (!node) return;
        state.selected = toggleChild(state.selected, node.getAttribute("data-child-id"), single());
        paintConfirm();
      });
    }
    var submit = document.getElementById("submit-btn");
    if (submit) submit.addEventListener("click", start);
    function onClose() {
      if (state.peeking) {
        state.epoch += 1;
        abortPeekers();
        state.peeking = false;
        show("tray");
        return;
      }
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
        if (target === "confirm") paintConfirm();
        show(target);
      });
    }
    var reOk = document.getElementById("re-ok");
    var rePart = document.getElementById("re-partial");
    if (reOk) reOk.addEventListener("click", resetBatch);
    if (rePart) rePart.addEventListener("click", resetBatch);
    paintStickers();
    show("tray");
  }

  if (global.document.readyState === "loading") global.document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(typeof window !== "undefined" ? window : globalThis);

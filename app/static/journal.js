/* Growth journal client. Copy lives in data-copy-* attributes, not in this file.
   bodyLen counts Unicode code points and ignores CR/LF. Do not count with UTF-16 length. */

function bodyLen(s) {
  return Array.from(s.replace(/[\r\n]/g, "")).length;
}

function isBlank(s) {
  return s.replace(/[\r\n\t \u3000]/g, "") === "";
}

function parseDay(iso) {
  var matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!matched) return null;
  return { y: +matched[1], m: +matched[2], d: +matched[3] };
}

function ageLabel(birth, on) {
  var b = typeof birth === "string" ? parseDay(birth) : birth;
  var o = typeof on === "string" ? parseDay(on) : on;
  if (!b || !o) return "";
  var years = o.y - b.y;
  var months = o.m - b.m;
  if (o.d < b.d) months -= 1;
  if (months < 0) {
    years -= 1;
    months += 12;
  }
  if (years < 0 || (years === 0 && months <= 0)) return "不足1个月";
  if (years === 0) return String(months) + "个月";
  if (months === 0) return String(years) + "岁";
  return String(years) + "岁" + String(months) + "个月";
}

function weekdayZh(iso) {
  var day = parseDay(iso);
  if (!day) return "";
  var stamp = new Date(Date.UTC(day.y, day.m - 1, day.d));
  var monday = (stamp.getUTCDay() + 6) % 7;
  return "周" + "一二三四五六日"[monday];
}

function newIdem() {
  if (window.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  var bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  var hex = [];
  for (var i = 0; i < bytes.length; i += 1) {
    var piece = bytes[i].toString(16);
    hex.push(piece.length === 1 ? "0" + piece : piece);
  }
  var raw = hex.join("");
  return raw.slice(0, 8) + "-" + raw.slice(8, 12) + "-" + raw.slice(12, 16) + "-" + raw.slice(16, 20) + "-" + raw.slice(20);
}

var DRAFT_PREFIX = "kids.journal.draft.";

function draftKeyFor(fid, uid, journalId) {
  if (journalId) return DRAFT_PREFIX + fid + "." + uid + ".edit." + journalId;
  return DRAFT_PREFIX + fid + "." + uid + ".new";
}

if (typeof document !== "undefined") {
  var pendingDiscard = null;
  var discardTimer = null;
  var saveTimer = null;
  var photoUrls = {};
  var state = {
    body: "",
    date: "",
    childId: null,
    photoIds: [],
    idemKey: "",
    baseUpdatedAt: "",
    locked: false,
    sending: false,
  };

  function app() {
    return document.getElementById("journal-app");
  }

  function copy(key) {
    var node = app();
    if (!node) return "";
    return node.getAttribute("data-copy-" + key) || "";
  }

  function fid() {
    return app().getAttribute("data-fid") || "";
  }

  function uid() {
    return app().getAttribute("data-uid") || "";
  }

  function journalId() {
    return app().getAttribute("data-journal-id") || "";
  }

  function mode() {
    return app().getAttribute("data-mode") || "";
  }

  function today() {
    return app().getAttribute("data-today") || "";
  }

  function maxBody() {
    var n = parseInt(app().getAttribute("data-body-max") || "", 10);
    return n > 0 ? n : 2000;
  }

  function warnBody() {
    var n = parseInt(app().getAttribute("data-body-warn") || "", 10);
    return n > 0 ? n : 1900;
  }

  function draftKey() {
    var id = mode() === "edit" || mode() === "recover" ? journalId() : "";
    return draftKeyFor(fid(), uid(), id);
  }

  function newKey() {
    return draftKeyFor(fid(), uid(), "");
  }

  function readDraft(key) {
    try {
      var raw = localStorage.getItem(key);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      return parsed && parsed.v === 1 ? parsed : null;
    } catch (err) {
      return null;
    }
  }

  function draftEmpty(draft) {
    if (!draft) return true;
    var photos = draft.photoIds || [];
    return isBlank(draft.body || "") && photos.length === 0;
  }

  function findChild(id) {
    var list = window.__journalChildren || [];
    for (var i = 0; i < list.length; i += 1) {
      if (String(list[i].id) === String(id)) return list[i];
    }
    return null;
  }

  function showSheet(id) {
    var node = document.getElementById(id);
    if (node) node.hidden = false;
  }

  function hideSheet(id) {
    var node = document.getElementById(id);
    if (node) node.hidden = true;
  }

  function showPlainToast(text) {
    if (!text) return;
    var old = document.getElementById("toast");
    if (old) old.remove();
    var host = document.querySelector(".app") || document.body;
    var el = document.createElement("div");
    el.className = "toast";
    el.id = "toast";
    el.textContent = text;
    host.appendChild(el);
    setTimeout(function () {
      if (el.parentNode) el.remove();
    }, 2000);
  }

  function hideToast() {
    var old = document.getElementById("toast");
    if (old) old.remove();
  }

  function showUndoToast() {
    hideToast();
    var host = document.querySelector(".app") || document.body;
    var el = document.createElement("div");
    el.className = "toast";
    el.id = "toast";
    var label = document.createElement("span");
    label.textContent = copy("draft_cleared") + " · ";
    var undo = document.createElement("button");
    undo.type = "button";
    undo.className = "toast-undo";
    undo.textContent = copy("draft_undo");
    undo.addEventListener("click", undoDiscard);
    el.appendChild(label);
    el.appendChild(undo);
    host.appendChild(el);
    if (discardTimer) clearTimeout(discardTimer);
    discardTimer = setTimeout(function () {
      pendingDiscard = null;
      hideToast();
    }, 4000);
  }

  function undoDiscard() {
    if (!pendingDiscard) return;
    localStorage.setItem(pendingDiscard.key, pendingDiscard.value);
    var saved = null;
    try {
      saved = JSON.parse(pendingDiscard.value);
    } catch (err) {
      saved = null;
    }
    pendingDiscard = null;
    if (discardTimer) clearTimeout(discardTimer);
    hideToast();
    if (saved) {
      state.locked = false;
      applyPayload(saved);
      paint();
    }
  }

  function currentPayload() {
    return {
      v: 1,
      body: state.body,
      date: state.date,
      childId: state.childId,
      photoIds: state.photoIds.slice(),
      idemKey: state.idemKey,
      baseUpdatedAt: state.baseUpdatedAt || app().getAttribute("data-updated-at") || "",
      savedAt: Date.now(),
    };
  }

  function saveDraftNow() {
    if (!app() || state.locked || pendingDiscard) return;
    if (mode() !== "new" && mode() !== "edit") return;
    var key = draftKey();
    if (isBlank(state.body) && state.photoIds.length === 0) {
      localStorage.removeItem(key);
      return;
    }
    var payload = currentPayload();
    if (mode() !== "edit") delete payload.baseUpdatedAt;
    localStorage.setItem(key, JSON.stringify(payload));
  }

  function scheduleSave() {
    if (state.locked) return;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDraftNow, 300);
  }

  function applyPayload(payload) {
    state.body = payload.body || "";
    state.date = payload.date || state.date || today();
    state.childId = payload.childId || null;
    state.photoIds = (payload.photoIds || []).slice();
    state.idemKey = payload.idemKey || state.idemKey || newIdem();
    state.baseUpdatedAt = payload.baseUpdatedAt || state.baseUpdatedAt || "";
  }

  function countText(n) {
    var max = maxBody();
    var warn = warnBody();
    if (n >= warn && n <= max) {
      var fmt = app().getAttribute("data-near-fmt") || "";
      return fmt.replace("{left}", String(max - n)).replace("{n}", String(n));
    }
    return (copy("w_count") || "{n}/2000").replace("{n}", String(n));
  }

  function paintCount() {
    var node = document.getElementById("journal-count");
    if (!node) return;
    var n = bodyLen(state.body || "");
    node.textContent = countText(n);
    node.className = "jcount";
    if (n > maxBody()) node.className = "jcount over";
    else if (n >= warnBody()) node.className = "jcount near";
  }

  function paintHead() {
    var child = findChild(state.childId);
    var name = document.getElementById("child-name");
    var avatar = document.getElementById("child-avatar");
    var age = document.getElementById("child-age");
    var head = document.getElementById("letterhead");
    if (name) name.textContent = child ? child.name : copy("child_pick");
    if (avatar) avatar.textContent = child && child.name ? child.name.slice(0, 1) : "？";
    if (age) {
      var next = child ? ageLabel(child.birth, state.date) : "";
      if (age.textContent !== next) {
        age.textContent = next;
        age.classList.add("flash");
        setTimeout(function () { age.classList.remove("flash"); }, 300);
      }
    }
    if (head) head.classList.toggle("bad", !state.childId);
    var label = document.getElementById("date-label");
    if (label && state.date) {
      var day = parseDay(state.date);
      var fmt = copy("date_fmt") || "{y}年{mo}月{d}日 {wd}";
      label.textContent = fmt
        .replace("{y}", String(day.y))
        .replace("{mo}", String(day.m))
        .replace("{d}", String(day.d))
        .replace("{wd}", weekdayZh(state.date));
    }
  }

  function paintFilm() {
    var row = document.getElementById("film-row");
    var label = document.getElementById("film-label");
    if (!row) return;
    if (label) {
      label.textContent = (copy("w_photos") || "").replace("{n}", String(state.photoIds.length));
    }
    row.textContent = "";
    state.photoIds.forEach(function (id) {
      var cell = document.createElement("div");
      cell.className = "fr";
      var img = document.createElement("img");
      img.src = photoUrls[id] || ("/media/" + id);
      img.alt = "";
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "x";
      remove.textContent = "✕";
      remove.addEventListener("click", function () {
        state.photoIds = state.photoIds.filter(function (item) { return item !== id; });
        scheduleSave();
        paint();
      });
      cell.appendChild(img);
      cell.appendChild(remove);
      row.appendChild(cell);
    });
    if (state.photoIds.length < 9) {
      var add = document.createElement("button");
      add.type = "button";
      add.className = "fr add";
      add.textContent = "＋ " + (copy("w_photos_add") || "");
      add.addEventListener("click", openPicker);
      row.appendChild(add);
    }
  }

  function paintButton() {
    var button = document.getElementById("journal-submit");
    if (!button) return;
    if (state.sending) {
      button.textContent = copy("w_sending");
      button.disabled = true;
      return;
    }
    button.textContent = mode() === "edit" ? copy("w_submit_edit") : copy("w_submit");
    var blocked = state.locked || bodyLen(state.body || "") > maxBody() || isBlank(state.body) || !state.childId;
    button.disabled = blocked;
  }

  function paint() {
    var field = document.getElementById("journal-body");
    if (field && field.value !== state.body) field.value = state.body;
    if (field) {
      field.readOnly = !!state.locked;
      field.classList.toggle("ghosted", !!state.locked);
    }
    paintCount();
    paintHead();
    paintFilm();
    paintButton();
    var done = document.getElementById("picker-done");
    if (done) done.textContent = (copy("pick_done") || "").replace("{n}", String(state.photoIds.length));
  }

  function showError(text, retry) {
    var node = document.getElementById("journal-error");
    if (!node) return;
    node.hidden = !text;
    node.textContent = text || "";
    if (retry) node.setAttribute("data-retry", "1");
    else node.removeAttribute("data-retry");
  }

  function clientError() {
    if (isBlank(state.body)) return copy("err_empty");
    var n = bodyLen(state.body);
    if (n > maxBody()) return copy("err_too_long").replace("{n}", String(n - maxBody()));
    if (!state.childId) return copy("err_no_child");
    var child = findChild(state.childId);
    if (child && state.date < child.birth) return copy("err_date_birth").replace("{name}", child.name);
    if (state.date > today()) return copy("err_date_future");
    if (state.photoIds.length > 9) return copy("err_photo_max");
    return "";
  }

  function fillKeep(editDraft, newDraft) {
    var editMeta = document.querySelector("[data-keep-edit-meta]");
    var newMeta = document.querySelector("[data-keep-new-meta]");
    var editBody = document.querySelector("[data-keep-edit-body]");
    var newBody = document.querySelector("[data-keep-new-body]");
    function md(iso) {
      var day = parseDay(iso || "");
      if (!day) return "";
      return (copy("date_short") || "{mo}月{d}日").replace("{mo}", String(day.m)).replace("{d}", String(day.d));
    }
    if (editMeta) editMeta.textContent = copy("keep_edit_meta").replace("{md}", md(editDraft.date));
    if (newMeta) newMeta.textContent = copy("keep_new_meta").replace("{md}", md(newDraft.date));
    if (editBody) editBody.textContent = editDraft.body || "";
    if (newBody) newBody.textContent = newDraft.body || "";
    document.querySelectorAll("[data-keep]").forEach(function (node) {
      node.classList.toggle("on", node.getAttribute("data-keep") === "edit");
    });
  }

  function moveEditToNew(source) {
    var payload = {
      v: 1,
      body: source.body || "",
      date: source.date || today(),
      childId: source.childId || null,
      photoIds: (source.photoIds || []).slice(),
      idemKey: newIdem(),
      savedAt: Date.now(),
    };
    localStorage.setItem(newKey(), JSON.stringify(payload));
    if (journalId()) localStorage.removeItem(draftKeyFor(fid(), uid(), journalId()));
  }

  function onSpent(source) {
    var fresh = readDraft(newKey());
    if (draftEmpty(fresh)) {
      moveEditToNew(source);
      showSheet("deleted-sheet");
      return;
    }
    fillKeep(source, fresh);
    showSheet("keep-sheet");
  }

  function explain403(data) {
    if (data && data.code === "journal_readonly") {
      showError(copy("err_demoted"), false);
      var button = document.getElementById("journal-submit");
      if (button) button.hidden = true;
      return;
    }
    fetch("/api/me", { credentials: "same-origin" }).then(function (res) {
      if (res.status === 401) {
        var next = location.pathname + location.search;
        location.href = "/?next=" + encodeURIComponent(next);
        return null;
      }
      return res.json().then(function (me) {
        if (res.status === 200 && me && !me.membership) {
          showPlainToast(copy("e_removed"));
          setTimeout(function () { location.href = "/"; }, 700);
          return;
        }
        showError(copy("err_offline"), true);
      });
    }, function () {
      showError(copy("err_offline"), true);
    });
  }

  function mapError(status, data) {
    var code = data && data.code;
    if (isRetryable(status, data)) return { text: copy("err_offline"), retry: true };
    if (status === 403) {
      explain403(data);
      return null;
    }
    if (status === 409 && code === "journal_key_spent") return { spent: true };
    if (status === 404 && code === "journal_not_found") return { spent: true };
    if (code === "body_too_long") {
      var overflow = data && typeof data.count === "number" ? data.count - (data.max || maxBody()) : bodyLen(state.body) - maxBody();
      return { text: copy("err_too_long").replace("{n}", String(overflow)), retry: false };
    }
    if (code === "body_invalid" || code === "body_empty") return { text: copy("err_empty"), retry: false };
    if (code === "child_required") return { text: copy("err_no_child"), retry: false };
    if (code === "child_not_found") {
      state.childId = null;
      return { text: copy("e_kid_gone"), retry: false };
    }
    if (code === "date_invalid" || code === "date_future") return { text: copy("err_date_future"), retry: false };
    if (code === "date_before_birth") {
      var child = findChild(state.childId);
      return { text: copy("err_date_birth").replace("{name}", child ? child.name : ""), retry: false };
    }
    if (code === "too_many_photos") return { text: copy("err_photo_max"), retry: false };
    if (code === "photo_not_in_family") {
      var bad = (data && data.invalid_ids) || [];
      state.photoIds = state.photoIds.filter(function (id) { return bad.indexOf(id) === -1; });
      return { text: copy("err_photo_foreign"), retry: false };
    }
    return { text: copy("err_offline"), retry: true };
  }

  function finishSuccess(data) {
    localStorage.removeItem(draftKey());
    pendingDiscard = null;
    var child = data && data.child ? data.child.id : state.childId;
    if (mode() === "edit" && data && data.id) {
      location.href = "/journals/" + data.id + "?jtoast=edited";
      return;
    }
    var when = data && data.date_label ? data.date_label : "";
    location.href = "/?child=" + child + "&jtoast=posted&jdate=" + encodeURIComponent(when);
  }

  function handleResult(status, data) {
    state.sending = false;
    if (status === 200 || status === 201) {
      finishSuccess(data);
      return;
    }
    var mapped = mapError(status, data);
    paintButton();
    paint();
    if (!mapped) return;
    if (mapped.spent) {
      onSpent(currentPayload());
      return;
    }
    showError(mapped.text, mapped.retry);
  }

  function isRetryable(status, data) {
    if (!status || status >= 500) return true;
    if (status === 404 && !(data && data.code)) return true;
    return false;
  }

  function submitJournal() {
    if (state.locked || state.sending) return;
    var err = clientError();
    if (err) {
      showError(err, false);
      return;
    }
    showError("", false);
    state.sending = true;
    paintButton();
    saveDraftNow();
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 20000);
    var url = mode() === "edit" ? "/api/journals/" + journalId() : "/api/journals";
    fetch(url, {
      method: mode() === "edit" ? "PUT" : "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": state.idemKey,
      },
      body: JSON.stringify({
        child_id: state.childId,
        on_date: state.date,
        body: state.body,
        photo_ids: state.photoIds,
      }),
      signal: controller.signal,
    }).then(function (res) {
      clearTimeout(timer);
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (err) { data = null; }
        }
        handleResult(res.status, data);
      });
    }, function () {
      clearTimeout(timer);
      state.sending = false;
      paintButton();
      showError(copy("err_offline"), true);
    });
  }

  function markPicker() {
    var full = state.photoIds.length >= 9;
    document.querySelectorAll("#picker-body [data-photo-id]").forEach(function (node) {
      var id = Number(node.getAttribute("data-photo-id"));
      var on = state.photoIds.indexOf(id) !== -1;
      node.classList.toggle("on", on);
      node.classList.toggle("dis", full && !on);
      var badge = node.querySelector(".n");
      if (badge) badge.textContent = on ? String(state.photoIds.indexOf(id) + 1) : "";
    });
  }

  function loadPicker(before) {
    var url = "/fragments/journal/picker" + (before ? "?before=" + encodeURIComponent(before) : "");
    fetch(url, { credentials: "same-origin" }).then(function (res) {
      return res.text();
    }).then(function (html) {
      var box = document.getElementById("picker-body");
      if (!box) return;
      if (before) {
        var more = box.querySelector("[data-picker-more]");
        if (more) more.remove();
        box.insertAdjacentHTML("beforeend", html);
      } else {
        box.innerHTML = html;
      }
      markPicker();
    });
  }

  function openPicker() {
    if (state.photoIds.length >= 9) {
      showError(copy("err_photo_max"), false);
      return;
    }
    showSheet("picker-sheet");
    loadPicker("");
  }

  function bindDateSheet() {
    var year = document.getElementById("date-year");
    var month = document.getElementById("date-month");
    var day = document.getElementById("date-day");
    if (!year || !month || !day) return;
    var current = parseDay(state.date || today());
    var child = findChild(state.childId);
    var min = child ? parseDay(child.birth) : { y: current.y - 10, m: 1, d: 1 };
    var max = parseDay(today());
    year.textContent = "";
    for (var y = min.y; y <= max.y; y += 1) {
      var opt = document.createElement("option");
      opt.value = String(y);
      opt.textContent = String(y);
      year.appendChild(opt);
    }
    month.textContent = "";
    for (var m = 1; m <= 12; m += 1) {
      var mopt = document.createElement("option");
      mopt.value = String(m);
      mopt.textContent = String(m);
      month.appendChild(mopt);
    }
    function refillDays() {
      var y = Number(year.value);
      var mth = Number(month.value);
      var last = new Date(Date.UTC(y, mth, 0)).getUTCDate();
      var selected = Number(day.value) || current.d;
      day.textContent = "";
      for (var d = 1; d <= last; d += 1) {
        var iso = y + "-" + String(mth).padStart(2, "0") + "-" + String(d).padStart(2, "0");
        var opt = document.createElement("option");
        opt.value = String(d);
        opt.textContent = String(d);
        var disabled = (child && iso < child.birth) || iso > today();
        opt.disabled = disabled;
        day.appendChild(opt);
      }
      day.value = String(Math.min(selected, last));
      var chosen = y + "-" + String(mth).padStart(2, "0") + "-" + String(Number(day.value)).padStart(2, "0");
      var bad = (child && chosen < child.birth) || chosen > today();
      var hint = document.getElementById("date-sheet-err");
      var ok = document.getElementById("date-ok");
      if (hint) {
        hint.hidden = !bad;
        if (child && chosen < child.birth) hint.textContent = copy("err_date_birth").replace("{name}", child.name);
        else if (chosen > today()) hint.textContent = copy("err_date_future");
      }
      if (ok) ok.disabled = bad;
    }
    year.value = String(current.y);
    month.value = String(current.m);
    refillDays();
    day.value = String(current.d);
    refillDays();
    year.onchange = refillDays;
    month.onchange = refillDays;
    day.onchange = refillDays;
    var okBtn = document.getElementById("date-ok");
    if (okBtn) {
      okBtn.onclick = function () {
        if (okBtn.disabled) return;
        var iso = year.value + "-" + String(month.value).padStart(2, "0") + "-" + String(day.value).padStart(2, "0");
        state.date = iso;
        hideSheet("date-sheet");
        scheduleSave();
        paint();
      };
    }
  }

  function bindChildren() {
    var box = document.getElementById("child-options");
    if (!box) return;
    box.textContent = "";
    (window.__journalChildren || []).forEach(function (child) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "opt" + (String(child.id) === String(state.childId) ? " on" : "");
      var name = document.createElement("b");
      name.textContent = child.name;
      button.appendChild(name);
      button.addEventListener("click", function () {
        state.childId = child.id;
        hideSheet("child-sheet");
        scheduleSave();
        paint();
      });
      box.appendChild(button);
    });
  }

  function showDraftBar(draft) {
    var bar = document.getElementById("draft-bar");
    var tag = document.getElementById("draft-tag");
    if (!bar || !tag) return;
    bar.hidden = false;
    if (mode() === "edit") {
      var day = parseDay(draft.date || "");
      var md = day ? (copy("date_short") || "").replace("{mo}", String(day.m)).replace("{d}", String(day.d)) : "";
      tag.textContent = copy("draft_label_edit").replace("{md}", md);
    } else {
      tag.textContent = copy("draft_label");
    }
  }

  function bootWrite() {
    var initialNode = document.getElementById("journal-initial");
    var childNode = document.getElementById("journal-children-data");
    window.__journalChildren = childNode ? JSON.parse(childNode.textContent || "[]") : [];
    var initial = initialNode ? JSON.parse(initialNode.textContent || "{}") : {};
    (initial.photos || []).forEach(function (photo) { photoUrls[photo.id] = photo.url; });
    applyPayload({
      body: initial.body || "",
      date: initial.date || today(),
      childId: initial.childId,
      photoIds: initial.photoIds || [],
      idemKey: newIdem(),
      baseUpdatedAt: initial.updatedAt || "",
    });
    var stored = readDraft(draftKey());
    if (!draftEmpty(stored)) {
      state.locked = true;
      applyPayload(stored);
      showDraftBar(stored);
    }
    paint();
    var field = document.getElementById("journal-body");
    if (field) {
      field.addEventListener("input", function () {
        if (state.locked) return;
        state.body = field.value;
        paintCount();
        paintButton();
        scheduleSave();
      });
    }
    var resume = document.getElementById("draft-resume");
    if (resume) {
      resume.addEventListener("click", function () {
        state.locked = false;
        var bar = document.getElementById("draft-bar");
        if (bar) bar.hidden = true;
        paint();
        if (field) field.focus();
      });
    }
    var discard = document.getElementById("draft-discard");
    if (discard) {
      discard.addEventListener("click", function () {
        var key = draftKey();
        var raw = localStorage.getItem(key);
        localStorage.removeItem(key);
        pendingDiscard = raw ? { key: key, value: raw } : null;
        state.locked = false;
        state.body = "";
        state.photoIds = [];
        state.idemKey = newIdem();
        var bar = document.getElementById("draft-bar");
        if (bar) bar.hidden = true;
        paint();
        if (pendingDiscard) showUndoToast();
      });
    }
    var submit = document.getElementById("journal-submit");
    if (submit) submit.addEventListener("click", submitJournal);
    var error = document.getElementById("journal-error");
    if (error) {
      error.addEventListener("click", function () {
        if (error.getAttribute("data-retry") === "1") submitJournal();
      });
    }
    var dateOpen = document.getElementById("date-open");
    if (dateOpen) {
      dateOpen.addEventListener("click", function () {
        if (state.locked) return;
        bindDateSheet();
        showSheet("date-sheet");
      });
    }
    var childSwitch = document.getElementById("child-switch");
    if (childSwitch) {
      childSwitch.addEventListener("click", function () {
        if (state.locked) return;
        bindChildren();
        showSheet("child-sheet");
      });
    }
    document.querySelectorAll("[data-sheet-close]").forEach(function (node) {
      node.addEventListener("click", function () {
        hideSheet("child-sheet");
        hideSheet("date-sheet");
        hideSheet("picker-sheet");
      });
    });
    var pickerBody = document.getElementById("picker-body");
    if (pickerBody) {
      pickerBody.addEventListener("click", function (event) {
        var more = event.target.closest("[data-picker-more]");
        if (more) {
          loadPicker(more.getAttribute("data-before"));
          return;
        }
        var cell = event.target.closest("[data-photo-id]");
        if (!cell || cell.classList.contains("dis")) {
          if (cell && cell.classList.contains("dis")) showPlainToast(copy("err_photo_max"));
          return;
        }
        var id = Number(cell.getAttribute("data-photo-id"));
        var img = cell.querySelector("img");
        if (img) photoUrls[id] = img.getAttribute("src");
        var index = state.photoIds.indexOf(id);
        if (index === -1) {
          if (state.photoIds.length >= 9) return;
          state.photoIds.push(id);
        } else {
          state.photoIds.splice(index, 1);
        }
        markPicker();
        scheduleSave();
        paint();
      });
    }
    var pickerDone = document.getElementById("picker-done");
    if (pickerDone) pickerDone.addEventListener("click", function () { hideSheet("picker-sheet"); });
    document.querySelectorAll("[data-deleted-go]").forEach(function (node) {
      node.addEventListener("click", function () { location.href = "/journals/new"; });
    });
    document.querySelectorAll("[data-keep]").forEach(function (node) {
      node.addEventListener("click", function () {
        document.querySelectorAll("[data-keep]").forEach(function (item) { item.classList.remove("on"); });
        node.classList.add("on");
      });
    });
    var keepOk = document.querySelector("[data-keep-ok]");
    if (keepOk) {
      keepOk.addEventListener("click", function () {
        var chosen = document.querySelector("[data-keep].on");
        var which = chosen ? chosen.getAttribute("data-keep") : "edit";
        if (which === "edit") moveEditToNew(currentPayload());
        else if (journalId()) localStorage.removeItem(draftKeyFor(fid(), uid(), journalId()));
        location.href = "/journals/new";
      });
    }
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && !pendingDiscard) saveDraftNow();
    });
    window.addEventListener("pagehide", function () {
      if (pendingDiscard) {
        pendingDiscard = null;
        return;
      }
      saveDraftNow();
    });
    window.addEventListener("offline", function () {
      if (!pendingDiscard) saveDraftNow();
    });
    window.addEventListener("storage", function (event) {
      if (event.key === draftKey() && !event.newValue) showPlainToast(copy("draft_cleared"));
    });
  }

  function bootRecover() {
    var edit = readDraft(draftKey());
    if (draftEmpty(edit)) return;
    var fresh = readDraft(newKey());
    if (draftEmpty(fresh)) {
      moveEditToNew(edit);
      showSheet("deleted-sheet");
      return;
    }
    state.body = edit.body || "";
    state.date = edit.date || "";
    state.childId = edit.childId || null;
    state.photoIds = (edit.photoIds || []).slice();
    fillKeep(edit, fresh);
    showSheet("keep-sheet");
    document.querySelectorAll("[data-deleted-go]").forEach(function (node) {
      node.addEventListener("click", function () { location.href = "/journals/new"; });
    });
    var keepOk = document.querySelector("[data-keep-ok]");
    if (keepOk) {
      keepOk.addEventListener("click", function () {
        var chosen = document.querySelector("[data-keep].on");
        var which = chosen ? chosen.getAttribute("data-keep") : "edit";
        if (which === "edit") moveEditToNew(edit);
        else localStorage.removeItem(draftKey());
        location.href = "/journals/new";
      });
    }
  }

  function bootDetail() {
    var more = document.querySelector("[data-journal-more]");
    var menu = document.getElementById("journal-menu");
    if (more && menu) {
      more.addEventListener("click", function (event) {
        event.stopPropagation();
        if (menu.hasAttribute("hidden")) menu.removeAttribute("hidden");
        else menu.setAttribute("hidden", "");
      });
    }
    var del = document.querySelector("[data-journal-delete]");
    if (del) {
      del.addEventListener("click", function () {
        if (menu) menu.setAttribute("hidden", "");
        showSheet("delete-sheet");
      });
    }
    var keep = document.querySelector("[data-delete-keep]");
    if (keep) keep.addEventListener("click", function () { hideSheet("delete-sheet"); });
    var ok = document.querySelector("[data-delete-ok]");
    if (ok) {
      ok.addEventListener("click", function () {
        fetch("/api/journals/" + journalId(), { method: "DELETE", credentials: "same-origin" }).then(function (res) {
          return res.text().then(function (text) {
            var data = null;
            if (text) {
              try { data = JSON.parse(text); } catch (err) { data = null; }
            }
            if (res.status === 204 || (res.status === 404 && data && data.code === "journal_not_found")) {
              location.href = "/?child=" + (app().getAttribute("data-child-id") || "") + "&jtoast=deleted";
              return;
            }
            var line = document.getElementById("delete-error");
            if (!line) return;
            line.hidden = false;
            line.textContent = data && data.code === "journal_readonly" ? copy("err_demoted") : copy("err_offline");
          });
        }, function () {
          var line = document.getElementById("delete-error");
          if (!line) return;
          line.hidden = false;
          line.textContent = copy("err_offline");
        });
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!app()) return;
    var kind = mode();
    if (kind === "new" || kind === "edit") bootWrite();
    else if (kind === "recover") bootRecover();
    else if (kind === "detail") bootDetail();
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { bodyLen: bodyLen, isBlank: isBlank, ageLabel: ageLabel };
}

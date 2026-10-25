/* One sentence a day. User-facing sentences stay in data-copy-* attributes. */
function dnFill(template, map) {
  return String(template || "").replace(/\{(\w+)\}/g, function (_match, key) {
    return Object.prototype.hasOwnProperty.call(map, key) ? String(map[key]) : "";
  });
}

function countState(text, displayCap, hardCap, warnAt) {
  var trimmed = String(text == null ? "" : text).trim();
  var n = Array.from(trimmed).length;
  var newline = /[\r\n]/.test(trimmed);
  var overHard = n > hardCap || newline;
  return {
    n: newline ? n : n,
    warn: !overHard && n >= warnAt && n <= displayCap,
    over: n > displayCap,
    blocked: n === 0 || overHard,
    newline: newline,
  };
}

function toggleIds(ids, childId, childCount) {
  var current = (ids || []).slice();
  var index = -1;
  for (var i = 0; i < current.length; i += 1) {
    if (String(current[i]) === String(childId)) index = i;
  }
  if (index === -1) {
    if (childCount <= 1) return { ids: [Number(childId)], blocked: false };
    current.push(Number(childId));
    return { ids: current, blocked: false };
  }
  if (current.length <= 1) return { ids: current, blocked: true };
  current.splice(index, 1);
  return { ids: current, blocked: false };
}

function decideDayNote(status, data, ctx) {
  ctx = ctx || {};
  var late = !!(ctx.left || ctx.settledEmpty || (ctx.generation !== undefined && ctx.generation !== ctx.currentGeneration));
  var base = { apply: false, ignore: false, ok: false, crash: false, retry: false, hideWrite: false, clearLocal: false, kind: "fail" };
  if (late) return Object.assign({}, base, { ignore: true, kind: "late" });
  if (!status) return Object.assign({}, base, { retry: true, kind: "offline" });
  if (status === 503 || status >= 500) return Object.assign({}, base, { retry: true, kind: "busy" });
  if (status === 404 && !(data && data.code)) return Object.assign({}, base, { retry: true, kind: "retryable" });
  if (status === 404) return Object.assign({}, base, { kind: "missing" });
  if (status === 403 && data && data.code === "day_note_readonly") {
    return Object.assign({}, base, { hideWrite: true, kind: "readonly" });
  }
  if (status === 403) return Object.assign({}, base, { kind: "denied" });
  if (status === 200 || status === 201) return Object.assign({}, base, { apply: true, ok: true, kind: "saved" });
  if (status === 422) return Object.assign({}, base, { kind: "invalid" });
  return base;
}

function messageFor(code, copy) {
  if (code === "body_empty" || code === "body_invalid") return copy("err_empty");
  if (code === "body_too_long" || code === "body_has_newline") return copy("err_too_long");
  if (code === "child_required" || code === "child_invalid") return copy("err_no_child");
  if (code === "day_note_readonly" || code === "day_note_not_author") return copy("err_perm");
  return "";
}

var dnLeft = false;
var dnGeneration = 1;

function bootDayNote() {
  var root = document.getElementById("day-note-app");
  if (!root || root.getAttribute("data-booted") === "1" || root.getAttribute("data-can-write") !== "1") return;
  root.setAttribute("data-booted", "1");
  var initialNode = document.getElementById("day-note-initial");
  var childrenNode = document.getElementById("day-note-children");
  var initial = initialNode ? JSON.parse(initialNode.textContent || "{}") : {};
  var children = childrenNode ? JSON.parse(childrenNode.textContent || "[]") : [];
  var state = {
    body: initial.body || "",
    childIds: (initial.childIds || []).slice(),
    noteId: initial.noteId || null,
    mode: initial.mode || "new",
    sending: false,
    open: false,
  };

  function copy(key) {
    return root.getAttribute("data-copy-" + key) || "";
  }
  function caps() {
    return {
      display: Number(root.getAttribute("data-body-display") || "80"),
      hard: Number(root.getAttribute("data-body-max") || "200"),
      warn: Number(root.getAttribute("data-body-warn") || "70"),
    };
  }
  function showError(text, sub) {
    var node = document.getElementById("day-note-error");
    var extra = document.getElementById("day-note-error-sub");
    if (node) {
      node.hidden = !text;
      node.textContent = text || "";
    }
    if (extra) {
      extra.hidden = !sub;
      extra.textContent = sub || "";
    }
  }
  function paintCount() {
    var node = document.getElementById("day-note-count");
    if (!node) return;
    var limit = caps();
    var view = countState(state.body, limit.display, limit.hard, limit.warn);
    node.textContent = dnFill(copy("w_count"), { n: view.n });
    node.classList.toggle("warn", !!view.warn);
    node.classList.toggle("over", !!view.over);
  }
  function paintSib() {
    var row = document.getElementById("sib-row");
    var hint = document.getElementById("sib-hint");
    if (!row) return;
    row.textContent = "";
    var single = children.length <= 1;
    children.forEach(function (child, index) {
      var on = state.childIds.some(function (id) { return String(id) === String(child.id); });
      var button = document.createElement("button");
      button.type = "button";
      button.className = "chip" + (on ? " on" : "") + (single && on ? " locked" : "");
      button.setAttribute("data-child-id", String(child.id));
      var av = document.createElement("span");
      av.className = "av c" + String(index % 3);
      av.appendChild(document.createTextNode(String(child.name || "").slice(0, 1)));
      var mark = document.createElement("span");
      mark.className = "ck";
      mark.textContent = "\u2713";
      av.appendChild(mark);
      var name = document.createElement("span");
      name.className = "nm";
      name.textContent = child.name || "";
      button.appendChild(av);
      button.appendChild(name);
      button.addEventListener("click", function () {
        var next = toggleIds(state.childIds, child.id, children.length);
        state.childIds = next.ids;
        paint();
      });
      row.appendChild(button);
    });
    if (!hint) return;
    if (!state.childIds.length) {
      hint.textContent = copy("sib_hint_pick");
      return;
    }
    if (single) {
      hint.textContent = "";
      return;
    }
    var named = children.filter(function (child) { return String(child.id) === String(state.childIds[0]); })[0];
    hint.textContent = dnFill(copy("sib_hint_pre"), { name: named ? named.name : "" });
  }
  function paintButton() {
    var button = document.getElementById("day-note-submit");
    if (!button) return;
    button.textContent = state.mode === "edit" ? copy("w_submit_edit") : copy("w_submit");
    var limit = caps();
    var view = countState(state.body, limit.display, limit.hard, limit.warn);
    button.disabled = state.sending || view.blocked || !state.childIds.length;
  }
  function paintTitle() {
    var title = document.getElementById("day-note-title");
    if (!title) return;
    title.textContent = state.mode === "edit" ? copy("w_title_edit") : copy("w_title_new");
  }
  function paintEntry() {
    var label = state.mode === "edit" ? copy("entry_edit") : copy("entry_write");
    document.querySelectorAll("[data-open-note]").forEach(function (node) {
      node.textContent = label;
    });
  }
  function paint() {
    var field = document.getElementById("day-note-body");
    if (field && field.value !== state.body) field.value = state.body;
    paintCount();
    paintSib();
    paintButton();
    paintTitle();
    paintEntry();
  }
  function openForm() {
    state.open = true;
    var form = document.getElementById("day-note-form");
    if (form) form.hidden = false;
    var dock = document.getElementById("day-note-dock");
    if (dock) dock.hidden = true;
    paint();
    var field = document.getElementById("day-note-body");
    if (field) field.focus();
  }
  function closeForm() {
    state.open = false;
    state.sending = false;
    var form = document.getElementById("day-note-form");
    if (form) form.hidden = true;
    var dock = document.getElementById("day-note-dock");
    if (dock) dock.hidden = false;
    showError("", "");
  }
  function hideWrite() {
    document.querySelectorAll("[data-open-note]").forEach(function (node) { node.hidden = true; });
    var form = document.getElementById("day-note-form");
    var submit = document.getElementById("day-note-submit");
    if (submit) submit.hidden = true;
    if (form) form.hidden = false;
    showError(copy("err_perm"), copy("err_perm_sub"));
  }
  function clientError() {
    var limit = caps();
    var view = countState(state.body, limit.display, limit.hard, limit.warn);
    if (!String(state.body || "").trim()) return copy("err_empty");
    if (view.newline || view.n > limit.hard) return copy("err_too_long");
    if (!state.childIds.length) return copy("err_no_child");
    return "";
  }
  function submit() {
    var problem = clientError();
    if (problem) {
      showError(problem, "");
      return;
    }
    if (state.sending) return;
    state.sending = true;
    paint();
    showError("", "");
    var generation = dnGeneration;
    var url = state.noteId ? "/api/day-notes/" + state.noteId : "/api/families/current/day-notes";
    var method = state.noteId ? "PUT" : "POST";
    var xhr = new XMLHttpRequest();
    xhr.open(method, url, true);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.onerror = function () {
      state.sending = false;
      var decision = decideDayNote(0, null, { left: dnLeft, generation: generation, currentGeneration: dnGeneration });
      if (!decision.ignore && !decision.ok) showError("", "");
      paint();
    };
    xhr.onload = function () {
      state.sending = false;
      var data = null;
      try { data = JSON.parse(xhr.responseText || "null"); } catch (err) { data = null; }
      var decision = decideDayNote(xhr.status, data, { left: dnLeft, generation: generation, currentGeneration: dnGeneration });
      if (decision.ignore || !decision.apply) {
        if (decision.hideWrite) hideWrite();
        else if (!decision.ok) showError(messageFor(data && data.code, copy), decision.hideWrite ? copy("err_perm_sub") : "");
        paint();
        return;
      }
      window.location.reload();
    };
    xhr.send(JSON.stringify({
      on_date: root.getAttribute("data-on-date"),
      body: String(state.body || "").trim(),
      child_ids: state.childIds.map(function (id) { return Number(id); }),
    }));
  }
  function removeNote(id) {
    var generation = dnGeneration;
    var xhr = new XMLHttpRequest();
    xhr.open("DELETE", "/api/day-notes/" + id, true);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.onload = function () {
      var data = null;
      try { data = JSON.parse(xhr.responseText || "null"); } catch (err) { data = null; }
      var decision = decideDayNote(xhr.status === 204 ? 200 : xhr.status, data, { left: dnLeft, generation: generation, currentGeneration: dnGeneration });
      if (decision.ignore || !decision.ok) {
        if (decision.hideWrite) hideWrite();
        else showError(messageFor(data && data.code, copy), "");
        return;
      }
      window.location.reload();
    };
    xhr.send();
  }
  document.querySelectorAll("[data-open-note]").forEach(function (node) {
    node.addEventListener("click", openForm);
  });
  var cancel = document.getElementById("day-note-cancel");
  if (cancel) cancel.addEventListener("click", closeForm);
  var field = document.getElementById("day-note-body");
  if (field) {
    field.addEventListener("input", function () {
      state.body = field.value;
      paintCount();
      paintButton();
    });
  }
  var submitButton = document.getElementById("day-note-submit");
  if (submitButton) submitButton.addEventListener("click", submit);
  root.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.getAttribute) return;
    var id = target.getAttribute("data-delete-note");
    if (id) removeNote(id);
  });
  paint();
}

if (typeof document !== "undefined") {
  document.addEventListener("pagehide", function () {
    dnLeft = true;
    dnGeneration += 1;
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bootDayNote);
  else bootDayNote();
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    decideDayNote: decideDayNote,
    countState: countState,
    toggleIds: toggleIds,
    dnFill: dnFill,
    messageFor: messageFor,
  };
}

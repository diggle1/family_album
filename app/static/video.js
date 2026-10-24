/* Growth video client. User-facing sentences come from data-copy-* only. */

function videoApp() {
  return document.getElementById("video-app");
}

function videoMulti() {
  if (videoMulti.cache) return videoMulti.cache;
  videoMulti.cache = {};
  if (typeof document === "undefined") return videoMulti.cache;
  var node = document.getElementById("multi-child-copy");
  if (!node) return videoMulti.cache;
  try {
    videoMulti.cache = JSON.parse(node.textContent || "{}") || {};
  } catch (err) {
    videoMulti.cache = {};
  }
  return videoMulti.cache;
}

function videoCopy(key) {
  var multi = videoMulti();
  if (multi && Object.prototype.hasOwnProperty.call(multi, key)) return multi[key];
  var node = videoApp();
  if (!node) return "";
  return node.getAttribute("data-copy-" + key) || "";
}

function videoFill(key, map) {
  return videoCopy(key).replace(/\{(\w+)\}/g, function (_all, name) {
    if (!map || map[name] === undefined || map[name] === null) return "";
    return String(map[name]);
  });
}

function videoParseDay(iso) {
  var matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!matched) return null;
  return { y: +matched[1], m: +matched[2], d: +matched[3] };
}

function videoWeekday(iso) {
  var day = videoParseDay(iso);
  if (!day) return "";
  var stamp = new Date(Date.UTC(day.y, day.m - 1, day.d));
  var index = (stamp.getUTCDay() + 6) % 7;
  var letters = (videoApp() && videoApp().getAttribute("data-weekdays")) || "";
  return letters.charAt(index);
}

function videoAge(birth, on) {
  var b = typeof birth === "string" ? videoParseDay(birth) : birth;
  var o = typeof on === "string" ? videoParseDay(on) : on;
  if (!b || !o) return "";
  var years = o.y - b.y;
  var months = o.m - b.m;
  if (o.d < b.d) months -= 1;
  if (months < 0) {
    years -= 1;
    months += 12;
  }
  var node = videoApp();
  var under = node ? node.getAttribute("data-age-under") || "" : "";
  var month = node ? node.getAttribute("data-age-month") || "" : "";
  var year = node ? node.getAttribute("data-age-year") || "" : "";
  if (years < 0 || (years === 0 && months <= 0)) return under;
  if (years === 0) return String(months) + month;
  if (months === 0) return String(years) + year;
  return videoFill("age_fmt", { y: years, m: months });
}

function videoNewIdem() {
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

if (typeof document !== "undefined" && videoApp()) {
  var mode = videoApp().getAttribute("data-mode") || "";
  if (mode === "upload" || mode === "edit") bootUpload();
  if (mode === "detail") bootDetail();
}

function bootUpload() {
  var children = [];
  try {
    children = JSON.parse(document.getElementById("video-children-data").textContent || "[]");
  } catch (err) {
    children = [];
  }
  var selected = [];
  try {
    selected = JSON.parse((document.getElementById("video-selected") || {}).textContent || "[]");
  } catch (err) {
    selected = [];
  }
  if (!selected.length && videoApp().getAttribute("data-child")) {
    selected = [videoApp().getAttribute("data-child")];
  }
  var state = {
    childId: selected.length ? String(selected[0]) : (videoApp().getAttribute("data-child") || ""),
    childIds: selected.map(function (id) { return String(id); }),
    date: videoApp().getAttribute("data-initial-date") || videoApp().getAttribute("data-today") || "",
    file: null,
    durationMs: 0,
    posterBlob: null,
    posterFailed: false,
    idemKey: videoNewIdem(),
    sending: false,
    aborted: false,
    overDur: false,
    overSize: false,
    badFormat: false,
    xhr: null,
    lastHint: "",
  };

  function maxMs() {
    return parseInt(videoApp().getAttribute("data-max-ms") || "60000", 10);
  }
  function maxBytes() {
    return parseInt(videoApp().getAttribute("data-max-bytes") || "0", 10);
  }
  function findChild(id) {
    for (var i = 0; i < children.length; i += 1) {
      if (String(children[i].id) === String(id)) return children[i];
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
  function showError(text) {
    var node = document.getElementById("video-error");
    if (!node) return;
    if (!text) {
      node.hidden = true;
      node.textContent = "";
      return;
    }
    node.hidden = false;
    node.textContent = text;
  }
  function dateLabel(iso) {
    var day = videoParseDay(iso);
    if (!day) return "";
    return videoFill("date_fmt", { y: day.y, mo: day.m, d: day.d, wd: videoWeekday(iso) });
  }
  function paintHead() {
    var child = findChild(state.childId);
    var avatar = document.getElementById("child-avatar");
    var name = document.getElementById("child-name");
    var age = document.getElementById("child-age");
    if (child) {
      if (avatar) avatar.textContent = (child.name || "").slice(0, 1);
      if (name) name.textContent = child.name;
      if (age) age.textContent = videoAge(child.birth, state.date);
    } else if (name) {
      name.textContent = videoCopy("child_pick");
      if (age) age.textContent = "";
    }
    var label = document.getElementById("date-label");
    if (label) label.textContent = dateLabel(state.date);
  }
  function paintMeter() {
    var sel = document.getElementById("meter-sel");
    var dur = document.getElementById("meter-dur");
    var size = document.getElementById("meter-size");
    var durBar = document.getElementById("meter-dur-bar");
    var sizeBar = document.getElementById("meter-size-bar");
    var badge = document.getElementById("film-dur");
    if (!state.file) {
      if (sel) sel.textContent = videoCopy("meter_unpicked");
      if (dur) dur.classList.remove("err");
      if (size) size.classList.remove("err");
      if (durBar) durBar.style.width = "0";
      if (sizeBar) sizeBar.style.width = "0";
      if (badge) badge.hidden = true;
      return;
    }
    var seconds = Math.max(0, Math.round(state.durationMs / 1000));
    var mb = state.file.size / (1024 * 1024);
    var mbText = (Math.round(mb * 10) / 10).toString();
    if (sel) sel.textContent = videoFill("u_picked", { s: seconds, mb: mbText });
    var durPct = Math.min(100, Math.round((state.durationMs / maxMs()) * 100));
    var sizePct = maxBytes() ? Math.min(100, Math.round((state.file.size / maxBytes()) * 100)) : 0;
    if (durBar) durBar.style.width = durPct + "%";
    if (sizeBar) sizeBar.style.width = sizePct + "%";
    if (dur) dur.classList.toggle("err", state.overDur);
    if (size) size.classList.toggle("err", state.overSize);
    if (badge) {
      var ss = String(seconds % 60);
      if (ss.length < 2) ss = "0" + ss;
      badge.hidden = false;
      badge.textContent = videoFill("card_dur", { m: Math.floor(seconds / 60), ss: ss });
    }
  }
  function paintFilm() {
    var ph = document.getElementById("film-ph");
    var img = document.getElementById("poster-img");
    if (!state.file) {
      if (ph) {
        ph.hidden = false;
        ph.textContent = videoCopy("u_pick");
      }
      if (img) {
        img.hidden = true;
        img.removeAttribute("src");
      }
      return;
    }
    if (state.posterFailed || !state.posterBlob) {
      if (img) img.hidden = true;
      if (ph) {
        ph.hidden = false;
        ph.textContent = videoCopy("cover_ph");
      }
      return;
    }
    if (img) {
      img.hidden = false;
      img.src = URL.createObjectURL(state.posterBlob);
    }
    if (ph) ph.hidden = true;
  }
  function selectedChildren() {
    return state.childIds.map(findChild).filter(Boolean);
  }
  function canSubmit() {
    if (state.sending) return false;
    if (videoApp().getAttribute("data-mode") === "edit") {
      return selectedChildren().length > 0 && !clientDateError();
    }
    if (!state.file || state.overDur || state.overSize || state.badFormat) return false;
    if (!selectedChildren().length) return false;
    return !clientDateError();
  }
  function latestChild() {
    var best = null;
    selectedChildren().forEach(function (child) {
      if (!best || String(child.birth) > String(best.birth)) best = child;
    });
    return best;
  }
  function clientDateError() {
    var child = latestChild();
    var day = videoParseDay(state.date);
    var today = videoParseDay(videoApp().getAttribute("data-today") || "");
    if (!day || !today) return videoCopy("err_date_future");
    if (state.date > (videoApp().getAttribute("data-today") || "")) return videoCopy("err_date_future");
    if (child && state.date < child.birth) return videoFill("err_date_birth", { name: child.name });
    return "";
  }
  function paintButton() {
    var button = document.getElementById("video-submit");
    var cancel = document.getElementById("video-cancel");
    var keep = document.getElementById("keep-open");
    var prog = document.getElementById("film-prog");
    if (!button) return;
    button.disabled = !canSubmit();
    var editing = videoApp().getAttribute("data-mode") === "edit";
    button.textContent = state.sending ? videoCopy("u_sending") : (editing ? videoCopy("w_submit_edit") : videoCopy("u_submit"));
    if (cancel) cancel.hidden = !state.sending;
    if (keep) keep.hidden = !state.sending;
    if (prog) prog.hidden = !state.sending;
  }
  function paintSib() {
    var row = document.getElementById("sib-row");
    var hint = document.getElementById("sib-hint");
    if (!row) return;
    var single = children.length <= 1;
    row.textContent = "";
    children.forEach(function (child, index) {
      var id = String(child.id);
      var on = state.childIds.indexOf(id) !== -1;
      var button = document.createElement("button");
      button.type = "button";
      button.className = "chip" + (on ? " on" : "") + (single ? " locked" : "");
      button.setAttribute("data-child-id", id);
      var av = document.createElement("span");
      av.className = "av c" + String(index % 3);
      av.appendChild(document.createTextNode((child.name || "").slice(0, 1)));
      var mark = document.createElement("span");
      mark.className = "ck";
      mark.textContent = "\u2713";
      av.appendChild(mark);
      var name = document.createElement("span");
      name.className = "nm";
      name.textContent = child.name || "";
      button.appendChild(av);
      button.appendChild(name);
      row.appendChild(button);
    });
    if (!hint) return;
    if (single) {
      hint.textContent = "";
      return;
    }
    if (state.lastHint) {
      hint.textContent = state.lastHint;
      return;
    }
    if (!state.childIds.length) {
      hint.textContent = videoCopy("sib_hint_pick");
      return;
    }
    var named = findChild(state.childIds[0]);
    var pre = videoApp().getAttribute("data-pre-name") || (named ? named.name : "");
    hint.textContent = videoFill("sib_hint_pre", { name: pre });
  }
  function syncPrimary() {
    state.childId = state.childIds.length ? state.childIds[0] : "";
  }
  function toggleChild(raw) {
    if (children.length <= 1) return;
    var id = String(raw);
    var index = state.childIds.indexOf(id);
    state.lastHint = "";
    if (index === -1) state.childIds.push(id);
    else if (state.childIds.length <= 1) state.lastHint = videoCopy("sib_hint_last");
    else state.childIds.splice(index, 1);
    syncPrimary();
    showError("");
    paint();
  }
  function paint() {
    paintHead();
    paintMeter();
    paintFilm();
    paintSib();
    paintButton();
  }
  function extOf(file) {
    var name = (file && file.name) || "";
    var dot = name.lastIndexOf(".");
    return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
  }
  function acceptable(file) {
    var ext = extOf(file);
    if (ext !== "mp4" && ext !== "mov") return false;
    var type = (file.type || "").toLowerCase();
    if (type.indexOf("webm") !== -1 || type.indexOf("matroska") !== -1) return false;
    return true;
  }
  function capturePoster(file, done) {
    var url = URL.createObjectURL(file);
    var video = document.createElement("video");
    video.preload = "auto";
    video.muted = true;
    video.playsInline = true;
    var settled = false;
    function finish(blob) {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      done(blob || null);
    }
    video.onloadeddata = function () {
      try {
        var canvas = document.createElement("canvas");
        canvas.width = video.videoWidth || 320;
        canvas.height = video.videoHeight || 180;
        var ctx = canvas.getContext("2d");
        if (!ctx) {
          finish(null);
          return;
        }
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) { finish(blob); }, "image/jpeg", 0.82);
      } catch (err) {
        finish(null);
      }
    };
    video.onerror = function () { finish(null); };
    video.src = url;
  }
  function takeFile(file) {
    state.file = null;
    state.durationMs = 0;
    state.posterBlob = null;
    state.posterFailed = false;
    state.overDur = false;
    state.overSize = false;
    state.badFormat = false;
    showError("");
    if (!file) {
      paint();
      return;
    }
    if (!acceptable(file)) {
      state.badFormat = true;
      state.file = file;
      showError(videoCopy("err_format"));
      paint();
      return;
    }
    if (file.size > maxBytes()) {
      state.overSize = true;
      state.file = file;
      state.durationMs = 0;
      showError(videoCopy("err_limit_size"));
      paint();
      return;
    }
    var url = URL.createObjectURL(file);
    var probe = document.createElement("video");
    probe.preload = "metadata";
    probe.onloadedmetadata = function () {
      var ms = Math.round((probe.duration || 0) * 1000);
      URL.revokeObjectURL(url);
      state.file = file;
      state.durationMs = ms;
      if (!isFinite(probe.duration) || ms <= 0) {
        state.badFormat = true;
        showError(videoCopy("err_format"));
        paint();
        return;
      }
      if (ms > maxMs()) {
        state.overDur = true;
        showError(videoCopy("err_limit_dur"));
        paint();
        return;
      }
      capturePoster(file, function (blob) {
        var cap = parseInt(videoApp().getAttribute("data-poster-max") || "0", 10);
        if (!blob || (cap && blob.size > cap)) {
          state.posterFailed = true;
          state.posterBlob = null;
        } else {
          state.posterBlob = blob;
          state.posterFailed = false;
        }
        paint();
      });
    };
    probe.onerror = function () {
      URL.revokeObjectURL(url);
      state.file = file;
      state.badFormat = true;
      showError(videoCopy("err_format"));
      paint();
    };
    probe.src = url;
  }
  function mapUpload(status, data) {
    if (status === 422 || !status || status >= 500) return videoCopy("err_retry");
    if (status === 404 && !(data && data.code)) return videoCopy("err_retry");
    var code = data && data.code;
    if (code === "video_key_spent") {
      state.idemKey = videoNewIdem();
      return videoCopy("err_retry");
    }
    if (code === "date_before_birth" && data.detail) return data.detail;
    var table = {
      idempotency_key_invalid: "err_retry",
      video_empty: "err_format",
      video_size: "err_limit_size",
      video_format: "err_format",
      video_duration_invalid: "err_limit_dur",
      video_duration: "err_limit_dur",
      child_required: "err_no_child",
      child_not_found: "e_kid_gone",
      children_too_many: "e_kid_gone",
      video_file_immutable: "err_format",
      date_invalid: "err_date_future",
      date_future: "err_date_future",
      poster_invalid: "err_format",
      video_forbidden_role: "err_demoted",
      video_not_found: "err_retry",
    };
    if (code && table[code]) {
      if (code === "err_date_birth") return videoFill("err_date_birth", { name: (findChild(state.childId) || {}).name || "" });
      return videoCopy(table[code]);
    }
    if (status === 403 && !(data && data.code)) return "";
    return videoCopy("err_retry");
  }
  function afterForbidden() {
    fetch("/api/me", { credentials: "same-origin" }).then(function (res) {
      if (res.status === 401) {
        window.location.href = "/";
        return;
      }
      showError(videoCopy("e_removed"));
    }, function () {
      showError(videoCopy("e_removed"));
    });
  }
  function abortUpload() {
    if (!state.sending) return;
    state.aborted = true;
    state.sending = false;
    if (state.xhr) {
      try { state.xhr.abort(); } catch (err) { /* already finished */ }
    }
    var bar = document.getElementById("film-prog-bar");
    if (bar) bar.style.width = "0";
    showError(videoCopy("err_retry"));
    paint();
  }
  function finishSend(xhr) {
    if (state.aborted) return;
    state.sending = false;
    var data = null;
    try { data = JSON.parse(xhr.responseText || ""); } catch (err) { data = null; }
    if (xhr.status === 200 || xhr.status === 201) {
      var when = data && data.date_label ? data.date_label : "";
      var child = data && data.child ? data.child.id : state.childId;
      var id = data && data.id ? data.id : "";
      var editing = videoApp().getAttribute("data-mode") === "edit";
      if (editing) {
        window.location.href = "/videos/" + id + "?vtoast=edited";
        return;
      }
      window.location.href = "/?child=" + child + "&vtoast=posted&vdate=" + encodeURIComponent(when) + "&vhl=" + id;
      return;
    }
    if (xhr.status === 403 && !(data && data.code)) {
      paint();
      afterForbidden();
      return;
    }
    showError(mapUpload(xhr.status, data));
    paint();
  }
  function submitEdit() {
    var xhr = new XMLHttpRequest();
    state.xhr = xhr;
    var id = videoApp().getAttribute("data-video-id") || "";
    xhr.open("PATCH", "/api/videos/" + id);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.onload = function () { finishSend(xhr); };
    xhr.onerror = function () {
      if (state.aborted) return;
      state.sending = false;
      showError(videoCopy("err_retry"));
      paint();
    };
    xhr.onabort = function () {
      state.sending = false;
      if (!state.aborted) showError(videoCopy("err_retry"));
      paint();
    };
    xhr.send(JSON.stringify({
      child_ids: state.childIds.map(function (item) { return parseInt(item, 10); }),
      on_date: state.date,
    }));
  }
  function submit() {
    if (state.sending) return;
    var editing = videoApp().getAttribute("data-mode") === "edit";
    var dateErr = clientDateError();
    if (!selectedChildren().length) {
      showError(videoCopy("err_no_child"));
      return;
    }
    if (dateErr) {
      showError(dateErr);
      return;
    }
    if (!editing && (!state.file || state.overDur || state.overSize || state.badFormat)) return;
    showError("");
    state.sending = true;
    state.aborted = false;
    paint();
    if (editing) {
      submitEdit();
      return;
    }
    var body = new FormData();
    state.childIds.forEach(function (id) { body.append("child_ids", String(id)); });
    body.append("child_id", String(state.childIds[0]));
    body.append("on_date", state.date);
    body.append("duration_ms", String(state.durationMs));
    var ext = extOf(state.file) || "mp4";
    body.append("file", state.file, "clip." + ext);
    if (state.posterBlob && !state.posterFailed) body.append("poster", state.posterBlob, "poster.jpg");
    var xhr = new XMLHttpRequest();
    state.xhr = xhr;
    xhr.open("POST", "/api/videos");
    xhr.setRequestHeader("Idempotency-Key", state.idemKey);
    xhr.upload.onprogress = function (event) {
      if (!event.lengthComputable) return;
      var bar = document.getElementById("film-prog-bar");
      if (bar) bar.style.width = Math.round((event.loaded / event.total) * 100) + "%";
    };
    xhr.onload = function () { finishSend(xhr); };
    xhr.onerror = function () {
      if (state.aborted) return;
      state.sending = false;
      showError(videoCopy("err_retry"));
      paint();
    };
    xhr.onabort = function () {
      state.sending = false;
      if (!state.aborted) showError(videoCopy("err_retry"));
      paint();
    };
    xhr.send(body);
  }
  function fillYears() {
    var year = document.getElementById("date-year");
    var month = document.getElementById("date-month");
    var day = document.getElementById("date-day");
    if (!year || !month || !day) return;
    var today = videoParseDay(videoApp().getAttribute("data-today") || state.date);
    var current = videoParseDay(state.date) || today;
    year.innerHTML = "";
    var start = today ? today.y - 18 : 2008;
    var end = today ? today.y : 2026;
    for (var y = end; y >= start; y -= 1) {
      var opt = document.createElement("option");
      opt.value = String(y);
      opt.textContent = String(y);
      year.appendChild(opt);
    }
    month.innerHTML = "";
    for (var m = 1; m <= 12; m += 1) {
      var mopt = document.createElement("option");
      mopt.value = String(m);
      mopt.textContent = String(m);
      month.appendChild(mopt);
    }
    if (current) {
      year.value = String(current.y);
      month.value = String(current.m);
    }
    fillDays();
    if (current) day.value = String(current.d);
  }
  function fillDays() {
    var year = document.getElementById("date-year");
    var month = document.getElementById("date-month");
    var day = document.getElementById("date-day");
    if (!year || !month || !day) return;
    var y = parseInt(year.value, 10);
    var m = parseInt(month.value, 10);
    var count = new Date(Date.UTC(y, m, 0)).getUTCDate();
    var prev = day.value;
    day.innerHTML = "";
    for (var d = 1; d <= count; d += 1) {
      var opt = document.createElement("option");
      opt.value = String(d);
      opt.textContent = String(d);
      day.appendChild(opt);
    }
    if (prev) day.value = prev;
  }
  function renderChildren() {
    var box = document.getElementById("child-options");
    if (!box) return;
    box.innerHTML = "";
    children.forEach(function (child) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "opt";
      button.textContent = child.name;
      button.addEventListener("click", function () {
        state.childId = String(child.id);
        hideSheet("child-sheet");
        showError("");
        paint();
      });
      box.appendChild(button);
    });
  }
  var fileInput = document.getElementById("video-file");
  var film = document.getElementById("film-preview");
  if (film && fileInput) {
    film.addEventListener("click", function () {
      if (state.sending) return;
      if (videoApp().getAttribute("data-mode") === "edit") return;
      fileInput.click();
    });
  }
  if (fileInput) {
    fileInput.addEventListener("change", function () {
      var file = fileInput.files && fileInput.files[0];
      if (!file) {
        takeFile(null);
        return;
      }
      takeFile(file);
    });
  }
  var submitBtn = document.getElementById("video-submit");
  if (submitBtn) submitBtn.addEventListener("click", submit);
  var cancelBtn = document.getElementById("video-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", abortUpload);
  document.querySelectorAll("[data-video-close]").forEach(function (node) {
    node.addEventListener("click", function (event) {
      if (state.sending) {
        event.preventDefault();
        abortUpload();
      }
    });
  });
  var errorBtn = document.getElementById("video-error");
  if (errorBtn) errorBtn.addEventListener("click", function () {
    var editing = videoApp().getAttribute("data-mode") === "edit";
    if (!state.sending && canSubmit() && (state.file || editing)) submit();
  });
  var childSwitch = document.getElementById("child-switch");
  if (childSwitch) childSwitch.addEventListener("click", function () { showSheet("child-sheet"); });
  var dateOpen = document.getElementById("date-open");
  if (dateOpen) dateOpen.addEventListener("click", function () {
    fillYears();
    var err = document.getElementById("date-sheet-err");
    if (err) err.hidden = true;
    showSheet("date-sheet");
  });
  var dateYear = document.getElementById("date-year");
  var dateMonth = document.getElementById("date-month");
  if (dateYear) dateYear.addEventListener("change", fillDays);
  if (dateMonth) dateMonth.addEventListener("change", fillDays);
  var dateOk = document.getElementById("date-ok");
  if (dateOk) dateOk.addEventListener("click", function () {
    var y = document.getElementById("date-year").value;
    var m = document.getElementById("date-month").value;
    var d = document.getElementById("date-day").value;
    var iso = y + "-" + (m.length < 2 ? "0" + m : m) + "-" + (d.length < 2 ? "0" + d : d);
    var child = latestChild();
    var err = document.getElementById("date-sheet-err");
    if (iso > (videoApp().getAttribute("data-today") || "")) {
      if (err) {
        err.hidden = false;
        err.textContent = videoCopy("err_date_future");
      }
      return;
    }
    if (child && iso < child.birth) {
      if (err) {
        err.hidden = false;
        err.textContent = videoFill("err_date_birth", { name: child.name });
      }
      return;
    }
    state.date = iso;
    hideSheet("date-sheet");
    showError("");
    paint();
  });
  document.querySelectorAll("[data-sheet-close]").forEach(function (node) {
    node.addEventListener("click", function () {
      hideSheet("child-sheet");
      hideSheet("date-sheet");
    });
  });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") abortUpload();
  });
  window.addEventListener("pagehide", function () { abortUpload(); });
  window.addEventListener("beforeunload", function () { abortUpload(); });
  var sibRow = document.getElementById("sib-row");
  if (sibRow) {
    sibRow.addEventListener("click", function (event) {
      if (state.sending) return;
      var node = event.target;
      while (node && node !== sibRow) {
        if (node.getAttribute && node.getAttribute("data-child-id")) {
          toggleChild(node.getAttribute("data-child-id"));
          return;
        }
        node = node.parentNode;
      }
    });
  }
  renderChildren();
  paint();
}

function bootDetail() {
  var deleting = false;
  function copy(key) { return videoCopy(key); }
  function showSheet() {
    var node = document.getElementById("delete-sheet");
    if (node) node.hidden = false;
  }
  function hideSheet() {
    var node = document.getElementById("delete-sheet");
    if (node) node.hidden = true;
  }
  var more = document.querySelector("[data-video-more]");
  var menu = document.getElementById("video-menu");
  if (more && menu) {
    more.addEventListener("click", function () { menu.hidden = !menu.hidden; });
  }
  var open = document.querySelector("[data-video-delete]");
  if (open) open.addEventListener("click", function () {
    if (menu) menu.hidden = true;
    var err = document.getElementById("delete-error");
    if (err) err.hidden = true;
    showSheet();
  });
  document.querySelectorAll("[data-delete-keep]").forEach(function (node) {
    node.addEventListener("click", hideSheet);
  });
  var ok = document.querySelector("[data-delete-ok]");
  if (ok) ok.addEventListener("click", function () {
    if (deleting) return;
    deleting = true;
    ok.disabled = true;
    ok.textContent = copy("del_ing");
    var err = document.getElementById("delete-error");
    if (err) err.hidden = true;
    var id = videoApp().getAttribute("data-video-id");
    var child = videoApp().getAttribute("data-child-id");
    fetch("/api/videos/" + id, { method: "DELETE", credentials: "same-origin" }).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (ignore) { data = null; }
        }
        if (res.status === 204 || (res.status === 404 && data && data.code === "video_not_found")) {
          window.location.href = "/?child=" + child + "&vtoast=deleted";
          return;
        }
        deleting = false;
        ok.disabled = false;
        ok.textContent = copy("del_ok");
        if (err) {
          err.hidden = false;
          if (res.status === 404 && !(data && data.code)) err.textContent = copy("err_retry");
          else if (!res.status || res.status >= 500 || res.status === 422) err.textContent = copy("del_fail");
          else err.textContent = copy("del_fail");
        }
      });
    }, function () {
      deleting = false;
      ok.disabled = false;
      ok.textContent = copy("del_ok");
      if (err) {
        err.hidden = false;
        err.textContent = copy("del_fail");
      }
    });
  });
}

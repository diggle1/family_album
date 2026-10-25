/* Timeline slip. User-facing sentences stay in data-copy-* attributes. */

var WN_WEEK = "日一二三四五六";

function decideSlip(status, data, ctx) {
  ctx = ctx || {};
  if (ctx.left || ctx.settledEmpty || (ctx.generation !== undefined && ctx.generation !== ctx.currentGeneration)) {
    return { render: false, ignore: true, postSeen: false, kind: "late" };
  }
  if (status === 0 || status === null || typeof status === "undefined") {
    return { render: false, ignore: false, postSeen: false, retry: true, kind: "network" };
  }
  if (status >= 500) return { render: false, ignore: false, postSeen: false, retry: true, kind: "busy" };
  if (status === 422) return { render: false, ignore: false, postSeen: false, retry: true, kind: "validation" };
  if (status === 404 && !(data && data.code)) {
    return { render: false, ignore: false, postSeen: false, retry: true, kind: "rolling" };
  }
  if (status === 403 && !(data && data.code)) {
    return { render: false, ignore: false, postSeen: false, kind: "forbidden" };
  }
  if (status === 403) return { render: false, ignore: false, postSeen: false, kind: "forbidden" };
  if (status === 200 && data && data.has_updates === true && data.days && data.days.length) {
    return { render: true, ignore: false, postSeen: true, kind: "slip" };
  }
  if (status === 200 && data && data.has_updates === false) {
    return { render: false, ignore: false, postSeen: true, kind: "empty" };
  }
  return { render: false, ignore: false, postSeen: false, retry: true, kind: "partial" };
}

function seenOutcome(status, data) {
  if (status === 422) return { ok: false, retry: true, crash: false, kind: "validation" };
  if (status === 0 || status === null || typeof status === "undefined") {
    return { ok: false, retry: true, crash: false, kind: "network" };
  }
  if (status >= 500) return { ok: false, retry: true, crash: false, kind: "busy" };
  if (status === 404 && !(data && data.code)) {
    return { ok: false, retry: true, crash: false, kind: "rolling" };
  }
  if (status === 404) return { ok: false, retry: false, crash: false, kind: "missing" };
  if (status === 403) return { ok: false, retry: false, crash: false, kind: "forbidden" };
  if (status === 200 && data && data.last_seen_at) return { ok: true, retry: false, crash: false, kind: "ok" };
  return { ok: false, retry: true, crash: false, kind: "retry" };
}

function wnFill(template, map) {
  return String(template || "").replace(/\{(\w+)\}/g, function (_match, key) {
    return map && map[key] != null ? String(map[key]) : "";
  });
}

function wnWeekday(iso) {
  var parts = String(iso || "").split("-");
  if (parts.length !== 3) return "";
  var utc = Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  var index = new Date(utc).getUTCDay();
  if (!(index >= 0 && index < WN_WEEK.length)) return "";
  return WN_WEEK.charAt(index);
}

function wnCopy(root, name) {
  return (root && root.getAttribute("data-copy-" + name)) || "";
}

function renderSlip(root, data) {
  if (!root || !data || !data.days) return;
  var title = wnCopy(root, "slip-title");
  var corner = data.window === "fallback_7d"
    ? wnCopy(root, "slip-sub-7d")
    : wnFill(wnCopy(root, "slip-sub-days"), { n: data.days.length });
  var fallback = root.getAttribute("data-fallback-child") || "";
  var prefer = root.getAttribute("data-prefer-child") || "";
  var list = data.days.map(function (day) {
    var parts = String(day.date || "").split("-");
    var month = String(Number(parts[1]));
    var dateNum = String(Number(parts[2]));
    var weekday = wnWeekday(day.date);
    var chips = "";
    if (day.photo_count > 0) chips += '<span class="chip ph">' + wnCopy(root, "chip-photo") + "</span>";
    if (day.journal_count > 0) chips += '<span class="chip jn">' + wnCopy(root, "chip-journal") + "</span>";
    if (day.video_count > 0) chips += '<span class="chip vd">' + wnCopy(root, "chip-video") + "</span>";
    var child = prefer || fallback;
    var href = child ? "/children/" + child + "/days/" + day.date : "";
    return (
      '<a class="item" href="' + href + '">' +
      '<span class="cal"><span class="d">' + dateNum + "</span><span class=\"m\">" +
      wnFill(wnCopy(root, "row-cal-m"), { mo: month, w: weekday }) +
      "</span></span>" +
      '<span class="body"><span class="dt">' +
      wnFill(wnCopy(root, "row-date-w"), { mo: month, d: dateNum, w: weekday }) +
      '</span><span class="chips">' + chips + '</span><span class="tot">' +
      wnFill(wnCopy(root, "row-total"), { n: day.total }) +
      "</span></span>" +
      '<span class="go">›</span></a>'
    );
  }).join("");
  root.innerHTML =
    '<details class="wn-slip"><summary class="slipbar"><span class="rail"></span>' +
    '<span class="row1"><span class="ttl">' + title + '</span><span class="corner">' + corner +
    '<span class="chev"> ▾</span></span></span></summary><div class="sliplist">' + list +
    "</div></details>";
}

function clearSlip(root) {
  if (!root) return;
  root.innerHTML = "";
}

var wnLeft = false;
var wnGeneration = 0;
var wnXhr = null;

function postSeen(root) {
  if (!root || root.getAttribute("data-seen-posted") === "1") return;
  var url = root.getAttribute("data-seen-url");
  if (!url) return;
  root.setAttribute("data-seen-posted", "1");
  var xhr = new XMLHttpRequest();
  xhr.open("POST", url);
  xhr.setRequestHeader("Content-Type", "application/json");
  xhr.onload = function () {
    seenOutcome(xhr.status, null);
  };
  xhr.onerror = function () {
    seenOutcome(0, null);
  };
  xhr.send("{}");
}

function fetchSlip(root) {
  var url = root.getAttribute("data-read-url");
  if (!url) return;
  wnGeneration += 1;
  var generation = wnGeneration;
  if (wnXhr) wnXhr.abort();
  var xhr = new XMLHttpRequest();
  wnXhr = xhr;
  xhr.open("GET", url);
  xhr.onload = function () {
    if (xhr !== wnXhr) return;
    var data = null;
    try { data = JSON.parse(xhr.responseText || ""); } catch (err) { data = null; }
    var decision = decideSlip(xhr.status, data, {
      left: wnLeft,
      generation: generation,
      currentGeneration: wnGeneration,
      settledEmpty: root.getAttribute("data-settled") === "empty",
    });
    if (decision.ignore) return;
    if (decision.render) renderSlip(root, data);
    else clearSlip(root);
    if (!decision.render && xhr.status === 200) root.setAttribute("data-settled", "empty");
    if (decision.postSeen) postSeen(root);
  };
  xhr.onerror = function () {
    var decision = decideSlip(0, null, { left: wnLeft, generation: generation, currentGeneration: wnGeneration });
    if (!decision.ignore && !decision.render) clearSlip(root);
  };
  xhr.send();
}

function bootWhatsNew(root) {
  if (!root || root.getAttribute("data-booted") === "1") return;
  root.setAttribute("data-booted", "1");
  if (root.getAttribute("data-ssr") === "1") {
    postSeen(root);
    return;
  }
  fetchSlip(root);
}

function bootWhatsNewNow() {
  bootWhatsNew(document.getElementById("whats-new-root"));
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", bootWhatsNewNow);
  document.addEventListener("htmx:afterSwap", bootWhatsNewNow);
  document.addEventListener("pagehide", function () {
    wnLeft = true;
    var xhr = wnXhr;
    if (xhr) xhr.abort();
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    decideSlip: decideSlip,
    seenOutcome: seenOutcome,
    wnFill: wnFill,
    renderSlip: renderSlip,
    clearSlip: clearSlip,
  };
}

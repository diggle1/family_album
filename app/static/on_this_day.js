/* Year card. User-facing sentences stay in data-copy-* attributes. */

function otdFill(template, map) {
  return String(template || "").replace(/\{(\w+)\}/g, function (_match, key) {
    return map && map[key] != null ? String(map[key]) : "";
  });
}

function otdEsc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch];
  });
}

function decideOnThisDay(status, data, ctx) {
  ctx = ctx || {};
  if (ctx.left || ctx.settledEmpty || (ctx.generation !== undefined && ctx.generation !== ctx.currentGeneration)) {
    return { render: false, ignore: true, crash: false, retry: false, kind: "late" };
  }
  if (status === 0 || status === null || typeof status === "undefined") {
    return { render: false, ignore: false, crash: false, retry: true, kind: "network" };
  }
  if (status >= 500) {
    return { render: false, ignore: false, crash: false, retry: true, kind: "busy" };
  }
  if (status === 422) {
    return { render: false, ignore: false, crash: false, retry: true, kind: "validation" };
  }
  if (status === 404 && !(data && data.code)) {
    return { render: false, ignore: false, crash: false, retry: true, kind: "rolling" };
  }
  if (status === 403) {
    return { render: false, ignore: false, crash: false, retry: false, kind: "forbidden" };
  }
  if (status === 200 && data && data.has_on_this_day === true && data.total > 0 && data.day_path) {
    return { render: true, ignore: false, crash: false, retry: false, kind: "card" };
  }
  if (status === 200 && data && data.has_on_this_day === false) {
    return { render: false, ignore: false, crash: false, retry: false, kind: "empty" };
  }
  return { render: false, ignore: false, crash: false, retry: true, kind: "partial" };
}

function pairDecision(slip, amber) {
  return {
    slip: !!(slip && slip.render),
    amber: !!(amber && amber.render),
    merged: false,
  };
}

function otdCopy(root, name) {
  return (root && root.getAttribute("data-copy-" + name)) || "";
}

function cardHtml(copy, data) {
  var decision = decideOnThisDay(200, data, { left: false, generation: 1, currentGeneration: 1 });
  if (!decision.render) return "";
  copy = copy || {};
  var parts = String(data.target_date || "").split("-");
  var month = String(Number(parts[1]));
  var day = String(Number(parts[2]));
  var year = data.year;
  var chips = "";
  if (data.photo_count > 0) {
    chips += '<span class="chip ph">' + otdEsc(otdFill(copy.chip_count, { label: copy.chip_photo, n: data.photo_count })) + "</span>";
  }
  if (data.journal_count > 0) {
    chips += '<span class="chip jn">' + otdEsc(otdFill(copy.chip_count, { label: copy.chip_journal, n: data.journal_count })) + "</span>";
  }
  if (data.video_count > 0) {
    chips += '<span class="chip vd">' + otdEsc(otdFill(copy.chip_count, { label: copy.chip_video, n: data.video_count })) + "</span>";
  }
  var thumbs = data.thumb_urls || [];
  var thumbHtml = "";
  if (thumbs.length) {
    var imgs = thumbs.slice(0, 4).map(function (url) {
      return '<img src="' + otdEsc(url) + '" alt="">';
    }).join("");
    thumbHtml = '<span class="amber-thumbs count-' + thumbs.slice(0, 4).length + '">' + imgs + "</span>";
  }
  return (
    '<a class="amber-card" href="' + otdEsc(data.day_path) + '">' +
    '<span class="amber-stamp"><span class="seal"><span class="yy">' + otdEsc(year) +
    '</span><span class="dot"></span><span class="lab">' + otdEsc(copy.stamp_lab) +
    "</span></span></span>" +
    thumbHtml +
    '<span class="amber-body"><span class="ttl">' + otdEsc(copy.title) +
    '</span><span class="sub">' + otdEsc(otdFill(copy.sub, { mo: month, d: day, y: year })) +
    '</span><span class="chips">' + chips + "</span></span>" +
    '<span class="amber-go">›</span></a>'
  );
}

function renderOnThisDay(root, data) {
  if (!root) return;
  var html = cardHtml({
    title: otdCopy(root, "amber-title"),
    sub: otdCopy(root, "amber-sub"),
    stamp_lab: otdCopy(root, "amber-stamp-lab"),
    chip_photo: otdCopy(root, "chip-photo"),
    chip_journal: otdCopy(root, "chip-journal"),
    chip_video: otdCopy(root, "chip-video"),
    chip_count: otdCopy(root, "chip-count"),
  }, data);
  if (!html) {
    root.innerHTML = "";
    return;
  }
  root.innerHTML = html;
}

function clearOnThisDay(root) {
  if (!root) return;
  root.innerHTML = "";
}

var otdLeft = false;
var otdGeneration = 0;
var otdXhr = null;

function fetchOnThisDay(root) {
  var url = root.getAttribute("data-read-url");
  if (!url) return;
  otdGeneration += 1;
  var generation = otdGeneration;
  if (otdXhr) otdXhr.abort();
  var xhr = new XMLHttpRequest();
  otdXhr = xhr;
  xhr.open("GET", url);
  xhr.onload = function () {
    if (xhr !== otdXhr) return;
    var data = null;
    try { data = JSON.parse(xhr.responseText || ""); } catch (err) { data = null; }
    var decision = decideOnThisDay(xhr.status, data, {
      left: otdLeft,
      generation: generation,
      currentGeneration: otdGeneration,
      settledEmpty: root.getAttribute("data-settled") === "empty",
    });
    if (decision.ignore) return;
    if (decision.render) renderOnThisDay(root, data);
    else clearOnThisDay(root);
    if (!decision.render && xhr.status === 200) root.setAttribute("data-settled", "empty");
  };
  xhr.onerror = function () {
    var decision = decideOnThisDay(0, null, {
      left: otdLeft,
      generation: generation,
      currentGeneration: otdGeneration,
    });
    if (!decision.ignore && !decision.render) clearOnThisDay(root);
  };
  xhr.send();
}

function bootOnThisDay(root) {
  if (!root || root.getAttribute("data-booted") === "1") return;
  root.setAttribute("data-booted", "1");
  if (root.getAttribute("data-ssr") === "1") return;
  fetchOnThisDay(root);
}

function bootOnThisDayNow() {
  bootOnThisDay(document.getElementById("on-this-day-root"));
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", bootOnThisDayNow);
  document.addEventListener("htmx:afterSwap", bootOnThisDayNow);
  document.addEventListener("pagehide", function () {
    otdLeft = true;
    var xhr = otdXhr;
    if (xhr) xhr.abort();
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    decideOnThisDay: decideOnThisDay,
    pairDecision: pairDecision,
    otdFill: otdFill,
    cardHtml: cardHtml,
    renderOnThisDay: renderOnThisDay,
    clearOnThisDay: clearOnThisDay,
  };
}

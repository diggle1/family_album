/* Who-tag switches. User-facing sentences stay in data-copy-* attributes. */

function acceptLate(seen, current) {
  return Number(seen) === Number(current);
}

function decideFilter(status, data, ctx) {
  ctx = ctx || {};
  var late = ctx.left || (ctx.generation !== undefined && ctx.generation !== ctx.currentGeneration);
  if (late) {
    return { apply: false, ignore: true, pretend: false, retry: false, crash: false, kind: "late" };
  }
  if (status === 0 || status === null || typeof status === "undefined") {
    return { apply: false, ignore: false, pretend: false, retry: true, crash: false, kind: "offline" };
  }
  if (status >= 500) {
    return { apply: false, ignore: false, pretend: false, retry: true, crash: false, kind: "busy" };
  }
  if (status === 422) {
    return { apply: false, ignore: false, pretend: false, retry: false, crash: false, fallbackFamily: true, kind: "invalid" };
  }
  if (status === 404 && !(data && data.code)) {
    return { apply: false, ignore: false, pretend: false, retry: true, crash: false, kind: "rolling" };
  }
  if (status === 404) {
    return { apply: false, ignore: false, pretend: false, retry: false, crash: false, kind: "missing" };
  }
  if (status === 403) {
    return { apply: false, ignore: false, pretend: false, retry: false, crash: false, kind: "forbidden" };
  }
  if (status === 200) {
    return { apply: true, ignore: false, pretend: false, retry: false, crash: false, kind: "ok" };
  }
  return { apply: false, ignore: false, pretend: false, retry: true, crash: false, kind: "partial" };
}

var whoGeneration = 0;

function isWhoNav(elt) {
  if (!elt || !elt.closest) return false;
  return !!(elt.closest(".who-strip") || elt.hasAttribute("data-who-nav"));
}

function onWhoBeforeRequest(event) {
  var detail = event.detail || {};
  if (!isWhoNav(detail.elt)) return;
  whoGeneration += 1;
  if (detail.xhr) detail.xhr._whoGen = whoGeneration;
  if (detail.requestConfig && detail.requestConfig.headers) {
    detail.requestConfig.headers["X-Who-Gen"] = String(whoGeneration);
  }
}

function onWhoBeforeSwap(event) {
  var detail = event.detail || {};
  var xhr = detail.xhr;
  if (!xhr || xhr._whoGen == null) return;
  var decision = decideFilter(xhr.status, null, {
    generation: xhr._whoGen,
    currentGeneration: whoGeneration,
  });
  if (decision.ignore || !decision.apply) detail.shouldSwap = false;
}

if (typeof document !== "undefined") {
  document.addEventListener("htmx:beforeRequest", onWhoBeforeRequest);
  document.addEventListener("htmx:beforeSwap", onWhoBeforeSwap);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    acceptLate: acceptLate,
    decideFilter: decideFilter,
  };
}

/* Day-page stamp lightbox. Sentences come from data-copy-* only. */
(function (global) {
  function fill(template, map) {
    return String(template || "").replace(/\{(\w+)\}/g, function (_, name) {
      return map && Object.prototype.hasOwnProperty.call(map, name) ? String(map[name]) : "{" + name + "}";
    });
  }

  function stepIndex(index, total, delta) {
    var n = Number(total) || 0;
    if (n <= 0) return 0;
    var next = Number(index) + Number(delta);
    if (next < 0) return 0;
    if (next > n - 1) return n - 1;
    return next;
  }

  var api = { fill: fill, stepIndex: stepIndex };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.DayGrid = api;
  if (!global.document) return;

  function boot() {
    var root = global.document.getElementById("day-note-app");
    if (!root) return;
    var dataNode = global.document.getElementById("qige-photos");
    var photos = [];
    try {
      photos = JSON.parse(dataNode ? dataNode.textContent || "[]" : "[]");
    } catch (err) {
      photos = [];
    }
    var lb = global.document.getElementById("day-lb");
    if (!lb || !photos.length) return;
    var index = 0;
    var img = global.document.getElementById("lb-img");
    var count = global.document.getElementById("lb-count");
    var open = global.document.getElementById("lb-open");
    var hint = global.document.getElementById("lb-swipe");
    if (hint) hint.textContent = root.getAttribute("data-copy-swipe") || "";

    function paint() {
      var item = photos[index];
      if (!item) return;
      if (img) {
        img.src = item.url || "";
        img.alt = "";
      }
      if (count) {
        count.textContent = fill(root.getAttribute("data-copy-lb_count") || "", {
          i: String(index + 1),
          n: String(photos.length),
        });
      }
      if (open) open.setAttribute("href", "/photos/" + encodeURIComponent(item.id));
    }
    function show(at) {
      index = stepIndex(at, photos.length, 0);
      paint();
      lb.hidden = false;
    }
    function move(delta) {
      index = stepIndex(index, photos.length, delta);
      paint();
    }
    var grid = global.document.getElementById("qige-grid");
    if (grid) {
      grid.addEventListener("click", function (event) {
        var node = event.target;
        while (node && node !== grid) {
          if (node.getAttribute && node.getAttribute("data-photo-id")) {
            var id = String(node.getAttribute("data-photo-id"));
            var found = 0;
            for (var i = 0; i < photos.length; i += 1) {
              if (String(photos[i].id) === id) found = i;
            }
            show(found);
            return;
          }
          node = node.parentNode;
        }
      });
    }
    var prev = global.document.getElementById("lb-prev");
    var next = global.document.getElementById("lb-next");
    var close = global.document.getElementById("lb-close");
    if (prev) prev.addEventListener("click", function () { move(-1); });
    if (next) next.addEventListener("click", function () { move(1); });
    if (close) close.addEventListener("click", function () { lb.hidden = true; });
    var startX = 0;
    lb.addEventListener("touchstart", function (event) {
      if (!event.changedTouches || !event.changedTouches.length) return;
      startX = event.changedTouches[0].clientX;
    }, { passive: true });
    lb.addEventListener("touchend", function (event) {
      if (!event.changedTouches || !event.changedTouches.length) return;
      var dx = event.changedTouches[0].clientX - startX;
      if (dx <= -40) move(1);
      else if (dx >= 40) move(-1);
    });
    global.document.addEventListener("keydown", function (event) {
      if (lb.hidden) return;
      if (event.key === "ArrowRight") move(1);
      else if (event.key === "ArrowLeft") move(-1);
      else if (event.key === "Escape") lb.hidden = true;
    });
  }

  if (global.document.readyState === "loading") global.document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(typeof window !== "undefined" ? window : globalThis);

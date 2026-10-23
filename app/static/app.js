(function () {
  var toast = document.getElementById("toast");
  if (toast) {
    setTimeout(function () { toast.remove(); }, 2000);
  }
  document.body.addEventListener("htmx:responseError", function (event) {
    var xhr = event.detail && event.detail.xhr;
    if (xhr && xhr.status >= 500) {
      var next = location.pathname + location.search;
      window.location.href = "/error?next=" + encodeURIComponent(next);
    }
  });
})();

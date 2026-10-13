/* Keep startup feedback available even if the application script cannot run. */
(function () {
  var status = document.getElementById('startupStatus');
  var title = document.getElementById('startupTitle');
  var detail = document.getElementById('startupDetail');
  var ready = false;
  var timer;

  function failed(message) {
    if (ready) return;
    clearTimeout(timer);
    status.hidden = false;
    title.textContent = 'EventPilot could not finish loading';
    detail.textContent = message || 'Reload the page. If the problem continues, try a current browser.';
  }

  window.eventpilotBoot = {
    ready: function () {
      ready = true;
      clearTimeout(timer);
      status.hidden = true;
    },
    failed: failed
  };
  window.addEventListener('error', function () { failed(); }, true);
  window.addEventListener('unhandledrejection', function () { failed(); });
  timer = setTimeout(function () {
    failed('Loading is taking longer than expected. Check your connection and reload the page.');
  }, 10000);
}());

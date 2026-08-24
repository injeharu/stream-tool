/* 一覧・ランキング画面の自動更新(5秒ごと)。
   フォーム入力中・チュートリアル中・操作直後(確認ダイアログ等)は割り込まないよう一時停止する。 */
(function () {
  var INTERVAL_MS = 5000;
  var INTERACTION_GRACE_MS = 8000;
  var lastInteraction = 0;

  function isTourActive() {
    return sessionStorage.getItem("tourActive") === "1";
  }

  function isEditing() {
    var el = document.activeElement;
    if (!el) return false;
    var tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  }

  function recentlyInteracted() {
    return Date.now() - lastInteraction < INTERACTION_GRACE_MS;
  }

  function tick() {
    if (!isTourActive() && !isEditing() && !recentlyInteracted() && document.visibilityState === "visible") {
      window.location.reload();
      return;
    }
    setTimeout(tick, INTERVAL_MS);
  }

  document.addEventListener("mousedown", function () { lastInteraction = Date.now(); });
  document.addEventListener("keydown", function () { lastInteraction = Date.now(); });

  document.addEventListener("DOMContentLoaded", function () {
    setTimeout(tick, INTERVAL_MS);
  });
})();

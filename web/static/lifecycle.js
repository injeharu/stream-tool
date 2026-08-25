/* アプリの生存監視とワンクリック更新。
   - アプリが終了したら: window.close() を試み、閉じられないブラウザでは「終了しました」画面に切り替える
   - 更新中は: 進行状況を表示し、再起動後に自動でページを復帰させる */
(function () {
  var CHECK_MS = 3000;
  var misses = 0;
  var finished = false;

  function isUpdating() {
    return sessionStorage.getItem("tdUpdating") === "1";
  }

  function showFullscreen(message, sub) {
    document.body.innerHTML =
      '<div style="position:fixed;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;' +
      'background:var(--bg,#0f0e17);color:var(--text,#e8e6f0);font-family:sans-serif;gap:12px;">' +
      '<div style="font-size:1.6rem;">' + message + "</div>" +
      '<div style="color:#9a96b0;">' + sub + "</div></div>";
  }

  function tryRecover() {
    /* 更新による再起動を待ち、復帰したら「見ていたページのまま」開き直す */
    var timer = setInterval(function () {
      fetch("/api/alive", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function () {
          clearInterval(timer);
          sessionStorage.removeItem("tdUpdating");
          location.reload();
        })
        .catch(function () {});
    }, 2000);
  }

  function onDead() {
    if (finished) return;
    finished = true;

    if (isUpdating()) {
      showFullscreen("🔄 更新しています...", "インストールが終わると自動でこの画面に戻ります");
      tryRecover();
      return;
    }

    /* 通常の終了: タブを閉じる(スクリプトで開かれていないタブはブラウザが拒否するため、その場合は終了画面) */
    window.close();
    setTimeout(function () {
      showFullscreen("特典台帳は終了しました", "このタブは閉じて大丈夫です");
      /* 更新など、すぐに復活する場合に備えて監視を続ける。
         復活したら画面を自動で元に戻す(取り残されないように) */
      tryRecover();
    }, 300);
  }

  setInterval(function () {
    if (finished) return;
    fetch("/api/alive", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error();
        misses = 0;
        /* 更新フラグが残ったまま正常応答している=更新完了後の復帰済み */
        if (isUpdating()) sessionStorage.removeItem("tdUpdating");
      })
      .catch(function () {
        misses += 1;
        if (misses >= 2) onDead();
      });
  }, CHECK_MS);

  /* ---------- ワンクリック更新 ---------- */

  window.startOneClickUpdate = function (version) {
    if (!confirm("v" + version + " に更新しますか?\n更新中は画面が一時的に切断され、完了後に自動で再起動します。")) {
      return;
    }
    var badge = document.getElementById("update-badge");

    fetch("/update/start", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          alert(data.message || "更新を開始できませんでした");
          return;
        }
        sessionStorage.setItem("tdUpdating", "1");
        if (badge) badge.textContent = "⬇ 更新を開始しました...";

        var poll = setInterval(function () {
          fetch("/api/update/progress", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (p) {
              if (badge && p.message) badge.textContent = "⬇ " + p.message;
              if (p.phase === "error") {
                clearInterval(poll);
                sessionStorage.removeItem("tdUpdating");
                alert(p.message + "\nリリースページから手動で更新してください。");
                if (badge) badge.textContent = "🔄 更新があります";
              }
            })
            .catch(function () { clearInterval(poll); });  /* サーバー停止=インストール開始 */
        }, 1000);
      })
      .catch(function () {
        alert("更新を開始できませんでした");
      });
  };
})();

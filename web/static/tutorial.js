(function () {
  var STEPS = [
    {
      page: null,
      selector: null,
      title: "ようこそ",
      body:
        "このツールは、Twitchのチャットを見張って「サブスク月数の節目(6ヶ月・12ヶ月など)に届いた視聴者」を自動で見つけ、" +
        "特典の管理を手伝うツールです。実際の画面を見ながら、ひとつずつ操作してみましょう。",
      advance: "next",
    },
    {
      page: null,
      selector: '[data-tour="nav-settings"]',
      title: "① このタブを押してみましょう",
      body: "「⚙ 設定」をクリックして、設定画面を開いてください。",
      advance: "click",
    },
    {
      page: "/settings",
      selector: "#tour-channel-input",
      title: "チャンネル名を入れるだけ",
      body: "ここにあなたのTwitchのユーザー名を入力して保存すると、このツールが動き始めます。パスワードやAPIキーは不要です。",
      advance: "next",
    },
    {
      page: null,
      selector: '[data-tour="nav-index"]',
      title: "② このタブを押してみましょう",
      body: "「📦 特典待ち」をクリックしてください。",
      advance: "click",
    },
    {
      page: "/",
      selector: "#tour-pending-area",
      title: "特典待ちの操作",
      body:
        "視聴者が節目の月数に届くと、ここにカードが自動で増えます。" +
        "特典を渡したら「特典済みにする」、対応しないと決めたら「対応不要にする」を押してください。",
      advance: "next",
    },
    {
      page: null,
      selector: '[data-tour="nav-subscribers"]',
      title: "③ このタブを押してみましょう",
      body: "「👥 サブスク一覧」をクリックしてください。",
      advance: "click",
    },
    {
      page: "/subscribers",
      selector: "#tour-manual-form",
      title: "月数を手入力できます",
      body:
        "このツールを使い始める前のサブスク歴は自動では分かりません。ここから手入力で登録できます。" +
        "あとで本人がチャットで再サブスクすると、自動で正しい値に更新されます。",
      advance: "next",
    },
    {
      page: null,
      selector: '[data-tour="nav-ranking"]',
      title: "④ このタブを押してみましょう",
      body: "「🏆 ランキング」をクリックしてください。",
      advance: "click",
    },
    {
      page: "/ranking",
      selector: "#tour-ranking-tabs",
      title: "ランキングの見方",
      body: "コメント数・キーワード・ビッツ(応援ポイント)・ギフトの4種類を切り替えて見られます。キーワードは「⚙ 設定」から登録できます。",
      advance: "next",
    },
    {
      page: null,
      selector: null,
      title: "自動起動のすすめ",
      body:
        "このツールは起動していない間のサブスクやコメントを記録できません。つまり起動し忘れ=特典の取りこぼしに直結します。" +
        "「自動起動をONにする」を押しておくと、パソコンにサインインするだけで自動で立ち上がるので、消し忘れ・付け忘れの心配がなくなります(あとから設定画面で変更できます)。",
      advance: "next",
      autostartOffer: true,
    },
    {
      page: null,
      selector: null,
      title: "知っておいてほしいこと",
      body:
        "視聴者が「共有しない」を選んで再サブスクすると自動では取得できません。過去のデータも遡れません。" +
        "お疲れさまでした、これでチュートリアルは終わりです!",
      advance: "finish",
    },
  ];

  var STORAGE_ACTIVE = "tourActive";
  var STORAGE_STEP = "tourStep";
  var STORAGE_AUTOSHOWN = "tourAutoShownOnce";

  var highlightEl = null;
  var cardEl = null;
  var modalEl = null;
  var clickHandler = null;
  var clickTarget = null;
  var repositionHandler = null;

  function getActive() {
    return sessionStorage.getItem(STORAGE_ACTIVE) === "1";
  }
  function getStep() {
    return parseInt(sessionStorage.getItem(STORAGE_STEP) || "0", 10);
  }
  function setState(active, step) {
    sessionStorage.setItem(STORAGE_ACTIVE, active ? "1" : "0");
    sessionStorage.setItem(STORAGE_STEP, String(step));
  }

  function cleanupUI() {
    if (highlightEl) { highlightEl.remove(); highlightEl = null; }
    if (cardEl) { cardEl.remove(); cardEl = null; }
    if (modalEl) { modalEl.remove(); modalEl = null; }
    if (clickTarget && clickHandler) {
      clickTarget.removeEventListener("click", clickHandler);
    }
    clickHandler = null;
    clickTarget = null;
    if (repositionHandler) {
      window.removeEventListener("resize", repositionHandler);
      window.removeEventListener("scroll", repositionHandler, true);
      repositionHandler = null;
    }
  }

  function finishTour() {
    cleanupUI();
    setState(false, 0);
    fetch("/tutorial/seen", { method: "POST" }).catch(function () {});
  }

  function goToStep(i) {
    if (i < 0) i = 0;
    setState(true, i);
    if (i >= STEPS.length) {
      finishTour();
      return;
    }
    var step = STEPS[i];
    if (step.page && step.page !== window.location.pathname) {
      window.location.href = step.page;
      return;
    }
    cleanupUI();
    renderStep(i);
  }

  function buildCardButtons(container, i, step) {
    if (step.autostartOffer) {
      var offer = document.createElement("div");
      offer.style.cssText = "margin: 4px 0 10px;";
      var onBtn = document.createElement("button");
      onBtn.className = "btn btn-primary btn-sm";
      onBtn.textContent = "🖥 自動起動をONにする";
      onBtn.onclick = function () {
        fetch("/autostart/enable", { method: "POST" })
          .then(function () {
            onBtn.textContent = "✅ 自動起動をONにしました";
            onBtn.disabled = true;
          })
          .catch(function () {});
      };
      offer.appendChild(onBtn);
      container.appendChild(offer);
    }

    var actions = document.createElement("div");
    actions.className = "tutorial-actions";

    var skipBtn = document.createElement("button");
    skipBtn.className = "btn btn-ghost btn-sm";
    skipBtn.textContent = "スキップ";
    skipBtn.onclick = finishTour;
    actions.appendChild(skipBtn);

    var spacer = document.createElement("div");
    spacer.style.flex = "1";
    actions.appendChild(spacer);

    if (i > 0) {
      var prevBtn = document.createElement("button");
      prevBtn.className = "btn btn-ghost btn-sm";
      prevBtn.textContent = "← 前へ";
      prevBtn.onclick = function () { goToStep(i - 1); };
      actions.appendChild(prevBtn);
    }

    if (step.advance === "next" || step.advance === "finish") {
      var nextBtn = document.createElement("button");
      nextBtn.className = "btn btn-primary btn-sm";
      nextBtn.textContent = step.advance === "finish" ? "完了" : "次へ";
      nextBtn.onclick = function () { goToStep(i + 1); };
      actions.appendChild(nextBtn);
    } else {
      var hint = document.createElement("span");
      hint.style.cssText = "font-size:0.78rem; color:var(--text-dim); align-self:center;";
      hint.textContent = "強調された項目をクリックしてください →";
      actions.appendChild(hint);
    }

    container.appendChild(actions);
  }

  function renderModal(i, step) {
    modalEl = document.createElement("div");
    modalEl.className = "tutorial-overlay";
    modalEl.style.display = "flex";

    var card = document.createElement("div");
    card.className = "tutorial-card";

    var progress = document.createElement("div");
    progress.className = "tutorial-progress";
    progress.textContent = (i + 1) + " / " + STEPS.length;
    card.appendChild(progress);

    var title = document.createElement("h2");
    title.textContent = step.title;
    card.appendChild(title);

    var body = document.createElement("p");
    body.textContent = step.body;
    card.appendChild(body);

    buildCardButtons(card, i, step);
    modalEl.appendChild(card);
    document.body.appendChild(modalEl);
  }

  function positionHighlight(el) {
    var rect = el.getBoundingClientRect();
    var pad = 6;
    highlightEl.style.top = (rect.top - pad) + "px";
    highlightEl.style.left = (rect.left - pad) + "px";
    highlightEl.style.width = (rect.width + pad * 2) + "px";
    highlightEl.style.height = (rect.height + pad * 2) + "px";
  }

  function renderSpotlight(i, step, el) {
    highlightEl = document.createElement("div");
    highlightEl.className = "tour-highlight";
    document.body.appendChild(highlightEl);
    positionHighlight(el);

    repositionHandler = function () { positionHighlight(el); };
    window.addEventListener("resize", repositionHandler);
    window.addEventListener("scroll", repositionHandler, true);

    cardEl = document.createElement("div");
    cardEl.className = "tour-card";

    var progress = document.createElement("div");
    progress.className = "tutorial-progress";
    progress.textContent = (i + 1) + " / " + STEPS.length;
    cardEl.appendChild(progress);

    var title = document.createElement("h2");
    title.textContent = step.title;
    cardEl.appendChild(title);

    var body = document.createElement("p");
    body.textContent = step.body;
    cardEl.appendChild(body);

    buildCardButtons(cardEl, i, step);
    document.body.appendChild(cardEl);

    if (step.advance === "click") {
      clickTarget = el;
      clickHandler = function () {
        setState(true, i + 1);
      };
      el.addEventListener("click", clickHandler);
    }
  }

  function renderStep(i) {
    var step = STEPS[i];
    if (!step) { finishTour(); return; }

    if (step.selector) {
      var el = document.querySelector(step.selector);
      if (el) {
        renderSpotlight(i, step, el);
        return;
      }
    }
    renderModal(i, step);
  }

  window.tutorialOpen = function () {
    setState(true, 0);
    cleanupUI();
    renderStep(0);
  };

  document.addEventListener("DOMContentLoaded", function () {
    var autoshow = document.body.dataset.tutorialAutoshow === "1";
    var active = getActive();

    if (!active && autoshow && sessionStorage.getItem(STORAGE_AUTOSHOWN) !== "1") {
      sessionStorage.setItem(STORAGE_AUTOSHOWN, "1");
      setState(true, 0);
      active = true;
    }

    if (active) {
      renderStep(getStep());
    }
  });
})();

(function () {
  var STEPS = [
    {
      title: "ようこそ",
      body:
        "このツールは、Twitchのチャットを見張って「サブスク月数の節目(6ヶ月・12ヶ月など)に届いた視聴者」を自動で見つけ、" +
        "グッズ発送の管理を手伝うツールです。むずかしい登録は一切いりません。",
    },
    {
      title: "最初の設定はこれだけ",
      body:
        "「⚙ 設定」を開いて、チャンネル名(Twitchのユーザー名)を入れて保存するだけで動き始めます。" +
        "パスワードやAPIキーの登録は不要です。設定を変えたあとはツールの再起動をお忘れなく。",
    },
    {
      title: "発送待ちの使い方",
      body:
        "視聴者が節目の月数に届くと、自動で「📦 発送待ち」にカードが増えます。" +
        "グッズを送ったら「発送済みにする」、送らないと決めたら「対応不要にする」を押してください。",
    },
    {
      title: "月数を手入力できます",
      body:
        "このツールを使い始める前のサブスク歴は自動では分かりません。" +
        "「👥 サブスク一覧」から手入力で登録できます。あとで本人がチャットで再サブスクすると、自動で正しい値に更新されます。",
    },
    {
      title: "ランキングとキーワード",
      body:
        "「🏆 ランキング」でコメント数・キーワード・ビッツ(応援ポイント)の順位を見られます。" +
        "「⚙ 設定」の下の方から、好きなキーワード(例:かわいい)を登録できます。",
    },
    {
      title: "知っておいてほしいこと",
      body:
        "視聴者が「共有しない」を選んで再サブスクすると自動では取得できません。過去のデータも遡れません。" +
        "配信を始める前にこのツールを起動しておく運用がおすすめです。「⚙ 設定」の自動起動をONにすると、PC起動時に自動で立ち上がります。",
    },
  ];

  var currentStep = 0;

  function render() {
    var step = STEPS[currentStep];
    var titleEl = document.getElementById("tutorial-title");
    var bodyEl = document.getElementById("tutorial-body");
    var progressEl = document.getElementById("tutorial-progress");
    var prevBtn = document.getElementById("tutorial-prev");
    var nextBtn = document.getElementById("tutorial-next");

    titleEl.textContent = step.title;
    bodyEl.textContent = step.body;
    progressEl.textContent = (currentStep + 1) + " / " + STEPS.length;
    prevBtn.style.visibility = currentStep === 0 ? "hidden" : "visible";
    nextBtn.textContent = currentStep === STEPS.length - 1 ? "完了" : "次へ";
  }

  function closeTutorial() {
    var overlay = document.getElementById("tutorial-overlay");
    if (overlay) overlay.style.display = "none";
    fetch("/tutorial/seen", { method: "POST" }).catch(function () {});
  }

  window.tutorialNext = function () {
    if (currentStep >= STEPS.length - 1) {
      closeTutorial();
      return;
    }
    currentStep += 1;
    render();
  };

  window.tutorialPrev = function () {
    if (currentStep > 0) {
      currentStep -= 1;
      render();
    }
  };

  window.tutorialSkip = function () {
    closeTutorial();
  };

  window.tutorialOpen = function () {
    currentStep = 0;
    var overlay = document.getElementById("tutorial-overlay");
    if (overlay) overlay.style.display = "flex";
    render();
  };

  document.addEventListener("DOMContentLoaded", function () {
    var overlay = document.getElementById("tutorial-overlay");
    if (overlay && overlay.dataset.autoshow === "1") {
      render();
    }
  });
})();

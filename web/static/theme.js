(function () {
  var KEY = "stream-tool-theme";
  var saved = localStorage.getItem(KEY);
  if (saved === "light") {
    document.body.classList.add("light");
  }

  window.toggleTheme = function () {
    document.body.classList.toggle("light");
    localStorage.setItem(KEY, document.body.classList.contains("light") ? "light" : "dark");
  };
})();

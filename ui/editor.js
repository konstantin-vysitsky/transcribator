/* Окно правки промпта: текст, сохранение и тема.
 *
 * Python шлёт текст через `EX.apply(...)` и тему через `EX.theme(...)`,
 * страница шлёт нажатия. Закрытие окна — тоже сообщение: правило «пустой
 * промпт не сохраняется» живёт в Python, а не здесь.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var text = document.getElementById("text");
  var hint = document.getElementById("hint");
  var lastText = null;

  function send(name, value) {
    var api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.control !== "function") {
      return false;
    }
    try {
      api.control(name, value === undefined ? null : value);
      return true;
    } catch (error) {
      return false;
    }
  }

  function setHint(message, kind) {
    hint.textContent = message || "";
    hint.className = "hint" + (kind ? " " + kind : "");
  }

  window.EX = {
    apply: function (data) {
      if (!data || typeof data !== "object") {
        return;
      }
      if (typeof data.theme === "string") {
        window.EX.theme(data.theme);
      }
      if (typeof data.text === "string" && data.text !== lastText) {
        lastText = data.text;
        text.value = data.text;
        text.scrollTop = 0;
        text.focus();
        text.setSelectionRange(text.value.length, text.value.length);
      }
      if (typeof data.hint === "string") {
        setHint(data.hint, data.kind || "");
      }
    },
    theme: function (name) {
      root.dataset.theme = name === "light" ? "light" : "dark";
    },
    text: function () {
      return text.value;
    },
    hint: function () {
      return hint.textContent;
    }
  };

  document.getElementById("save").addEventListener("click", function () {
    send("save", text.value);
  });

  document.getElementById("restore").addEventListener("click", function () {
    send("restore");
  });

  document.getElementById("close").addEventListener("click", function () {
    send("close");
  });

  // Ctrl+S — привычное сохранение, чтобы не тянуться мышью.
  text.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      send("save", text.value);
    }
  });

  window.addEventListener("pywebviewready", function () {
    send("ready");
  }, { once: true });

  if (window.pywebview && window.pywebview.api) {
    send("ready");
  }
})();

/* Панель диктовки: страница и мост к Python.
 *
 * Python шлёт состояние одним вызовом `DSH.apply(state)`, страница шлёт
 * нажатия. Пока ничего не меняется, ни опроса, ни таймеров нет.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var $ = function (id) { return document.getElementById(id); };

  var ui = {
    dot: $("dot"),
    meter: $("meter"),
    meterFill: $("meterFill"),
    title: $("title"),
    done: $("done"),
    abort: $("abort"),
    mark1: $("mark1"),
    track1: $("track1"),
    track2: $("track2"),
    fill1: $("fill1"),
    fill2: $("fill2"),
    note1: $("note1"),
    note2: $("note2"),
    detail: $("detail"),
    swAuto: $("sw-auto"),
    swAi: $("sw-ai"),
    swAiText: $("sw-ai-text"),
    stubName: $("stub-name")
  };

  var STYLE_NAMES = {
    glow: "Тихое свечение",
    black: "Чёрное стекло",
    smoke: "Дымчатое с акцентом",
    frost: "Светлое стекло"
  };

  var TITLES = {
    recording: "Запись — говорите",
    transcribing: "Распознаю…",
    processing: "Обрабатываю через ИИ…"
  };

  var outbox = [];
  var readySent = false;
  var shownTitle = "";
  var lastState = null;

  /* --- связь с Python ---------------------------------------------- */

  function api() {
    return window.pywebview && window.pywebview.api;
  }

  function flush() {
    var door = api();
    if (!door || typeof door.control !== "function") {
      return;
    }
    var waiting = outbox;
    outbox = [];
    for (var i = 0; i < waiting.length; i += 1) {
      try {
        door.control(waiting[i].name, waiting[i].value);
      } catch (error) {
        return;
      }
    }
  }

  function send(name, value) {
    var message = { name: name, value: value === undefined ? null : value };
    var door = api();
    if (!door || typeof door.control !== "function") {
      outbox.push(message);
      return;
    }
    try {
      door.control(message.name, message.value);
    } catch (error) {
      /* Окно закрывается: молчим. */
    }
  }

  /* Нажатие заходит в дверь окна: без этого панель не схватилась бы мышью. */
  function knock() {
    var door = api();
    if (door && typeof door.press === "function") {
      try {
        door.press();
      } catch (error) {
        /* нет окна — не беда */
      }
    }
  }

  function announce() {
    if (readySent) {
      return;
    }
    readySent = true;
    send("ready");
    flush();
  }

  /* --- отрисовка ---------------------------------------------------- */

  function setText(node, value) {
    var next = value === undefined || value === null ? "" : String(value);
    if (node && node.textContent !== next) {
      node.textContent = next;
    }
  }

  function setWidth(node, value) {
    if (!node) {
      return;
    }
    var percent = Math.max(0, Math.min(100, Math.round((value || 0) * 100)));
    if (node.dataset.percent !== String(percent)) {
      node.dataset.percent = String(percent);
      node.style.width = percent + "%";
    }
  }

  function aiLabel(state) {
    if (!state.ai_ready) {
      return "ИИ не настроен";
    }
    if (state.ai_error) {
      return state.ai_enabled ? "ИИ" : "ИИ выкл";
    }
    return state.ai_enabled ? "Обработка ИИ" : "ИИ выкл";
  }

  function clock(seconds) {
    var whole = Math.max(0, Math.floor(seconds || 0));
    var minutes = Math.floor(whole / 60);
    var tail = String(whole % 60);
    return minutes + ":" + (tail.length < 2 ? "0" + tail : tail);
  }

  // Метка вида: страница может прийти до того, как все узлы готовы,
  // поэтому состояние ставит только то, что нашлось.
  function flag(node, name, on) {
    if (node) {
      node.classList.toggle(name, !!on);
    }
  }

  function applyState(state) {
    var mode = state.mode === "work" ? "work" : "recording";
    root.dataset.mode = mode;

    var style = state.style && STYLE_NAMES[state.style] ? state.style : "black";
    if (root.dataset.style !== style) {
      root.dataset.style = style;
      setText(ui.stubName, STYLE_NAMES[style]);
    }
    root.dataset.theme = state.theme === "light" ? "light" : "dark";

    var title = TITLES[state.activity] || "";
    if (state.activity === "recording" && state.duration) {
      title += " · " + clock(state.duration);
    }
    if (shownTitle !== title) {
      shownTitle = title;
      setText(ui.title, title);
    }

    var recording = mode === "recording";
    if (ui.done) {
      ui.done.hidden = !recording;
    }
    if (ui.dot) {
      ui.dot.style.display = recording ? "" : "none";
    }
    if (ui.meter) {
      ui.meter.style.display = recording ? "" : "none";
    }
    setWidth(ui.meterFill, state.level);

    flag(ui.swAuto, "on", state.auto_enter);
    flag(ui.swAi, "on", state.ai_enabled);
    flag(ui.swAi, "alert", state.ai_error && state.ai_ready);
    if (ui.swAi) {
      ui.swAi.title = state.ai_hint || "Обработка через ИИ";
    }
    setText(ui.swAiText, aiLabel(state));

    setWidth(ui.fill1, state.transcribe_progress);
    setWidth(ui.fill2, state.ai_progress);
    flag(ui.track1, "running", mode === "work" && state.activity === "transcribing");
    flag(ui.track2, "running", mode === "work" && state.activity === "processing" && state.ai_result === null);
    setText(ui.note1, state.transcribe_done ? "готово" : "");
    if (state.ai_result === true) {
      setText(ui.note2, "готово");
    } else if (state.ai_result === false) {
      setText(ui.note2, "без обработки");
    } else {
      setText(ui.note2, state.ai_note || "");
    }
    flag(ui.mark2, "on", state.ai_enabled || state.ai_result !== null);
    flag(ui.mark2, "off", !state.ai_enabled && state.ai_result === null);
    flag(ui.mark2, "good", state.ai_result === true);
    flag(ui.mark2, "bad", state.ai_result === false);
    setText(ui.detail, mode === "work" ? state.detail || "" : "");
  }

  window.DSH = {
    apply: function (state) {
      if (!state || typeof state !== "object") {
        return false;
      }
      lastState = state;
      try {
        applyState(state);
      } catch (error) {
        return "ошибка: " + (error && error.message ? error.message : error);
      }
      return true;
    },
    state: function () {
      return lastState;
    },
    colors: function (variables) {
      if (!variables || typeof variables !== "object") {
        return false;
      }
      for (var name in variables) {
        if (Object.prototype.hasOwnProperty.call(variables, name)) {
          root.style.setProperty(name, variables[name]);
        }
      }
      return true;
    },
    theme: function (name) {
      root.dataset.theme = name === "light" ? "light" : "dark";
    },
    ready: announce,
    show: function () {
      var bar = $("bar");
      return bar ? Math.ceil(bar.getBoundingClientRect().height) : 0;
    }
  };

  function onClick(node, action) {
    if (node) {
      node.addEventListener("click", function (event) {
        event.stopPropagation();
        action();
      });
    }
  }

  if (ui.done) { onClick(ui.done, function () { send("done"); }); }
  if (ui.abort) { onClick(ui.abort, function () { send("abort"); }); }
  if (ui.swAuto) {
    onClick(ui.swAuto, function () {
      send("auto_enter", !ui.swAuto.classList.contains("on"));
    });
  }
  if (ui.swAi) {
    onClick(ui.swAi, function () {
      send("ai", !ui.swAi.classList.contains("on"));
    });
  }

  document.addEventListener("mouseenter", knock, true);
  window.addEventListener("pywebviewready", announce, { once: true });
  // Запасной путь: событие могло пройти до подключения этого файла.
  if (window.pywebview && window.pywebview.api) {
    announce();
  }
})();

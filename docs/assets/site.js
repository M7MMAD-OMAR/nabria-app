(() => {
  const header = document.querySelector(".site-header");
  const menuButton = document.querySelector(".menu-toggle");
  const pageMain = document.querySelector("main");
  const pageFooter = document.querySelector(".site-footer");
  const mobileMenu = document.getElementById("mobile-menu");

  const closeMenu = () => {
    if (!header || !menuButton) return;
    header.dataset.menuOpen = "false";
    menuButton.setAttribute("aria-expanded", "false");
    menuButton.setAttribute("aria-label", menuButton.dataset.openLabel || "Open menu");
    document.body.classList.remove("menu-open");
    if (pageMain) pageMain.inert = false;
    if (pageFooter) pageFooter.inert = false;
  };

  if (header && menuButton) {
    menuButton.addEventListener("click", () => {
      const open = menuButton.getAttribute("aria-expanded") !== "true";
      header.dataset.menuOpen = String(open);
      menuButton.setAttribute("aria-expanded", String(open));
      menuButton.setAttribute(
        "aria-label",
        open ? menuButton.dataset.closeLabel || "Close menu" : menuButton.dataset.openLabel || "Open menu",
      );
      document.body.classList.toggle("menu-open", open);
      if (pageMain) pageMain.inert = open;
      if (pageFooter) pageFooter.inert = open;
      if (open) {
        window.setTimeout(() => mobileMenu?.querySelector("a")?.focus(), 180);
      }
    });

    header.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeMenu));
    window.addEventListener("resize", () => {
      if (window.innerWidth > 980) closeMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && menuButton.getAttribute("aria-expanded") === "true") {
        closeMenu();
        menuButton.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (document.body.classList.contains("menu-open") && event.target === document.body) {
        closeMenu();
        menuButton.focus();
      }
    });
  }

  /* The hero stage plays one take end to end: the microphone waits, hears a
     voice, the indicator arrives with the second keypress, and the sentence is
     typed only after that. The final sentence is what the markup contains, so a
     search engine and a reader with no JavaScript both get the real text; the
     animation reads it back out of the DOM before it starts. */
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  document.querySelectorAll("[data-dictation]").forEach((stage) => {
    const line = stage.querySelector(".typed-line");
    const body = stage.querySelector("[data-dictation-text]");
    const label = stage.querySelector("[data-dictation-label]");
    if (!line || !body || !label) return;

    const sentence = body.textContent.trim();
    const characters = [...sentence];

    const setPhase = (phase) => {
      stage.dataset.phase = phase;
      label.textContent = label.dataset[phase] || label.dataset.done || "";
    };

    if (reducedMotion.matches) {
      setPhase("done");
      return;
    }

    // Typed one character at a time it would be announced one character at a
    // time, so the paragraph says the whole sentence once and the animating
    // node says nothing.
    line.setAttribute("role", "img");
    line.setAttribute("aria-label", sentence);
    body.setAttribute("aria-hidden", "true");

    let running = false;
    let playing = false;
    const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

    const play = async () => {
      while (running) {
        setPhase("ready");
        body.textContent = "";
        await wait(1600);
        if (!running) break;

        setPhase("listening");
        await wait(4200);
        if (!running) break;

        setPhase("thinking");
        await wait(1700);
        if (!running) break;

        setPhase("typing");
        for (let index = 1; index <= characters.length; index += 1) {
          body.textContent = characters.slice(0, index).join("");
          await wait(characters[index - 1] === " " ? 105 : 58);
          if (!running) break;
        }
        if (!running) break;

        setPhase("done");
        await wait(5400);
      }

      playing = false;
      // The stage can come back into view while the last take is still winding
      // down; without this it would stop for good on the way back.
      if (running) {
        start();
        return;
      }
      setPhase("done");
      body.textContent = sentence;
    };

    const start = () => {
      running = true;
      if (playing) return;
      playing = true;
      play();
    };

    // Nothing animates while the reader is somewhere else on the page.
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) start();
            else running = false;
          });
        },
        { threshold: 0.35 },
      ).observe(stage);
    } else {
      start();
    }

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) running = false;
    });
  });

  document.querySelectorAll("[data-install-tabs]").forEach((group) => {
    const tabs = [...group.querySelectorAll('[role="tab"]')];
    const panels = tabs
      .map((tab) => document.getElementById(tab.getAttribute("aria-controls")))
      .filter(Boolean);

    const activate = (tab) => {
      tabs.forEach((item) => {
        const selected = item === tab;
        item.setAttribute("aria-selected", String(selected));
        item.tabIndex = selected ? 0 : -1;
      });
      panels.forEach((panel) => {
        panel.hidden = panel.id !== tab.getAttribute("aria-controls");
      });
    };

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activate(tab));
      tab.addEventListener("keydown", (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        let next = index;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        tabs[next].focus();
        activate(tabs[next]);
      });
    });
  });

  document.querySelectorAll("[data-copy-command]").forEach((button) => {
    button.addEventListener("click", async () => {
      const panel = button.closest(".command-panel");
      const target = button.dataset.copyTarget
        ? document.getElementById(button.dataset.copyTarget)
        : panel?.querySelector(".command-line");
      const command = target?.textContent?.trim();
      if (!command) return;

      try {
        await navigator.clipboard.writeText(command);
      } catch {
        const field = document.createElement("textarea");
        field.value = command;
        field.setAttribute("readonly", "");
        field.style.position = "fixed";
        field.style.opacity = "0";
        document.body.append(field);
        field.select();
        document.execCommand("copy");
        field.remove();
      }

      const label = button.querySelector("span");
      const original = label?.textContent;
      button.classList.add("is-copied");
      if (label) label.textContent = button.dataset.copied || "Copied";
      window.setTimeout(() => {
        button.classList.remove("is-copied");
        if (label && original) label.textContent = original;
      }, 1800);
    });
  });

  document.querySelectorAll("[data-quick-install]").forEach((quickInstall) => {
    const picker = quickInstall.querySelector("[data-distro-picker]");
    const trigger = picker?.querySelector(".distro-trigger");
    const menu = picker?.querySelector(".distro-menu");
    const label = picker?.querySelector("[data-distro-label]");
    const options = menu ? [...menu.querySelectorAll('[role="option"]')] : [];
    const command = quickInstall.querySelector("code");
    if (!picker || !trigger || !menu || !label || !options.length || !command) return;

    const close = () => {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    };

    const choose = (option) => {
      options.forEach((item) => item.setAttribute("aria-selected", String(item === option)));
      label.textContent = option.textContent.trim();
      command.textContent = option.dataset.command || "";
      command.title = command.textContent;
      close();
    };

    trigger.addEventListener("click", () => {
      const open = menu.hidden;
      menu.hidden = !open;
      trigger.setAttribute("aria-expanded", String(open));
      if (open) options.find((option) => option.getAttribute("aria-selected") === "true")?.focus();
    });

    options.forEach((option) => {
      option.addEventListener("click", () => choose(option));
      option.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose(option);
        } else if (event.key === "ArrowDown") {
          event.preventDefault();
          options[(options.indexOf(option) + 1) % options.length].focus();
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          options[(options.indexOf(option) - 1 + options.length) % options.length].focus();
        } else if (event.key === "Escape") {
          close();
          trigger.focus();
        }
      });
    });

    document.addEventListener("click", (event) => {
      if (!picker.contains(event.target)) close();
    });
  });
})();

(() => {
  const header = document.querySelector(".site-header");
  const menuButton = document.querySelector(".menu-toggle");

  const closeMenu = () => {
    if (!header || !menuButton) return;
    header.dataset.menuOpen = "false";
    menuButton.setAttribute("aria-expanded", "false");
    menuButton.setAttribute("aria-label", menuButton.dataset.openLabel || "Open menu");
    document.body.classList.remove("menu-open");
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
  }

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
      const command = panel?.querySelector(".command-line")?.textContent?.trim();
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
})();

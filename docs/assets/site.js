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

  document.querySelectorAll("[data-carousel]").forEach((carousel) => {
    const slides = [...carousel.querySelectorAll("[data-slide]")];
    const previous = carousel.querySelector("[data-carousel-prev]");
    const next = carousel.querySelector("[data-carousel-next]");
    const dots = carousel.querySelector("[data-carousel-dots]");
    const caption = carousel.querySelector("[data-carousel-caption]");
    const count = carousel.querySelector("[data-carousel-count]");
    const viewport = carousel.querySelector(".carousel-viewport");
    const locale = document.documentElement.lang || "en";
    const numbers = new Intl.NumberFormat(locale, {
      useGrouping: false,
      numberingSystem: locale.startsWith("ar") ? "arab" : "latn",
    });
    let active = 0;
    let pointerStart = null;

    if (!slides.length || !previous || !next || !dots || !caption || !count || !viewport) return;

    const dotButtons = slides.map((slide, index) => {
      const dot = document.createElement("button");
      dot.type = "button";
      dot.className = "carousel-dot";
      dot.setAttribute("aria-label", slide.dataset.caption || `${index + 1}`);
      dot.addEventListener("click", () => show(index));
      dots.append(dot);
      return dot;
    });

    const show = (index) => {
      active = (index + slides.length) % slides.length;
      slides.forEach((slide, slideIndex) => {
        const selected = slideIndex === active;
        slide.classList.toggle("is-active", selected);
        slide.setAttribute("aria-hidden", String(!selected));
      });
      dotButtons.forEach((dot, dotIndex) => {
        dot.setAttribute("aria-current", String(dotIndex === active));
      });
      caption.textContent = slides[active].dataset.caption || "";
      count.textContent = `${numbers.format(active + 1)} / ${numbers.format(slides.length)}`;
      const activeImage = slides[active].querySelector("img");
      if (activeImage) activeImage.loading = "eager";
    };

    previous.addEventListener("click", () => show(active - 1));
    next.addEventListener("click", () => show(active + 1));
    carousel.addEventListener("keydown", (event) => {
      const rtl = document.documentElement.dir === "rtl";
      if (event.key === "ArrowRight") show(active + (rtl ? -1 : 1));
      else if (event.key === "ArrowLeft") show(active + (rtl ? 1 : -1));
      else if (event.key === "Home") show(0);
      else if (event.key === "End") show(slides.length - 1);
      else return;
      event.preventDefault();
    });
    viewport.addEventListener("pointerdown", (event) => {
      pointerStart = event.clientX;
    });
    viewport.addEventListener("pointerup", (event) => {
      if (pointerStart === null) return;
      const distance = event.clientX - pointerStart;
      pointerStart = null;
      if (Math.abs(distance) < 44) return;
      show(active + (distance < 0 ? 1 : -1));
    });
    viewport.addEventListener("pointercancel", () => {
      pointerStart = null;
    });

    show(0);
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
    const select = quickInstall.querySelector("select");
    const command = quickInstall.querySelector("code");
    if (!select || !command) return;
    select.addEventListener("change", () => {
      const option = select.options[select.selectedIndex];
      command.textContent = option.dataset.command || "";
      command.title = command.textContent;
    });
  });
})();

document.addEventListener("DOMContentLoaded", function () {
  // ==============================
  // Utilitários
  // ==============================
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

  // Ano automático
  $$(".js-year").forEach((el) => {
    el.textContent = new Date().getFullYear();
  });

  // ==============================
  // Sidebar mobile
  // ==============================
  const btn = $("#sidebar-toggle-mobile");
  const sidebar = $("#sidebar");

  let overlay = $(".mobile-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "mobile-overlay";
    document.body.appendChild(overlay);
  }

  let scrollY = 0;

  function lockScroll() {
    scrollY = window.scrollY;
    document.body.style.position = "fixed";
    document.body.style.top = `-${scrollY}px`;
    document.body.style.left = "0";
    document.body.style.right = "0";
    document.body.style.width = "100%";
  }

  function unlockScroll() {
    const top = document.body.style.top;
    document.body.style.position = "";
    document.body.style.top = "";
    document.body.style.left = "";
    document.body.style.right = "";
    document.body.style.width = "";
    window.scrollTo(0, Math.abs(parseInt(top || "0", 10)));
  }

  function openSidebar() {
    document.body.classList.add("sidebar-open");
    overlay.classList.add("show");
    lockScroll();
    if (btn) btn.setAttribute("aria-expanded", "true");
  }

  function closeSidebar() {
    document.body.classList.remove("sidebar-open");
    overlay.classList.remove("show");
    unlockScroll();
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function toggleSidebar() {
    document.body.classList.contains("sidebar-open") ? closeSidebar() : openSidebar();
  }

  if (btn && sidebar) {
    btn.addEventListener("click", toggleSidebar);
    overlay.addEventListener("click", closeSidebar);

    // Fecha ao clicar em links (mobile)
    $$("a", sidebar).forEach((link) => {
      link.addEventListener("click", function () {
        if (window.innerWidth < 992) closeSidebar();
      });
    });

    window.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && document.body.classList.contains("sidebar-open")) {
        closeSidebar();
      }
    });

    window.addEventListener("resize", function () {
      if (window.innerWidth >= 992 && document.body.classList.contains("sidebar-open")) {
        closeSidebar();
      }
    });
  }

  // ==============================
  // Busca do navbar: não envia
  // ==============================
  $$('form[role="search"]').forEach((form) => {
    form.addEventListener("submit", (e) => e.preventDefault());
  });

  // ==============================
  // Confirmação segura para alteração de status
  // ==============================
  $$("form[action*='/status/']").forEach((form) => {
    form.addEventListener("submit", function (e) {
      try {
        const url = new URL(form.action, window.location.origin);
        const parts = url.pathname.split("/").filter(Boolean);
        const status = parts[parts.length - 1] || "novo status";
        if (!confirm(`Alterar status para "${decodeURIComponent(status)}"?`)) {
          e.preventDefault();
        }
      } catch {
        if (!confirm("Confirmar alteração de status?")) {
          e.preventDefault();
        }
      }
    });
  });

  // ==============================
  // Fechar alerts automaticamente
  // ==============================
  $$(".alert[data-auto-close='true']").forEach((alertEl) => {
    const delay = parseInt(alertEl.dataset.autoCloseDelay || "4000", 10);
    setTimeout(() => {
      alertEl.classList.add("hide");
      setTimeout(() => alertEl.remove(), 300);
    }, delay);
  });

  // ==============================
  // Botões de copiar texto
  // ==============================
  $$("[data-copy-target], [data-copy-text]").forEach((btnCopy) => {
    btnCopy.addEventListener("click", async function () {
      const targetSelector = btnCopy.dataset.copyTarget;
      const directText = btnCopy.dataset.copyText;
      let textToCopy = "";

      if (directText) {
        textToCopy = directText;
      } else if (targetSelector) {
        const target = $(targetSelector);
        if (target) {
          textToCopy = target.value || target.textContent || "";
        }
      }

      textToCopy = textToCopy.trim();
      if (!textToCopy) return alert("Nada para copiar.");

      try {
        await navigator.clipboard.writeText(textToCopy);
      } catch {
        // Fallback para document.execCommand
        const textarea = document.createElement("textarea");
        textarea.value = textToCopy;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
      }

      const original = btnCopy.innerHTML;
      btnCopy.innerHTML = "✅ Copiado";
      btnCopy.disabled = true;
      setTimeout(() => {
        btnCopy.innerHTML = original;
        btnCopy.disabled = false;
      }, 1600);
    });
  });

  // ==============================
  // Preview de imagem
  // ==============================
  $$('input[type="file"][data-preview]').forEach((input) => {
    input.addEventListener("change", function () {
      const previewSelector = input.dataset.preview;
      const preview = $(previewSelector);
      if (!preview || !input.files?.[0]) return;

      const file = input.files[0];
      if (!file.type.startsWith("image/")) return;

      const reader = new FileReader();
      reader.onload = (e) => {
        if (preview.tagName === "IMG") {
          preview.src = e.target.result;
        } else {
          preview.style.backgroundImage = `url('${e.target.result}')`;
        }
      };
      reader.readAsDataURL(file);
    });
  });

  // ==============================
  // Máscara de telefone (corrigida)
  // ==============================
  $$('input[data-mask="phone"]').forEach((input) => {
    input.addEventListener("input", function () {
      let v = this.value.replace(/\D/g, "").slice(0, 11);
      if (v.length === 0) {
        this.value = "";
        return;
      }
      if (v.length <= 2) {
        this.value = v;
      } else if (v.length <= 6) {
        this.value = `(${v.slice(0, 2)}) ${v.slice(2)}`;
      } else if (v.length <= 10) {
        this.value = `(${v.slice(0, 2)}) ${v.slice(2, 6)}-${v.slice(6)}`;
      } else {
        this.value = `(${v.slice(0, 2)}) ${v.slice(2, 7)}-${v.slice(7, 11)}`;
      }
    });
  });

  // ==============================
  // Máscara de CPF
  // ==============================
  $$('input[data-mask="cpf"]').forEach((input) => {
    input.addEventListener("input", function () {
      let v = this.value.replace(/\D/g, "").slice(0, 11);
      v = v.replace(/^(\d{3})(\d)/, "$1.$2");
      v = v.replace(/^(\d{3})\.(\d{3})(\d)/, "$1.$2.$3");
      v = v.replace(/\.(\d{3})(\d)/, ".$1-$2");
      this.value = v;
    });
  });

  // ==============================
  // Alternar visibilidade de senha
  // ==============================
  $$("[data-toggle-password]").forEach((toggleBtn) => {
    toggleBtn.addEventListener("click", function () {
      const selector = this.dataset.togglePassword;
      const input = $(selector);
      if (!input) return;

      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";
      this.setAttribute("aria-pressed", String(isPassword));
    });
  });

  // ==============================
  // Confirmação genérica (form, link, botão)
  // ==============================
  $$("[data-confirm]").forEach((el) => {
    const eventName = el.tagName === "FORM" ? "submit" : "click";
    el.addEventListener(eventName, function (e) {
      const msg = this.dataset.confirm || "Tem certeza?";
      if (!confirm(msg)) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  });

  // ==============================
  // Tabs simples
  // ==============================
  $$("[data-tab-target]").forEach((tabBtn) => {
    tabBtn.addEventListener("click", function () {
      const targetSelector = this.dataset.tabTarget;
      const target = $(targetSelector);
      if (!target) return;

      const group = this.dataset.tabGroup || "default";

      $$(`[data-tab-target][data-tab-group="${group}"]`).forEach((btn) => {
        btn.classList.remove("active");
      });

      $$(`.tab-pane[data-tab-group="${group}"]`).forEach((pane) => {
        pane.classList.remove("active");
        pane.hidden = true;
      });

      this.classList.add("active");
      target.classList.add("active");
      target.hidden = false;
    });
  });

  // ==============================
  // Accordion
  // ==============================
  $$(".js-accordion-header").forEach((header) => {
    header.addEventListener("click", function () {
      const body = this.nextElementSibling;
      if (!body) return;
      const isOpen = this.classList.contains("open");
      this.classList.toggle("open", !isOpen);
      body.hidden = isOpen;
    });
  });

  // ==============================
  // Auto-submit em selects e inputs
  // ==============================
  $$("select[data-auto-submit], input[data-auto-submit]").forEach((el) => {
    el.addEventListener("change", function () {
      const form = this.closest("form");
      if (form) form.submit();
    });
  });

  // ==============================
  // Loading em botões ao enviar form
  // ==============================
  $$("form").forEach((form) => {
    let originalBtnText = new Map();

    form.addEventListener("submit", function () {
      const submitButtons = $$('button[type="submit"], input[type="submit"]', this);
      submitButtons.forEach((btn) => {
        const loadingText = btn.getAttribute("data-loading-text");
        if (!loadingText) return;

        if (btn.tagName === "BUTTON") {
          originalBtnText.set(btn, btn.innerHTML);
          btn.innerHTML = loadingText;
        } else {
          originalBtnText.set(btn, btn.value);
          btn.value = loadingText;
        }
        btn.disabled = true;
      });
    });

    // Caso a submissão falhe (ex: AJAX com erro), restaura os botões
    // Isso é útil se você usar fetch. Para submit tradicional, a página recarrega.
    // Mantemos como fallback para projetos que usam AJAX.
    form.addEventListener("ajax:error", function () {
      const submitButtons = $$('button[type="submit"], input[type="submit"]', this);
      submitButtons.forEach((btn) => {
        if (originalBtnText.has(btn)) {
          if (btn.tagName === "BUTTON") {
            btn.innerHTML = originalBtnText.get(btn);
          } else {
            btn.value = originalBtnText.get(btn);
          }
          btn.disabled = false;
          originalBtnText.delete(btn);
        }
      });
    });
  });
});
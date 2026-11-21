document.addEventListener("DOMContentLoaded", () => {
    const toggle = document.getElementById("t2cToggle");
    const links = document.getElementById("t2cLinks");

    if (toggle && links) {
        toggle.addEventListener("click", () => {
            const open = links.classList.toggle("open");
            toggle.classList.toggle("open", open);
            toggle.setAttribute("aria-expanded", String(open));
        });

        links.querySelectorAll("a").forEach((a) => {
            a.addEventListener("click", () => {
                links.classList.remove("open");
                toggle.classList.remove("open");
                toggle.setAttribute("aria-expanded", "false");
            });
        });
    }

    const trigger = document.getElementById("t2cProfileTrigger");
    const dropdown = document.getElementById("t2cDropdown");

    if (trigger && dropdown) {
        const close = () => {
            dropdown.classList.remove("open");
            trigger.setAttribute("aria-expanded", "false");
        };

        trigger.addEventListener("click", (e) => {
            e.stopPropagation();
            const open = !dropdown.classList.contains("open");
            dropdown.classList.toggle("open", open);
            trigger.setAttribute("aria-expanded", String(open));
        });

        document.addEventListener("click", (e) => {
            if (!dropdown.contains(e.target) && !trigger.contains(e.target)) {
                close();
            }
        });

        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") {
                close();
            }
        });
    }

    // Home link: if we're already on the Home page, don't reload the whole
    // page -- just scroll smoothly back to the top. Otherwise let it navigate
    // there as normal.
    const homeLink = document.getElementById("home");

    if (homeLink) {
        homeLink.addEventListener("click", (e) => {
            const linkUrl = new URL(homeLink.href, location.href);

            if (linkUrl.pathname === location.pathname) {
                e.preventDefault();
                window.scrollTo({ top: 0, behavior: "smooth" });
            }
        });
    }
});

/* ---------------------------------------------------------------------
   Shared UI helpers: confirmation dialog + toast notifications.
   (Replace the browser's confirm()/alert(), which look out of place.)

   Confirm:  <form method="POST" data-confirm="Request a refund for this plan?"
                   data-confirm-title="Request a refund" data-confirm-button="Request refund">
   Toast:    T2C.toast("Saved", "success")   // success | info | warning | error
--------------------------------------------------------------------- */
(() => {
    "use strict";
    const T2C = (window.T2C = window.T2C || {});

    // ---- confirmation dialog ----
    let lastFocus = null;
    const closeDialog = (overlay) => {
        overlay.classList.remove("active");
        window.setTimeout(() => overlay.remove(), 250);
        document.removeEventListener("keydown", overlay._onKey);
        if (lastFocus && lastFocus.focus) lastFocus.focus();
    };
    T2C.confirm = ({ title, message, confirmLabel, danger }) =>
        new Promise((resolve) => {
            lastFocus = document.activeElement;
            const overlay = document.createElement("div");
            overlay.className = "t2c-confirm-overlay";
            overlay.innerHTML =
                '<div class="t2c-confirm-box" role="alertdialog" aria-modal="true" aria-labelledby="t2cConfirmTitle" aria-describedby="t2cConfirmText">' +
                '<h3 id="t2cConfirmTitle"></h3><p id="t2cConfirmText"></p>' +
                '<div class="t2c-confirm-actions"><button type="button" class="t2c-confirm-cancel">Cancel</button>' +
                '<button type="button" class="t2c-confirm-ok"></button></div></div>';
            overlay.querySelector("h3").textContent = title || "Are you sure?";
            overlay.querySelector("p").textContent = message || "";
            const ok = overlay.querySelector(".t2c-confirm-ok");
            ok.textContent = confirmLabel || "Confirm";
            ok.classList.add(danger === false ? "t2c-confirm-primary" : "t2c-confirm-delete");
            const cancel = overlay.querySelector(".t2c-confirm-cancel");
            const done = (value) => { closeDialog(overlay); resolve(value); };
            ok.addEventListener("click", () => done(true));
            cancel.addEventListener("click", () => done(false));
            overlay.addEventListener("click", (e) => { if (e.target === overlay) done(false); });
            overlay._onKey = (e) => {
                if (e.key === "Escape") done(false);
                if (e.key === "Tab") {                       // keep keyboard focus inside the dialog
                    const first = cancel, last = ok;
                    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
                }
            };
            document.addEventListener("keydown", overlay._onKey);
            document.body.appendChild(overlay);
            requestAnimationFrame(() => { overlay.classList.add("active"); cancel.focus(); });
        });

    // Runs before loader.js's submit handler (capture phase), so a cancelled dialog never shows the loader.
    document.addEventListener("submit", (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || form.dataset.confirm === undefined || form.dataset.confirmed === "1") return;
        event.preventDefault();
        event.stopImmediatePropagation();
        T2C.confirm({
            title: form.dataset.confirmTitle, message: form.dataset.confirm,
            confirmLabel: form.dataset.confirmButton, danger: form.dataset.confirmTone !== "primary",
        }).then((yes) => {
            if (!yes) return;
            form.dataset.confirmed = "1";
            if (form.requestSubmit) form.requestSubmit(); else form.submit();
        });
    }, true);

    // ---- toast notifications ----
    T2C.toast = (message, kind = "info", ms = 5000) => {
        let stack = document.getElementById("t2cToasts");
        if (!stack) {
            stack = document.createElement("div");
            stack.id = "t2cToasts";
            stack.className = "t2c-toasts";
            stack.setAttribute("role", "status");
            stack.setAttribute("aria-live", "polite");
            document.body.appendChild(stack);
        }
        const toast = document.createElement("div");
        toast.className = "t2c-toast t2c-toast-" + kind;
        toast.textContent = message;
        stack.appendChild(toast);
        window.setTimeout(() => { toast.classList.add("t2c-toast-hide"); window.setTimeout(() => toast.remove(), 300); }, ms);
    };
})();

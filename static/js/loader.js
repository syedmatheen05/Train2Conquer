(() => {
    "use strict";
    const getLoader = () => document.getElementById("page-loader");
    const hide = () => {
        const loader = getLoader();
        document.documentElement.classList.remove("t2c-loading");
        document.body?.classList.remove("t2c-loading");
        if (loader) loader.classList.add("hide");
    };
    const show = (message = "Loading...") => {
        const loader = getLoader();
        if (!loader) return;
        const text = document.getElementById("loader-message");
        if (text) text.textContent = message;
        document.documentElement.classList.add("t2c-loading");
        document.body?.classList.add("t2c-loading");
        loader.classList.remove("hide");
    };
    const local = (url) => {
        try {
            const u = new URL(url, location.href);
            return u.origin === location.origin &&
                   (u.protocol === "http:" || u.protocol === "https:");
        } catch {
            return false;
        }
    };
    // Make the loader available to page-specific JavaScript.
    // This is important for buttons that submit a form programmatically.
    window.T2CLoader = { show, hide };
    const setupFlashMessages = () => {
        const flashes = document.querySelectorAll("[data-flash-message]");
        flashes.forEach((flash, index) => {
            // Stagger multiple flash messages slightly, then dismiss them.
            window.setTimeout(() => {
                flash.classList.add("t2c-flash-hide");
                window.setTimeout(() => flash.remove(), 350);
            }, 3000 + (index * 250));
        });
    };
    document.addEventListener("DOMContentLoaded", () => {
        setupFlashMessages();
        const loader = getLoader();
        if (!loader) return;
        const finish = () => window.setTimeout(hide, 100);
        if (document.readyState === "complete") {
            finish();
        } else {
            window.addEventListener("load", finish, { once: true });
        }
        // Normal page links.
        document.addEventListener("click", (event) => {
            const link = event.target.closest("a");
            if (!link || event.defaultPrevented) return;
            if (
                link.dataset.noLoader !== undefined ||
                link.target === "_blank" ||
                link.hasAttribute("download")
            ) return;
            const href = link.getAttribute("href");
            if (
                !href ||
                href.startsWith("#") ||
                href.startsWith("mailto:") ||
                href.startsWith("tel:") ||
                href.startsWith("javascript:")
            ) return;
            if (!local(link.href)) return;
            const current = new URL(location.href);
            const target = new URL(link.href, location.href);
            if (target.href !== current.href) {
                show(link.dataset.loaderMessage || "Loading...");
            }
        });
        // Real user-submitted forms.
        document.addEventListener("submit", (event) => {
            const form = event.target;
            if (!(form instanceof HTMLFormElement) || event.defaultPrevented) return;
            if (
                form.dataset.noLoader !== undefined ||
                form.target === "_blank"
            ) return;
            const action = form.getAttribute("action") || location.href;
            if (local(action)) {
                show(form.dataset.loaderMessage || "Processing...");
            }
            // Disable submit buttons immediately so a second click (or an
            // impatient double-click) can't fire a second submit -- this is
            // what causes duplicate OTP/contact/trainer-application emails
            // and duplicate account actions. Marked with a data attribute so
            // pageshow (bfcache restore) only re-enables ones *we* disabled,
            // not buttons that are intentionally disabled for other reasons
            // (e.g. "Load Next Week" until all 7 days are complete).
            form.querySelectorAll('button[type="submit"], input[type="submit"]')
                .forEach((btn) => {
                    if (!btn.disabled) {
                        btn.disabled = true;
                        btn.dataset.t2cLoaderDisabled = "true";
                    }
                });
        });
        window.addEventListener("pageshow", (event) => {
            hide();
            // If this page came from the back/forward cache, its submit
            // buttons may still be disabled from before navigating away --
            // re-enable them so the form is usable again.
            if (event.persisted) {
                document.querySelectorAll('[data-t2c-loader-disabled="true"]')
                    .forEach((btn) => {
                        btn.disabled = false;
                        delete btn.dataset.t2cLoaderDisabled;
                    });
            }
        });
        // Delete-account confirmation modal.
        // The button/form live in navbar.html (shared on every page);
        // the modal markup travels with them in navbar.html too, so
        // this works from any page, not just Home.
        const deleteBtn = document.getElementById("deleteAccountBtn");
        const modal = document.getElementById("deleteConfirmModal");
        const cancelBtn = document.getElementById("cancelDelete");
        const confirmBtn = document.getElementById("confirmDelete");
        const deleteForm = document.getElementById("deleteAccountForm");
        if (deleteBtn && modal && cancelBtn && confirmBtn && deleteForm) {
            deleteBtn.addEventListener("click", () => {
                modal.classList.add("active");
            });
            cancelBtn.addEventListener("click", () => {
                modal.classList.remove("active");
            });
            confirmBtn.addEventListener("click", () => {
                // Prevent accidental double click.
                confirmBtn.disabled = true;
                confirmBtn.textContent = "Deleting...";
                // requestSubmit() (not submit()) so the shared "submit"
                // listener above still fires and shows the loader.
                if (deleteForm.requestSubmit) {
                    deleteForm.requestSubmit();
                } else {
                    show(deleteForm.dataset.loaderMessage || "Processing...");
                    deleteForm.submit();
                }
            });
            modal.addEventListener("click", (event) => {
                if (event.target === modal) {
                    modal.classList.remove("active");
                }
            });
            document.addEventListener("keydown", (event) => {
                if (event.key === "Escape") {
                    modal.classList.remove("active");
                }
            });
        }
    });
})();

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
        });

        window.addEventListener("pageshow", hide);
    });
})();

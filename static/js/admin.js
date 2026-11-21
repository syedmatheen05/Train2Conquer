/* Admin panel behaviour (kept tiny: the page works without it).
   - the active sidebar/tab link scrolls into view on small screens
   - filter forms show a busy state while the next page loads (loader.js shows the spinner) */
(() => {
    "use strict";
    document.addEventListener("DOMContentLoaded", () => {
        const active = document.querySelector(".t2c-admin-navlink.is-active");
        if (active && active.scrollIntoView && window.matchMedia("(max-width: 960px)").matches) {
            active.scrollIntoView({ inline: "center", block: "nearest" });
        }
        document.querySelectorAll("form.t2c-admin-search").forEach((form) => {
            form.addEventListener("submit", () => form.setAttribute("aria-busy", "true"));
        });
    });
})();

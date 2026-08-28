
document.addEventListener("DOMContentLoaded", function () {

    const loader = document.getElementById("page-loader");

    // Hide loader when current page finishes loading
    window.addEventListener("load", function () {
        setTimeout(() => {
            loader.classList.add("hide");
        }, 150);
    });

    // Show loader when navigating to another page
    document.addEventListener("click", function (event) {

        const link = event.target.closest("a");

        if (!link) return;

        const href = link.getAttribute("href");

        // Ignore special links
        if (
            !href ||
            href.startsWith("#") ||
            href.startsWith("javascript:") ||
            href.startsWith("mailto:") ||
            href.startsWith("tel:") ||
            link.target === "_blank" ||
            link.hasAttribute("download")
        ) {
            return;
        }

        // Ignore external websites
        if (link.origin !== window.location.origin) {
            return;
        }

        // Don't show loader for same page
        if (link.href === window.location.href) {
            return;
        }

        loader.classList.remove("hide");
    });

});

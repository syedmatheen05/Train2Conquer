/* Instant feedback for forms. The SERVER enforces the same rules (validation.py);
   this only saves a round trip and never replaces that check.

     <input data-validate="person-name">                   letters, spaces, hyphens, apostrophes
     <input type="date" data-validate="min-age" data-min-age="15">   old enough, not in the future  */
(() => {
    "use strict";
    const NAME_ERROR = "Please enter a valid name using letters only.";
    const normaliseName = (v) => v.replace(/\s+/g, " ").trim();

    const nameProblem = (raw) => {
        const name = normaliseName(raw);
        if (!name) return "Please enter your name.";
        const letters = (name.match(/\p{L}/gu) || []).length;
        if (name.length > 60 || !/^[\p{L}\p{M}' \-\u2019.]+$/u.test(name) || letters < 2) return NAME_ERROR;
        if (/^[-'\u2019.]/.test(name) || /[-'\u2019]{2,}/.test(name)) return NAME_ERROR;
        return null;
    };

    // Whole years completed today (a birthday that hasn't happened yet this year doesn't count).
    const ageProblem = (value, minAge) => {
        if (!value) return "Please enter your date of birth.";
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
        if (!m) return "Please enter a valid date of birth.";
        const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
        const now = new Date();
        const born = new Date(y, mo - 1, d);
        if (born.getFullYear() !== y || born.getMonth() !== mo - 1 || born.getDate() !== d) return "Please enter a valid date of birth.";
        if (born > now) return "Date of birth can't be in the future.";
        let age = now.getFullYear() - y;
        if (now.getMonth() + 1 < mo || (now.getMonth() + 1 === mo && now.getDate() < d)) age -= 1;
        return age < minAge ? `You must be at least ${minAge} years old to create a fitness profile.` : null;
    };

    const problemFor = (input) => {
        const kind = input.dataset.validate;
        if (kind === "person-name") return nameProblem(input.value);
        if (kind === "min-age") return ageProblem(input.value, Number(input.dataset.minAge || 15));
        return null;
    };

    const show = (input, message) => {
        let box = input.parentElement.querySelector(".t2c-js-error");
        if (!message) {
            if (box) box.remove();
            input.classList.remove("is-invalid");
            input.removeAttribute("aria-invalid");
            return;
        }
        if (!box) {
            box = document.createElement("div");
            box.className = "invalid-feedback t2c-js-error";
            box.setAttribute("role", "alert");
            input.insertAdjacentElement("afterend", box);
        }
        box.textContent = message;
        input.classList.add("is-invalid");
        input.setAttribute("aria-invalid", "true");
    };

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("[data-validate]").forEach((input) => {
            const check = () => { const p = problemFor(input); show(input, p); return !p; };
            input.addEventListener("blur", () => {
                if (input.dataset.validate === "person-name") input.value = normaliseName(input.value);   // trim stray spaces
                if (input.value || input.classList.contains("is-invalid")) check();
            });
            input.addEventListener("input", () => { if (input.classList.contains("is-invalid")) check(); });
            input.addEventListener("change", check);
            if (input.form) {
                input.form.addEventListener("submit", (event) => {
                    if (!check()) { event.preventDefault(); event.stopImmediatePropagation(); input.focus(); }
                }, true);   // capture phase: runs before the page loader appears
            }
        });
    });
})();

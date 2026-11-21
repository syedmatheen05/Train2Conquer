/* Fitness profile: equipment selection rules (a convenience only - the server
   enforces the same rules in equipment.py / forms.py).

     "No equipment" and "Gym" each stand alone:
       ticking one clears every other option (including the other one);
     ticking any individual item (dumbbells, bands, ...) clears both of them;
     individual items can be combined freely.                                   */
(() => {
    "use strict";
    document.addEventListener("DOMContentLoaded", () => {
        const list = document.getElementById("equipment");
        if (!list) return;

        const boxes = Array.from(list.querySelectorAll('input[type="checkbox"]'));
        const STANDALONE = ["none", "gym"];
        const note = document.createElement("p");
        note.className = "t2c-equipment-note";
        note.setAttribute("role", "status");
        note.setAttribute("aria-live", "polite");
        list.insertAdjacentElement("afterend", note);

        const describe = () => {
            const picked = boxes.filter((b) => b.checked).map((b) => b.value);
            if (picked.includes("gym")) note.textContent = "Gym: your plan can use standard gym equipment.";
            else if (picked.includes("none")) note.textContent = "No equipment: your plan will use bodyweight exercises only.";
            else if (picked.length) note.textContent = "Your plan will use only the equipment you selected, plus bodyweight exercises.";
            else note.textContent = "Select at least one option.";
        };

        boxes.forEach((box) => {
            box.addEventListener("change", () => {
                if (box.checked) {
                    boxes.forEach((other) => {
                        if (other === box) return;
                        // A standalone option clears everything else; an individual item clears the standalone ones.
                        if (STANDALONE.includes(box.value) || STANDALONE.includes(other.value)) other.checked = false;
                    });
                }
                describe();
            });
        });

        // Normalise whatever the page was loaded with (saved profile, or a re-shown form after an error).
        const chosenStandalone = boxes.find((b) => b.checked && STANDALONE.includes(b.value));
        if (chosenStandalone) {
            boxes.forEach((b) => { if (b !== chosenStandalone) b.checked = false; });
        }
        describe();
    });
})();

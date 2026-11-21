/* Paid plans: Razorpay checkout + what to do when a payment does not go through.

   The browser never decides whether a payment worked. After a failure, or when the
   payment window is closed, we ask the server (/payments/failed), which checks the
   real status with Razorpay, and then offer only the actions that make sense:

     failed     -> Retry payment (same order) | Try another payment method (fresh order)
     cancelled  -> the same two actions
     pending    -> wait / check status (no second payment while one may be going through)
     paid       -> the plan starts; nothing else to do
     unknown    -> we couldn't check: check again (never assume)

   Which payment methods the customer can choose from is decided by Razorpay Checkout
   from the account's own settings - this file never lists or assumes any method.   */
(() => {
    "use strict";
    const box = document.getElementById("plan-options");
    if (!box) return;

    const message = document.getElementById("plan-message");
    const notesField = document.getElementById("plan-notes");
    const buyButtons = Array.from(box.querySelectorAll(".t2c-buy"));
    const panel = document.getElementById("pay-problem");
    const retryBtn = document.getElementById("pay-retry");
    const anotherBtn = document.getElementById("pay-another");
    const checkBtn = document.getElementById("pay-check");
    const csrf = box.dataset.csrf;
    let busy = false;            // blocks double clicks while a payment is in progress
    let gatewayLoaded = null;
    let active = null;           // { tier, orderId } of the attempt the panel is about

    const stateList = document.getElementById("plan-state");
    // Steps: select -> creating -> processing -> success -> generating -> ready
    const setState = (step) => {
        if (!stateList) return;
        const items = Array.from(stateList.children);
        const at = items.findIndex((li) => li.dataset.step === step);
        stateList.hidden = at < 0 || step === "select";
        items.forEach((li, i) => { li.className = i < at ? "done" : (i === at ? "active" : ""); });
    };
    setState("select");
    const say = (text, kind) => {
        message.textContent = text;
        message.className = "t2c-plan-message" + (kind ? " " + kind : "");
    };
    const actionButtons = () => [retryBtn, anotherBtn, checkBtn].filter(Boolean);
    const lock = (on) => {
        busy = on;
        buyButtons.forEach((b) => { b.disabled = on || b.dataset.unavailable === "1"; });
        actionButtons().forEach((b) => { b.disabled = on; b.setAttribute("aria-busy", on ? "true" : "false"); });
    };
    const postJson = (url, body) =>
        fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
            body: JSON.stringify(body),
        }).then((r) => r.json().catch(() => ({ ok: false, message: "Something went wrong." })));

    const loadGateway = () => {
        if (window.Razorpay) return Promise.resolve();
        if (!gatewayLoaded) {
            gatewayLoaded = new Promise((resolve, reject) => {
                const s = document.createElement("script");
                s.src = "https://checkout.razorpay.com/v1/checkout.js";
                s.onload = resolve;
                s.onerror = () => { gatewayLoaded = null; reject(new Error("load")); };
                document.head.appendChild(s);
            });
        }
        return gatewayLoaded;
    };

    // ------------------------------------------------------------ problem panel
    const ICONS = {
        fail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/></svg>',
        wait: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
        info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    };
    const COPY = {
        failed: { tone: "bad", icon: "fail", title: "Payment unsuccessful",
            text: "Your payment didn't go through, so your plan hasn't been created. Your bank or payment provider didn't complete the payment.",
            fine: "If any money was deducted, it is usually returned to your account automatically within a few working days. If it isn't, contact us with the reference above.",
            actions: ["retry", "another"] },
        cancelled: { tone: "neutral", icon: "info", title: "Payment not completed",
            text: "The payment window was closed before the payment finished. You haven't been charged and no plan was created.",
            fine: "", actions: ["retry", "another"] },
        pending: { tone: "wait", icon: "wait", title: "Your payment is still being processed",
            text: "Please don't pay again yet. If the payment goes through, your plan will start automatically.",
            fine: "This can take a few minutes. You can check the status below.", actions: ["check"] },
        unknown: { tone: "wait", icon: "wait", title: "We couldn't confirm your payment yet",
            text: "We couldn't check the payment status right now. If money was taken, your plan will appear automatically.",
            fine: "Please check again in a minute before paying again.", actions: ["check"] },
    };
    const showProblem = (kind, reference) => {
        const c = COPY[kind] || COPY.unknown;
        panel.hidden = false;
        panel.className = "t2c-pay-panel t2c-pay-" + c.tone;
        document.getElementById("pay-problem-icon").innerHTML = ICONS[c.icon];
        document.getElementById("pay-problem-title").textContent = c.title;
        document.getElementById("pay-problem-text").textContent = c.text;
        document.getElementById("pay-problem-fine").textContent = c.fine;
        document.getElementById("pay-problem-ref").textContent = reference || "";
        document.getElementById("pay-problem-ref-row").hidden = !reference;
        retryBtn.hidden = !c.actions.includes("retry");
        anotherBtn.hidden = !c.actions.includes("another");
        checkBtn.hidden = !c.actions.includes("check");
        say("", "");
        setState("select");
        panel.scrollIntoView({ behavior: "smooth", block: "center" });
        (retryBtn.hidden ? checkBtn : retryBtn).focus({ preventScroll: true });
    };
    const hideProblem = () => { panel.hidden = true; };

    // Ask the server what really happened, then show the matching panel.
    const checkStatus = async (orderId, reason) => {
        lock(true);
        say("Checking your payment...", "");
        try {
            const r = await postJson(box.dataset.failedUrl, { razorpay_order_id: orderId, reason });
            if (!r.ok) { showProblem("unknown", orderId); return; }
            if (r.state === "paid") {
                setState("generating");
                say("Payment successful. Building your workout...", "");
                if (window.T2CLoader) window.T2CLoader.show("Building your plan...");
                window.location.href = r.redirect || "/";
                return;
            }
            active = { tier: r.tier || (active && active.tier), orderId: r.reference || orderId };
            showProblem(r.state === "open" ? "cancelled" : r.state, r.reference || orderId);
        } catch (e) {
            showProblem("unknown", orderId);
        } finally {
            lock(false);
        }
    };

    // ----------------------------------------------------------------- payment
    // mode: "normal" (first try) | "retry" (same order) | "fresh" (another payment method)
    const startPayment = async (tier, mode = "normal", orderId = null) => {
        if (busy) return;
        lock(true);
        hideProblem();
        setState("creating");
        say(mode === "fresh" ? "Starting a new payment..." : "Creating your payment...", "");
        let failure = null;      // set by Razorpay's payment.failed event
        try {
            const body = { tier, notes: notesField ? notesField.value : "" };
            if (mode === "fresh") body.fresh = true;
            if (mode === "retry") { body.retry = true; body.order_id = orderId; }
            const [order] = await Promise.all([postJson(box.dataset.createUrl, body), loadGateway()]);
            if (order.state === "already_paid") {
                say(order.message, "");
                window.location.href = order.redirect || "/";
                return;
            }
            if (order.state === "pending") {
                active = { tier, orderId };
                showProblem("pending", orderId);
                lock(false);
                return;
            }
            if (!order.ok) {
                setState("select");
                say(order.message || "We couldn't start the payment.", "error");
                lock(false);
                return;
            }
            active = { tier, orderId: order.order_id };
            setState("processing");
            const checkout = new window.Razorpay({
                key: order.key_id,            // public key id (safe to show)
                order_id: order.order_id,
                amount: order.amount,         // display only; the server already fixed the price
                currency: order.currency,
                name: "Train2Conquer",
                description: order.label,
                prefill: { name: order.name, email: order.email },
                theme: { color: "#FF5A36" },
                modal: {
                    ondismiss: () => {
                        // Closed without paying, or closed after a failure: the server decides which it was.
                        lock(false);
                        checkStatus(order.order_id, failure ? "failed" : "cancelled");
                    },
                },
                handler: async (result) => {
                    failure = null;
                    setState("processing");
                    say("Confirming your payment...", "");
                    try {
                        const verified = await postJson(box.dataset.verifyUrl, {
                            razorpay_order_id: result.razorpay_order_id,
                            razorpay_payment_id: result.razorpay_payment_id,
                            razorpay_signature: result.razorpay_signature,
                        });
                        if (verified.ok) {
                            setState("generating");
                            say("Payment successful. Building your workout...", "");
                            if (window.T2CLoader) window.T2CLoader.show("Building your plan...");
                            window.location.href = verified.redirect || "/";
                        } else {
                            lock(false);
                            await checkStatus(order.order_id, "failed");   // authoritative answer from the server
                        }
                    } catch (e) {
                        lock(false);
                        showProblem("unknown", order.order_id);
                    }
                },
            });
            // The checkout window stays open after a failure (it has its own retry). We only
            // remember it here; the panel appears when the window is closed.
            checkout.on("payment.failed", (response) => { failure = (response && response.error) || {}; });
            checkout.open();
        } catch (e) {
            setState("select");
            say("We couldn't open the payment window. Check your connection and try again.", "error");
            lock(false);
        }
    };

    buyButtons.forEach((b) => {
        if (b.disabled) b.dataset.unavailable = "1";
        b.addEventListener("click", () => startPayment(b.dataset.tier));
    });
    if (retryBtn) retryBtn.addEventListener("click", () => active && startPayment(active.tier, "retry", active.orderId));
    if (anotherBtn) anotherBtn.addEventListener("click", () => active && startPayment(active.tier, "fresh"));
    if (checkBtn) checkBtn.addEventListener("click", () => active && checkStatus(active.orderId, "failed"));
})();

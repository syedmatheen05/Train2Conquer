(() => {
    "use strict";
    const box = document.getElementById("plan-options");
    if (!box) return;

    const message = document.getElementById("plan-message");
    const notesField = document.getElementById("plan-notes");
    const buyButtons = Array.from(box.querySelectorAll(".t2c-buy"));
    const csrf = box.dataset.csrf;
    let busy = false;      // blocks double clicks while a payment is in progress
    let gatewayLoaded = null;

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
    const lock = (on) => {
        busy = on;
        buyButtons.forEach((b) => { b.disabled = on; });
        if (!on) buyButtons.forEach((b) => { b.disabled = b.dataset.unavailable === "1"; });
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

    const startPayment = async (tier) => {
        if (busy) return;
        lock(true);
        setState("creating");
        say("Creating your payment...", "");
        try {
            const [order] = await Promise.all([
                postJson(box.dataset.createUrl, { tier, notes: notesField ? notesField.value : "" }),
                loadGateway(),
            ]);
            if (!order.ok) {
                setState("select");
                say(order.message || "We couldn't start the payment.", "error");
                lock(false);
                return;
            }
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
                        postJson(box.dataset.cancelUrl, { razorpay_order_id: order.order_id });
                        setState("select");
                        say("Payment cancelled. No plan was created.", "error");
                        lock(false);
                    },
                },
                handler: async (result) => {
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
                            setState("select");
                            say(verified.message || "We couldn't verify your payment.", "error");
                            lock(false);
                        }
                    } catch (e) {
                        say("We couldn't confirm your payment. Please refresh in a minute -- if it went through, your plan will be waiting.", "error");
                        lock(false);
                    }
                },
            });
            checkout.on("payment.failed", () => {
                setState("select");
                say("Payment failed. You have not been charged -- please try again.", "error");
                lock(false);
            });
            checkout.open();
        } catch (e) {
            say("We couldn't open the payment window. Check your connection and try again.", "error");
            lock(false);
        }
    };

    buyButtons.forEach((b) => {
        if (b.disabled) b.dataset.unavailable = "1";
        b.addEventListener("click", () => startPayment(b.dataset.tier));
    });
})();

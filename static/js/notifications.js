(function () {
    "use strict";

    var POLL_URL = "/tickets/activity/";
    var POLL_MS = 10000;
    var cursor = 0;
    var firstPoll = true;
    var audioCtx = null;

    function isMuted() {
        return localStorage.getItem("helpdeskMuted") === "1";
    }

    function updateMuteButton() {
        var btn = document.getElementById("mute-toggle");
        if (btn) btn.textContent = isMuted() ? "🔕" : "🔔";
    }

    function ensureAudioContext() {
        if (!audioCtx) {
            var Ctx = window.AudioContext || window.webkitAudioContext;
            if (Ctx) audioCtx = new Ctx();
        } else if (audioCtx.state === "suspended") {
            audioCtx.resume();
        }
    }

    function beep(freq, startTime, duration) {
        var osc = audioCtx.createOscillator();
        var gain = audioCtx.createGain();
        osc.frequency.value = freq;
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        gain.gain.setValueAtTime(0.15, startTime);
        osc.start(startTime);
        osc.stop(startTime + duration);
    }

    function playAlert() {
        if (isMuted() || !audioCtx) return;
        var now = audioCtx.currentTime;
        beep(880, now, 0.12);
        beep(1180, now + 0.15, 0.12);
    }

    function poll() {
        fetch(POLL_URL + "?after=" + cursor)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) return;
                if (!firstPoll && (data.new_tickets > 0 || data.customer_replies > 0)) {
                    playAlert();
                }
                firstPoll = false;
                cursor = data.latest;
            })
            .catch(function () {});
    }

    document.addEventListener("DOMContentLoaded", function () {
        updateMuteButton();
        var btn = document.getElementById("mute-toggle");
        if (btn) {
            btn.addEventListener("click", function () {
                localStorage.setItem("helpdeskMuted", isMuted() ? "0" : "1");
                updateMuteButton();
            });
        }
        // AudioContext must start from a user gesture (browser autoplay policy).
        document.addEventListener("click", ensureAudioContext, { once: true });
        setInterval(poll, POLL_MS);
    });
})();

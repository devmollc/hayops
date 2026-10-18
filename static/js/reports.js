// Dashboard charts. Lives here rather than inline so script-src needs no
// 'unsafe-inline'; the data comes in via the json_script blocks above it.
document.addEventListener("DOMContentLoaded", function() {
    const COLORS = {
        blue: "rgb(59, 130, 246)",
        blueBg: "rgba(59, 130, 246, 0.1)",
        green: "rgb(34, 197, 94)",
        greenBg: "rgba(34, 197, 94, 0.1)",
        orange: "rgb(249, 115, 22)",
        purple: "rgb(168, 85, 247)",
        red: "rgb(239, 68, 68)",
        yellow: "rgb(234, 179, 8)",
        teal: "rgb(20, 184, 166)",
        pink: "rgb(236, 72, 153)",
    };
    const PALETTE = [COLORS.blue, COLORS.green, COLORS.orange, COLORS.purple, COLORS.red, COLORS.yellow, COLORS.teal, COLORS.pink];

    // Every chart is maintainAspectRatio:false, so Chart.js sizes the canvas to
    // its parent. That parent must have a height of its own (the `relative h-64`
    // wrappers above) — sizing against an auto-height parent makes each resize
    // grow the box that determines the next resize, and the chart runs off the
    // page.
    const BASE = { responsive: true, maintainAspectRatio: false };

    // Ticket Volume (line chart)
    const volumeData = JSON.parse(document.getElementById("volume-data").textContent);
    new Chart(document.getElementById("volumeChart"), {
        type: "line",
        data: {
            labels: volumeData.dates,
            datasets: [
                {
                    label: "Opened",
                    data: volumeData.opened,
                    borderColor: COLORS.blue,
                    backgroundColor: COLORS.blueBg,
                    fill: true,
                    tension: 0.3,
                },
                {
                    label: "Closed",
                    data: volumeData.closed,
                    borderColor: COLORS.green,
                    backgroundColor: COLORS.greenBg,
                    fill: true,
                    tension: 0.3,
                },
            ],
        },
        options: {
            ...BASE,
            plugins: { legend: { position: "bottom" } },
            scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
        },
    });

    // Source Breakdown (doughnut)
    const sourceData = JSON.parse(document.getElementById("source-data").textContent);
    new Chart(document.getElementById("sourceChart"), {
        type: "doughnut",
        data: {
            labels: sourceData.labels,
            datasets: [{
                data: sourceData.counts,
                backgroundColor: PALETTE.slice(0, sourceData.labels.length),
            }],
        },
        options: {
            ...BASE,
            plugins: { legend: { position: "bottom" } },
        },
    });

    // CSAT Trend (line chart)
    const csatData = JSON.parse(document.getElementById("csat-data").textContent);
    new Chart(document.getElementById("csatChart"), {
        type: "line",
        data: {
            labels: csatData.dates,
            datasets: [{
                label: "Avg Rating",
                data: csatData.averages,
                borderColor: COLORS.orange,
                backgroundColor: "rgba(249, 115, 22, 0.1)",
                fill: true,
                tension: 0.3,
            }],
        },
        options: {
            ...BASE,
            plugins: { legend: { position: "bottom" } },
            scales: { y: { beginAtZero: true, max: 5, ticks: { precision: 1 } } },
        },
    });
});

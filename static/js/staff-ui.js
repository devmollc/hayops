// Delegated replacements for the onchange/onclick/onsubmit attributes these
// pages used to carry. Inline handlers are exactly what a CSP without
// 'unsafe-inline' blocks, and delegation also survives htmx swaps: the
// listeners sit on document, so partials that arrive later need no re-binding.

// Filter and sidebar <select>s that submit their own form on change.
// data-submit="submit" uses form.submit(), "request" uses requestSubmit() so
// htmx's hx-post on the form still fires.
document.addEventListener("change", function (event) {
    const select = event.target.closest("[data-submit]");
    if (!select || !select.form) return;
    if (select.dataset.submit === "request") {
        select.form.requestSubmit();
    } else {
        select.form.submit();
    }
});

// Appends rather than replaces: an agent halfway through a reply should not
// lose it to a canned response. `value` may be a string or a promise of one.
function insertIntoTarget(button, value) {
    const field = document.getElementById(button.closest("[data-target]").dataset.target);
    Promise.resolve(value).then((text) => {
        field.value = field.value ? field.value.trimEnd() + "\n\n" + text : text;
        field.focus();
    });
}

function csrfToken() {
    const input = document.querySelector("[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
}

document.addEventListener("click", function (event) {
    // Knowledge-base result: the link text is already on the element.
    const link = event.target.closest("[data-insert-link]");
    if (link) {
        insertIntoTarget(link, link.dataset.insertLink);
        return;
    }

    // Canned response: fetch the rendered body, then insert it.
    const canned = event.target.closest("[data-insert-url]");
    if (canned) {
        insertIntoTarget(
            canned,
            fetch(canned.dataset.insertUrl, {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken() },
            })
                .then((response) => response.json())
                .then((data) => data.body)
        );
        return;
    }

    // Merge picker: copy the chosen reference into the merge target field.
    const mergeTarget = event.target.closest("[data-merge-reference]");
    if (mergeTarget) {
        document.getElementById("merge-target").value = mergeTarget.dataset.mergeReference;
    }
});

// Forms that ask before they act, e.g. deleting a webhook endpoint.
document.addEventListener("submit", function (event) {
    const form = event.target.closest("[data-confirm]");
    if (form && !window.confirm(form.dataset.confirm)) {
        event.preventDefault();
    }
});

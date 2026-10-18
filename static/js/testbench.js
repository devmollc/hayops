// The testbench board re-renders itself every few seconds (templates/testbench/board.html).
// This keeps that from eating a half-written result.

// The poll's trigger filter: hold off while a form is open, focused, or holds unsaved input.
window.tbBusy = () =>
    Boolean(document.querySelector(".tb details[open], .tb form.is-dirty")) ||
    Boolean(document.activeElement?.closest?.(".tb form"));

for (const type of ["input", "change"]) {
    document.addEventListener(type, (event) => event.target.form?.classList.add("is-dirty"));
}

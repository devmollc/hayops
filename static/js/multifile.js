// Native <input type="file" multiple> replaces the whole selection every time the
// picker is used, so a second pick silently drops the first batch. Accumulate
// instead, and render a chip per file so a wrong pick can be removed without
// starting the form over.
const fileKey = (file) => `${file.name}:${file.size}:${file.lastModified}`;

function fileChipList(input) {
    if (!input._chipList) {
        input._chipList = document.createElement("ul");
        input._chipList.className = "file-chips";
        input.insertAdjacentElement("afterend", input._chipList);
    }
    return input._chipList;
}

function setPickedFiles(input, files) {
    const picked = new DataTransfer();
    for (const file of files) picked.items.add(file);
    input._pickedFiles = [...picked.files];
    input.files = picked.files;  // assignment does not re-fire change

    const list = fileChipList(input);
    list.replaceChildren(...input._pickedFiles.map((file) => {
        const name = document.createElement("span");
        name.className = "file-chip-name";
        name.textContent = file.name;
        name.title = file.name;

        const remove = document.createElement("button");
        remove.type = "button";  // a bare button inside a form submits it
        remove.className = "file-chip-remove";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.addEventListener("click", () => {
            setPickedFiles(input, input._pickedFiles.filter((f) => fileKey(f) !== fileKey(file)));
            (list.querySelector("button") || input).focus();
        });

        const chip = document.createElement("li");
        chip.className = "file-chip";
        chip.append(name, remove);
        return chip;
    }));
}

document.addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "file" || !input.multiple) return;

    const seen = new Set();
    const merged = [];
    for (const file of [...(input._pickedFiles || []), ...input.files]) {
        if (seen.has(fileKey(file))) continue;
        seen.add(fileKey(file));
        merged.push(file);
    }
    setPickedFiles(input, merged);
});

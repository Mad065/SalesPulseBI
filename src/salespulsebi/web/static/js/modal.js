import { disposeChartsIn } from "./charts.js";

let initialized = false;
let currentOnClose = null;

function isOutsideDialog(event, dialog) {
  const r = dialog.getBoundingClientRect();
  return (
    event.clientX < r.left ||
    event.clientX > r.right ||
    event.clientY < r.top ||
    event.clientY > r.bottom
  );
}

function ensureDialogInit(dialog) {
  if (initialized || !dialog) return;
  initialized = true;

  dialog.addEventListener("close", () => {
    disposeChartsIn(dialog);
    dialog.returnValue = "";
    dialog.innerHTML = "";
    if (typeof currentOnClose === "function") {
      const cb = currentOnClose;
      currentOnClose = null;
      cb();
    }
  });

  // Fallback for browsers without native <dialog closedby="any"> light-dismiss support (e.g. Safari)
  if (!("closedBy" in HTMLDialogElement.prototype)) {
    let pressedOutside = false;
    dialog.addEventListener("pointerdown", (e) => {
      if (!dialog.open) return;
      pressedOutside = isOutsideDialog(e, dialog);
    });
    dialog.addEventListener("pointerup", (e) => {
      if (dialog.open && pressedOutside && isOutsideDialog(e, dialog)) {
        dialog.close();
      }
      pressedOutside = false;
    });
  }
}

export function getDialogElement() {
  const dialog = document.getElementById("modal-dialog");
  ensureDialogInit(dialog);
  return dialog;
}

export function openDialog(html, { wide = false, onClose = null } = {}) {
  const dialog = getDialogElement();
  if (!dialog) return null;
  if (dialog.open) {
    disposeChartsIn(dialog);
    dialog.close();
  }
  currentOnClose = onClose;
  dialog.classList.toggle("modal-box-wide", !!wide);
  dialog.innerHTML = html;
  dialog.showModal();
  return dialog;
}

export function closeDialog() {
  const dialog = document.getElementById("modal-dialog");
  if (dialog?.open) {
    dialog.close();
  }
}

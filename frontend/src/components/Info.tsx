import { useState, type ReactNode } from "react";

/** A small "?" button that reveals a plain-language explanation below its heading. */
export function Info({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className={`info${open ? " is-open" : ""}`}
        aria-expanded={open}
        aria-label={`Explain: ${label}`}
        title={`Explain: ${label}`}
        onClick={() => setOpen((o) => !o)}
      >
        ?
      </button>
      {open && <div className="info__body">{children}</div>}
    </>
  );
}

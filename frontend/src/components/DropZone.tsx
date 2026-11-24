import { useRef, useState, type DragEvent, type KeyboardEvent } from "react";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";

export function DropZone({ onFiles }: { onFiles: (files: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) onFiles(files);
  }

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.current?.click();
    }
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label="Déposer des fichiers : PDF, JPG ou PNG, plusieurs à la fois"
      onClick={() => input.current?.click()}
      onKeyDown={onKeyDown}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      className={`cursor-pointer rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
        over ? "border-blue-600 bg-blue-50" : "border-slate-300 bg-white hover:border-slate-400"
      }`}
    >
      <p className="font-medium">Glissez vos fichiers ici, ou cliquez pour les choisir</p>
      <p className="mt-1 text-sm text-slate-600">PDF, JPG ou PNG · plusieurs fichiers à la fois · 50 Mo maximum par fichier</p>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
    </div>
  );
}

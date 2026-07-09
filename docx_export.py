"""Save an edited transcript as .docx (when Microsoft Word is available on
the machine) or plain .txt otherwise."""

import os

from transcriber import unique_path


def word_available():
    """True if Microsoft Word appears to be installed (Windows registry)."""
    try:
        import winreg
    except ImportError:
        return False
    for root in (winreg.HKEY_CLASSES_ROOT, winreg.HKEY_LOCAL_MACHINE):
        subkey = "Word.Application" if root == winreg.HKEY_CLASSES_ROOT \
            else r"SOFTWARE\Microsoft\Office\Word"
        try:
            with winreg.OpenKey(root, subkey):
                return True
        except OSError:
            continue
    return False


def read_transcript(path):
    """Read a transcript back into editable text, handling .docx and .txt."""
    if path.lower().endswith(".docx"):
        from docx import Document

        doc = Document(path)
        return "\n\n".join(p.text for p in doc.paragraphs)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _paragraphs(text):
    text = text.replace("\r\n", "\n").strip("\n")
    if not text.strip():
        return []
    # A blank line separates paragraphs; if there are none, treat each
    # non-empty line as its own paragraph (e.g. timestamped output).
    if "\n\n" in text:
        blocks = text.split("\n\n")
    else:
        blocks = text.split("\n")
    return [b.strip() for b in blocks if b.strip()]


def save_transcript(text, dest_folder, stem):
    """Write `text` into `dest_folder` as `stem.docx` or `stem.txt`.

    Returns (path, kind) where kind is "docx" or "txt".
    """
    os.makedirs(dest_folder, exist_ok=True)

    if word_available():
        try:
            from docx import Document

            doc = Document()
            paras = _paragraphs(text)
            if paras:
                for p in paras:
                    doc.add_paragraph(p)
            else:
                doc.add_paragraph("")
            path = unique_path(os.path.join(dest_folder, stem + ".docx"))
            doc.save(path)
            return path, "docx"
        except Exception:
            pass  # fall through to txt so the user never loses their edit

    path = unique_path(os.path.join(dest_folder, stem + ".txt"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path, "txt"

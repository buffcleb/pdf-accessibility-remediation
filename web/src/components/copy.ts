/** Text onto the clipboard: the Clipboard API, or where a browser refuses it, a hidden text area's copy. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const back = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      area.remove();
      back?.focus();  // the button pressed keeps the keyboard's place
    }
  }
}

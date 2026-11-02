/**
 * Trigger a browser download for an in-memory blob.
 *
 * Shared by the report toolbar exporters. We deliberately do NOT use
 * `window.open(url)` for authenticated endpoints: the backend only accepts
 * `Authorization: Bearer …` (no cookie channel), so a plain navigation would
 * land on a 401 JSON page instead of the file.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  // Safari needs the node attached to the document for click() to navigate.
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

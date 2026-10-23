/* The authorised app endpoint supplies translated text and aggregate part counts only. */
document.querySelectorAll('[data-transcription-progress]').forEach(node => {
  const bar = node.querySelector('[data-progress-bar]');
  const text = node.querySelector('[data-progress-text]');
  const parts = node.querySelector('[data-progress-parts]');
  const estimate = node.querySelector('[data-progress-estimate]');
  async function poll() {
    if (!node.isConnected) return;
    try {
      const response = await fetch(node.dataset.transcriptionProgress, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok || !response.headers.get('Content-Type')?.includes('application/json')) throw new Error();
      const data = await response.json();
      if (text.textContent !== data.text) text.textContent = data.text;
      bar.max = Math.max(1, data.total);
      bar.value = data.done;
      if (parts.textContent !== data.parts_text) parts.textContent = data.parts_text;
      if (estimate.textContent !== data.eta_text) estimate.textContent = data.eta_text;
      if (!data.active) {
        node.querySelector('form').hidden = true;
        node.querySelector('[data-progress-finished]').hidden = false;
        return;
      }
    } catch (_) {
      if (text.textContent !== node.dataset.error) text.textContent = node.dataset.error;
    }
    setTimeout(poll, document.hidden ? 15000 : 5000);
  }
  setTimeout(poll, 1000);
});

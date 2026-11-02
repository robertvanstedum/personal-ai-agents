/* Deliberately small Markdown vocabulary. Never parse agent output as HTML. */
(() => {
  function render(node, text) {
    node.textContent = '';
    const parts = String(text || '').split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g);
    for (const part of parts) {
      if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
        const code = document.createElement('code'); code.textContent = part.slice(1, -1); node.append(code);
      } else if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
        const strong = document.createElement('strong'); strong.textContent = part.slice(2, -2); node.append(strong);
      } else node.append(document.createTextNode(part));
    }
  }
  window.MiniMoiMessageText = { render };
})();

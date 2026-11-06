/* Deliberately small Markdown vocabulary. Never parse agent output as HTML.
   One backtick run on a single line is inline code, including a run inside bold,
   so a model word such as **`name`** or ```name``` does not keep literal ticks. */
(() => {
  function pieces(text) {
    const out = [];
    let i = 0;
    while (i < text.length) {
      if (text.startsWith('**', i)) {
        const close = text.indexOf('**', i + 2);
        const line = text.indexOf('\n', i + 2);
        if (close > i + 2 && (line === -1 || close < line)) {
          const strong = document.createElement('strong');
          strong.append(...pieces(text.slice(i + 2, close)));
          out.push(strong);
          i = close + 2;
          continue;
        }
      }
      if (text[i] === '`') {
        let marks = 0;
        while (text[i + marks] === '`') marks += 1;
        const fence = '`'.repeat(marks);
        const rest = text.slice(i + marks);
        const lineEnd = rest.indexOf('\n');
        const scope = lineEnd === -1 ? rest : rest.slice(0, lineEnd);
        const close = scope.indexOf(fence);
        if (close > 0) {
          let inner = scope.slice(0, close);
          if (inner.startsWith(' ') && inner.endsWith(' ') && inner.trim()) inner = inner.slice(1, -1);
          const code = document.createElement('code');
          code.textContent = inner;
          out.push(code);
          i += marks + close + marks;
          continue;
        }
      }
      let j = i + 1;
      while (j < text.length && text[j] !== '`' && !text.startsWith('**', j)) j += 1;
      out.push(document.createTextNode(text.slice(i, j)));
      i = j;
    }
    return out;
  }

  function render(node, text) {
    node.textContent = '';
    node.append(...pieces(String(text || '')));
  }

  window.MiniMoiMessageText = { render };
})();

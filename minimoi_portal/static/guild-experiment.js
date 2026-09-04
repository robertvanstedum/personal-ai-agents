/* Prototype Lab tables: click a header to sort, type in the filter row to
   filter. Plain JavaScript, no dependencies, no network. With JavaScript off
   the tables render as the full, unsorted, unfiltered list. */
(function () {
  'use strict';

  var ASC = '▲';
  var DESC = '▼';

  function cellText(cell) {
    return (cell ? cell.textContent : '').replace(/\s+/g, ' ').trim();
  }

  function sortKey(cell, kind) {
    var value = cellText(cell);
    if (kind === 'date') {
      var parsed = Date.parse(value.slice(0, 10));
      return isNaN(parsed) ? -Infinity : parsed;
    }
    return value.toLowerCase();
  }

  function setup(table) {
    var head = table.tHead;
    var body = table.tBodies[0];
    if (!head || !body || !head.rows.length) { return; }

    var headers = Array.prototype.slice.call(head.rows[0].cells);
    var filterRow = head.querySelector('.xp-filter-row');
    var inputs = filterRow
      ? Array.prototype.slice.call(filterRow.querySelectorAll('.xp-filter-input'))
      : [];
    var sortIndex = -1;
    var direction = 1;

    function bodyRows() {
      return Array.prototype.slice.call(body.rows);
    }

    function applyFilters() {
      bodyRows().forEach(function (row) {
        var hidden = inputs.some(function (input) {
          var needle = input.value.trim().toLowerCase();
          if (!needle) { return false; }
          var column = parseInt(input.getAttribute('data-column'), 10);
          return cellText(row.cells[column]).toLowerCase().indexOf(needle) === -1;
        });
        row.style.display = hidden ? 'none' : '';
      });
    }

    function markIndicators() {
      headers.forEach(function (header, index) {
        var mark = header.querySelector('.xp-sort');
        if (!mark) { return; }
        mark.textContent = index === sortIndex ? (direction === 1 ? ASC : DESC) : '';
      });
    }

    function sortBy(index) {
      var kind = headers[index].getAttribute('data-sort') || 'text';
      direction = sortIndex === index ? -direction : 1;
      sortIndex = index;
      bodyRows().sort(function (a, b) {
        var left = sortKey(a.cells[index], kind);
        var right = sortKey(b.cells[index], kind);
        if (left < right) { return -direction; }
        if (left > right) { return direction; }
        return 0;
      }).forEach(function (row) {
        body.appendChild(row);
      });
      markIndicators();
    }

    headers.forEach(function (header, index) {
      var mark = document.createElement('span');
      mark.className = 'xp-sort';
      header.appendChild(mark);
      header.setAttribute('tabindex', '0');
      header.setAttribute('role', 'button');
      header.addEventListener('click', function () { sortBy(index); });
      header.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          sortBy(index);
        }
      });
    });

    inputs.forEach(function (input) {
      input.addEventListener('input', applyFilters);
    });

    var clear = head.querySelector('.xp-clear');
    if (clear) {
      clear.addEventListener('click', function (event) {
        event.preventDefault();
        inputs.forEach(function (input) { input.value = ''; });
        applyFilters();
      });
    }

    table.classList.add('xp-grid-ready');
  }

  Array.prototype.slice.call(document.querySelectorAll('table[data-grid]')).forEach(setup);
}());

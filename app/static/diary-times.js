// Progressive enhancement: the original text fields remain the server's format and the no-JavaScript editor.
(function () {
  "use strict";
  document.querySelectorAll('[data-time-editor]').forEach(function (box) {
    var windows = box.dataset.timeEditor === 'windows', limit = windows ? 6 : 12;
    var source = box.querySelector('[data-time-source]'), rows = box.querySelector('[data-time-rows]');
    var add = box.querySelector('[data-time-add]'), bulk = box.querySelector('[data-time-bulk]');
    var preview = box.querySelector('[data-time-preview]'), module = box.closest('.diary-module');
    var syncing = false;
    function parse() {
      var parts = source.value.trim().split(windows ? /\s*[,;]\s*/ : /[,;\s]+/).filter(Boolean);
      if (!parts.length && !windows) return [];
      var result = parts.map(function (part) {
        var match = part.match(windows ? /^(\d{1,2}:[0-5]\d)\s*[-–]\s*(\d{1,2}:[0-5]\d)$/ : /^(\d{1,2}:[0-5]\d)$/);
        if (!match) return null;
        var times = match.slice(1).map(function (t, i) {
          var bits = t.split(':'), h = Number(bits[0]);
          if (h > 23 && !(windows && i === 1 && t === '24:00')) return null;
          return h === 24 ? '00:00' : String(h).padStart(2, '0') + ':' + bits[1];
        });
        return times.some(function (t) { return t === null; }) || (windows && times[0] === times[1]) ? null : times;
      });
      return result.length && result.length <= limit && result.every(Boolean) ? result : null;
    }
    function minute(t) { var v = t.split(':'); return Number(v[0]) * 60 + Number(v[1]); }
    function describe(values) {
      var days = Array.from(module.querySelectorAll('.weekday-row input:checked')).map(function (input) { return input.parentElement.textContent.trim(); });
      var text = values.map(function (v) {
        return windows ? v[0] + '–' + v[1] + (v[1] <= v[0] ? de_en(' (über Mitternacht)', ' (overnight)') : '') : v[0];
      }).join(', ');
      preview.textContent = (days.length ? days.join(', ') : de_en('Alle Wochentage', 'All weekdays')) + ': ' + text;
      if (windows) {
        if (values.some(function (v) { return v[1] < v[0] && v[1] !== '00:00'; })) {
          preview.textContent += de_en('. An gewählten Wochentagen gelten sowohl die frühen als auch die späten Stunden.', '. On selected weekdays, both early and late hours apply.');
        }
        var intervals = values.flatMap(function (v) {
          var a = minute(v[0]), b = minute(v[1]);
          return b < a ? [[a, 1440], [0, b]] : [[a, b]];
        }).filter(function (v) { return v[0] < v[1]; }).sort(function (a, b) { return a[0] - b[0]; });
        var end = -1, overlap = false;
        intervals.forEach(function (v) { if (v[0] < end) overlap = true; end = Math.max(end, v[1]); });
        if (overlap) preview.textContent += de_en('. Zeitfenster überschneiden sich; gemeinsame Minuten zählen nur einmal.', '. Time windows overlap; shared minutes count only once.');
      } else if (new Set(values.map(function (v) { return v[0]; })).size < values.length) {
        preview.textContent += de_en('. Doppelte Uhrzeiten zählen nur einmal.', '. Duplicate times count only once.');
      }
    }
    function row(values) {
      var el = document.createElement('div'); el.className = 'diary-time-row';
      values.forEach(function (value, i) {
        var label = document.createElement('label');
        label.textContent = windows ? (i ? de_en('Bis', 'To') : de_en('Von', 'From')) : de_en('Uhrzeit', 'Time');
        var input = document.createElement('input'); input.type = 'time'; input.step = '60'; input.value = value;
        label.appendChild(input); el.appendChild(label);
      });
      var remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button button-small button-secondary';
      remove.textContent = de_en('Entfernen', 'Remove');
      remove.setAttribute('aria-label', windows ? de_en('Zeitfenster entfernen', 'Remove time window') : de_en('Uhrzeit entfernen', 'Remove time'));
      remove.addEventListener('click', function () {
        var next = el.nextElementSibling || el.previousElementSibling;
        el.remove(); update(); (next ? next.querySelector('input') : add).focus();
      });
      el.appendChild(remove); rows.appendChild(el);
    }
    function update() {
      var values = Array.from(rows.children).map(function (el) { return Array.from(el.querySelectorAll('input')).map(function (input) { return input.value; }); });
      var invalid = !values.length || values.some(function (v) { return v.some(function (t) { return !t; }) || (windows && v[0] === v[1]); });
      Array.from(rows.children).forEach(function (el) { el.querySelectorAll('input').forEach(function (input) { input.setCustomValidity(''); }); });
      source.setCustomValidity(invalid ? de_en('Bitte vollständige, unterschiedliche Anfangs- und Endzeiten eingeben.', 'Please enter complete, different start and end times.') : '');
      if (invalid) {
        preview.textContent = de_en('Noch nicht gespeichert: Vervollständigen Sie die Zeiten. Anfang und Ende dürfen nicht gleich sein.', 'Not saved yet: complete the times. Start and end must differ.');
      } else {
        source.value = values.map(function (v) { return v.join('-'); }).join(', ');
        describe(values);
        syncing = true; source.dispatchEvent(new Event('change', {bubbles: true})); syncing = false;
      }
      add.disabled = values.length >= limit;
    }
    function render(values) {
      rows.replaceChildren(); values.forEach(row); add.disabled = values.length >= limit; describe(values);
    }
    var initial = parse();
    if (!initial) return; // Never reinterpret invalid or unsupported saved text.
    render(initial); rows.hidden = add.hidden = preview.hidden = false; bulk.open = false;
    rows.addEventListener('input', function (event) { event.stopPropagation(); update(); });
    rows.addEventListener('change', function (event) { event.stopPropagation(); update(); });
    add.addEventListener('click', function () {
      if (rows.children.length >= limit) return;
      row(windows ? ['', ''] : ['']); update(); rows.lastElementChild.querySelector('input').focus();
    });
    source.addEventListener('input', function () {
      source.setCustomValidity(''); var values = parse();
      source.setCustomValidity(values ? '' : de_en('Prüfen Sie die Zeiten und die Anzahl der Einträge.', 'Check the times and the number of entries.'));
      if (values) render(values);
      else preview.textContent = de_en('Noch nicht gespeichert: Prüfen Sie die Texteingabe.', 'Not saved yet: check the text input.');
    });
    source.addEventListener('change', function () {
      if (syncing) return;
      source.dispatchEvent(new Event('input', {bubbles: true}));
    });
    source.addEventListener('invalid', function () { bulk.open = true; });
    module.querySelectorAll('.weekday-row input').forEach(function (input) {
      input.addEventListener('change', function () { var values = parse(); if (values && source.validity.valid) describe(values); });
    });
  });
})();


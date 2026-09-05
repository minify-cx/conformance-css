(() => {
  const filter = document.getElementById('status-filter');
  const rows = [...document.querySelectorAll('#cases tr[data-status]')];
  if (!filter) return;
  filter.addEventListener('change', () => {
    for (const row of rows) row.hidden = filter.value !== 'all' && row.dataset.status !== filter.value;
  });
})();

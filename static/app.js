const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const statusUrl = '/api/status';
const isAdmin = document.body.dataset.userRole === 'admin';
let currentStatus = null;
let currentView = { type: 'none' };
let lastSearchCount = 0;
const selectedSearchItems = new Set();
const selectedConferenceItems = new Set();
const selectedAuctionItems = new Set();
const tablePageSize = 50;
let auctionItems = [];
let auctionLots = [];
let auctionCurrentPage = 1;
const auctionPageSize = 10;
const multiFilterConfigs = {
  'search-sectors': {
    filter: '#search-sectors-filter',
    options: '#search-sectors-options',
    values: '#search-sectors-values',
    placeholder: '#search-sectors-placeholder',
  },
  'search-status': {
    filter: '#search-status-filter',
    options: '#search-status-options',
    values: '#search-status-values',
    placeholder: '#search-status-placeholder',
  },
};

const $ = (selector) => document.querySelector(selector);

async function api(url, options = {}) {
  const config = { ...options, headers: { ...(options.headers || {}) } };
  if (config.method && config.method !== 'GET') {
    config.headers['X-CSRF-Token'] = csrfToken;
  }
  if (config.body && !(config.body instanceof FormData)) {
    config.headers['Content-Type'] = 'application/json';
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(url, config);
  if (response.status === 401) {
    window.location.assign('/login');
    throw new Error('Sua sessão expirou. Entre novamente.');
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `Falha na solicitação (${response.status}).`);
    error.payload = payload;
    throw error;
  }
  return payload;
}

function notify(message, type = 'success') {
  const notice = document.createElement('div');
  notice.className = `notice ${type}`;
  notice.textContent = message;
  $('#notifications').append(notice);
  window.setTimeout(() => notice.remove(), 6000);
}

function setBusy(button, busy, busyText = 'Aguarde…') {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? busyText : button.dataset.label;
  button.classList.toggle('is-busy', busy);
  button.setAttribute('aria-busy', String(busy));
}

function setSelectOptions(select, values, placeholder = null) {
  const selected = new Set(Array.from(select.selectedOptions, option => option.value));
  select.replaceChildren();
  if (placeholder !== null) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = placeholder;
    select.append(option);
  }
  values.forEach(value => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    option.selected = selected.has(value);
    select.append(option);
  });
}

function updateStatusView(data) {
  currentStatus = data;
  const badge = $('#base-badge');
  const details = $('#base-details');
  const sources = $('#source-details');
  const photos = $('#photo-details');
  const error = $('#base-error');
  details.replaceChildren();
  sources.replaceChildren();
  photos.replaceChildren();
  error.hidden = true;

  if (data.estado === 'disponivel') {
    badge.className = 'badge badge-good';
    badge.textContent = 'Dados disponíveis';
    $('#base-summary').textContent = data.arquivo;
    [
      `${data.total} bens`,
      `${data.setores} setores disponíveis`,
      data.ultima_preparacao
        ? `Última preparação: ${data.ultima_preparacao}`
        : 'Preparação ainda não registrada',
    ].forEach(text => {
      const item = document.createElement('span');
      item.textContent = text;
      details.append(item);
    });
    const badges = Object.entries(data.status || {});
    badges.forEach(([name, total]) => {
      const item = document.createElement('span');
      item.textContent = `${name}: ${total}`;
      details.append(item);
    });
  } else {
    badge.className = data.estado === 'invalida' ? 'badge badge-bad' : 'badge badge-warning';
    badge.textContent = data.estado === 'invalida' ? 'Base inválida' : 'Aguardando dados';
    $('#base-summary').textContent = data.estado === 'invalida'
      ? 'A base existente precisa ser corrigida antes de continuar.'
      : 'Importe o cadastro patrimonial CSV para começar.';
    if (data.error) {
      error.textContent = data.error;
      error.hidden = false;
    }
  }

  const sourceLabels = [
    ['csv', 'CSV patrimonial'],
    ['alienados', 'Bens alienados (Excel)'],
    ['baixados', 'Bens baixados (Excel)'],
  ];
  sourceLabels.forEach(([key, label]) => {
    const item = document.createElement('span');
    item.textContent = `${label}: ${data.fontes?.[key] ? 'importado' : 'não importado'}`;
    sources.append(item);
  });
  if (data.fotos?.referenciadas) {
    const item = document.createElement('span');
    item.textContent =
      `Fotos: ${data.fotos.disponiveis} de ${data.fotos.referenciadas} referências disponíveis` +
      (data.fotos.pendentes ? ` · ${data.fotos.pendentes} aguardando importação` : '');
    photos.append(item);
  }

  const setores = data.lista_setores || [];
  setSelectOptions($('#sector-select'), setores, 'Selecione um setor');
  setSelectOptions($('#search-sectors'), setores);
  renderMultiFilterOptions('search-sectors');
  setSelectOptions($('#add-sector'), setores, 'Selecione um setor');
  setSelectOptions($('#auction-sector'), setores, 'Sem setor informado');
}

async function loadStatus() {
  try {
    updateStatusView(await api(statusUrl));
  } catch (error) {
    if (error.payload?.estado === 'invalida') {
      updateStatusView(error.payload);
      return;
    }
    $('#base-badge').className = 'badge badge-bad';
    $('#base-badge').textContent = 'Base inválida';
    $('#base-summary').textContent = 'Não foi possível ler a base web.';
    $('#base-error').textContent = error.message;
    $('#base-error').hidden = false;
  }
}

function selectedValues(select) {
  return Array.from(select.selectedOptions, option => option.value).filter(Boolean);
}

function renderMultiFilterSelection(selectId) {
  const config = multiFilterConfigs[selectId];
  const selected = selectedValues($(`#${selectId}`));
  const values = $(config.values);
  values.replaceChildren();
  selected.forEach(value => {
    const chip = document.createElement('span');
    chip.className = 'multi-filter-chip';
    chip.textContent = value;
    values.append(chip);
  });
  $(config.placeholder).hidden = selected.length > 0;
}

function renderMultiFilterOptions(selectId) {
  const config = multiFilterConfigs[selectId];
  const select = $(`#${selectId}`);
  const options = $(config.options);
  options.replaceChildren();
  Array.from(select.options).forEach(option => {
    const label = document.createElement('label');
    label.className = 'multi-filter-option';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = option.selected;
    checkbox.addEventListener('change', () => {
      option.selected = checkbox.checked;
      renderMultiFilterSelection(selectId);
      invalidateSearch();
      updateSearchFilterIndicators();
    });
    label.append(checkbox, document.createTextNode(option.textContent));
    options.append(label);
  });
  renderMultiFilterSelection(selectId);
}

function photoUrl(reference, thumbnail = false) {
  const name = String(reference).split(/[\\/]/).pop();
  const url = `/dados-web/fotos/${encodeURIComponent(name)}`;
  return thumbnail ? `${url}?miniatura=1` : url;
}

function makeTable(container, items) {
  container.replaceChildren();
  if (!items.length) {
    container.textContent = 'Nenhum item encontrado.';
    return;
  }
  const columns = [
    ['Código', 'Código'],
    ['Plaqueta', 'Plaqueta'],
    ['Descrição', 'Descrição'],
    ['Setor Atual', 'Setor'],
    ['STATUS', 'Status'],
    ['ESTADO', 'Estado'],
    ['Valor Atual', 'Valor atual (R$)'],
    ['Valor', 'Valor informado (R$)'],
    ['OBSERVACAO', 'Observação'],
    ['Data', 'Data'],
    ['Duplicidade Plaqueta', 'Duplicidade'],
    ['Possivel Duplicidade Plaqueta', 'Possível duplicidade'],
    ['ALTERADO_EM', 'Alterado em'],
  ];
  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const heading = document.createElement('tr');
  columns.forEach(([, label]) => {
    const th = document.createElement('th');
    th.textContent = label;
    heading.append(th);
  });
  const photoHeading = document.createElement('th');
  photoHeading.textContent = 'Foto';
  heading.append(photoHeading);
  const actionHeading = document.createElement('th');
  actionHeading.textContent = 'Ação';
  heading.append(actionHeading);
  const selectionEnabled = isAdmin
    && (currentView.type === 'search' || currentView.type === 'sector');
  const selectedItems = currentView.type === 'sector'
    ? selectedConferenceItems
    : selectedSearchItems;
  if (selectionEnabled) {
    const selectHeading = document.createElement('th');
    const selectPage = document.createElement('input');
    selectPage.type = 'checkbox';
    selectPage.setAttribute('aria-label', 'Selecionar todos os itens desta página');
    selectPage.checked = items.length > 0 && items.every(item => selectedItems.has(item.id));
    selectPage.addEventListener('change', () => {
      items.forEach(item => selectPage.checked
        ? selectedItems.add(item.id)
        : selectedItems.delete(item.id));
      updateBulkDeleteButton();
      makeTable(container, items);
    });
    selectHeading.append(selectPage);
    heading.append(selectHeading);
  }
  thead.append(heading);
  table.append(thead);

  const tbody = document.createElement('tbody');
  items.forEach(item => {
    const row = document.createElement('tr');
    columns.forEach(([key, label]) => {
      const cell = document.createElement('td');
      const value = item[key] ?? '';
      if (key === 'STATUS' || key === 'ESTADO') {
        const badge = document.createElement('span');
        const normalized = String(value).trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-');
        const typeClass = key === 'STATUS' ? 'table-status' : 'table-state';
        const valueClass = key === 'STATUS' ? `status-${normalized}` : `state-${normalized}`;
        badge.className = `table-highlight ${typeClass} ${valueClass}`;
        badge.textContent = String(value || '—');
        cell.append(badge);
      } else if (key === 'OBSERVACAO') {
        const observation = document.createElement('span');
        const text = String(value).trim();
        observation.className = `table-observation${text ? '' : ' is-empty'}`;
        observation.textContent = text || 'Sem observação';
        cell.append(observation);
      } else if (key === 'Valor Atual' || key === 'Valor') {
        cell.textContent = formatTableCurrency(value);
      } else {
        cell.textContent = key === 'Data' || key === 'ALTERADO_EM'
          ? formatTableDate(value, key === 'ALTERADO_EM')
          : value;
      }
      cell.dataset.field = key;
      cell.dataset.label = label;
      row.append(cell);
    });
    const photoCell = document.createElement('td');
    photoCell.dataset.label = 'Foto';
    if (item.Foto) {
      const link = document.createElement('a');
      link.className = 'table-photo-link';
      link.href = photoUrl(item.Foto);
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.setAttribute('aria-label', `Abrir foto de ${item['Plaqueta'] || item['Descrição']}`);
      const image = document.createElement('img');
      image.className = 'table-photo';
      image.src = photoUrl(item.Foto, true);
      image.alt = `Foto de ${item['Plaqueta'] || item['Descrição']}`;
      image.loading = 'lazy';
      image.onerror = () => {
        link.replaceChildren(document.createTextNode('Foto indisponível'));
      };
      link.append(image);
      photoCell.append(link);
    } else {
      photoCell.textContent = 'Sem foto';
    }
    row.append(photoCell);
    const actionCell = document.createElement('td');
    actionCell.dataset.label = 'Ação';
    actionCell.className = 'table-actions-cell';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'button button-secondary table-action';
    button.textContent = 'Editar';
    button.addEventListener('click', () => openEdit(item));
    actionCell.append(button);
    row.append(actionCell);
    if (selectionEnabled) {
      const selectionCell = document.createElement('td');
      selectionCell.dataset.label = 'Selecionar';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = selectedItems.has(item.id);
      checkbox.setAttribute('aria-label', `Selecionar ${item['Descrição'] || 'item'}`);
      checkbox.addEventListener('change', () => {
        checkbox.checked ? selectedItems.add(item.id) : selectedItems.delete(item.id);
        updateBulkDeleteButton();
        const pageSelect = container.querySelector('thead input[type="checkbox"]');
        if (pageSelect) pageSelect.checked = items.length > 0 && items.every(rowItem => selectedItems.has(rowItem.id));
      });
      selectionCell.append(checkbox);
      row.append(selectionCell);
    }
    tbody.append(row);
  });
  table.append(tbody);
  container.append(table);
}

function formatTableDate(value, includeTime = false) {
  const text = String(value ?? '').trim();
  if (!text) return '';

  const iso = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$/);
  if (iso) {
    const date = `${iso[3]}/${iso[2]}/${iso[1]}`;
    return includeTime && iso[4] ? `${date} ${iso[4]}:${iso[5]}` : date;
  }

  const brazilian = text.match(/^(\d{2})\/(\d{2})\/(\d{4})(?:\s+(\d{2}):(\d{2})(?::\d{2})?)?$/);
  if (brazilian) {
    const date = `${brazilian[1]}/${brazilian[2]}/${brazilian[3]}`;
    return includeTime && brazilian[4] ? `${date} ${brazilian[4]}:${brazilian[5]}` : date;
  }

  if (typeof value === 'number' && Number.isFinite(value) && value >= 1 && value <= 100000) {
    const date = new Date(Date.UTC(1899, 11, 30) + value * 86400000);
    const formatted = `${String(date.getUTCDate()).padStart(2, '0')}/${String(date.getUTCMonth() + 1).padStart(2, '0')}/${date.getUTCFullYear()}`;
    if (includeTime && value % 1) {
      const minutes = Math.round((value % 1) * 1440);
      return `${formatted} ${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
    }
    return formatted;
  }

  return text;
}

function formatTableCurrency(value) {
  const text = String(value ?? '').trim();
  if (!text) return '—';

  let normalized = text.replace(/R\$\s*/gi, '').replace(/\s/g, '');
  if (normalized.includes(',') && normalized.includes('.')) {
    normalized = normalized.lastIndexOf(',') > normalized.lastIndexOf('.')
      ? normalized.replace(/\./g, '').replace(',', '.')
      : normalized.replace(/,/g, '');
  } else if (normalized.includes(',')) {
    normalized = normalized.replace(',', '.');
  } else if (/^\-?\d{1,3}(?:\.\d{3})+$/.test(normalized)) {
    normalized = normalized.replace(/\./g, '');
  }
  const amount = Number(normalized);
  if (!Number.isFinite(amount)) return text;
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'BRL',
  }).format(amount);
}

async function refreshCurrentView() {
  if (currentView.type === 'sector') {
    await startConference(currentView.page || 1);
  } else if (currentView.type === 'search') {
    await runSearch(null, currentView.page || 1);
  }
  await loadStatus();
}

async function startConference(page = 1) {
  const setor = $('#sector-select').value;
  if (!setor) {
    notify('Selecione um setor para iniciar a conferência.', 'error');
    return;
  }
  const button = $('#start-sector');
  setBusy(button, true, 'Carregando…');
  try {
    const query = new URLSearchParams({
      setor,
      pagina: String(page),
      por_pagina: String(tablePageSize),
    });
    const data = await api(`/api/conferencia?${query.toString()}`);
    currentView = { type: 'sector', setor, page: data.pagina };
    const primeiro = data.total ? (data.pagina - 1) * data.por_pagina + 1 : 0;
    const ultimo = Math.min(data.pagina * data.por_pagina, data.total);
    $('#conference-summary').textContent =
      `${primeiro}–${ultimo} de ${data.total} item(ns) no setor ${data.setor}.`;
    makeTable($('#conference-table'), data.itens);
    renderPagination(
      $('#conference-pagination'),
      data.pagina,
      data.por_pagina,
      data.total,
      nextPage => startConference(nextPage),
    );
    $('#add-item-button').hidden = false;
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

async function runSearch(event, page = 1) {
  if (event) event.preventDefault();
  const button = $('#search-form').querySelector('button[type="submit"]');
  setBusy(button, true, 'Buscando…');
  const filters = collectSearchFilters();
  const query = new URLSearchParams();
  if (filters.termo) query.set('termo', filters.termo);
  filters.setores.forEach(value => query.append('setor', value));
  filters.status.forEach(value => query.append('status', value));
  filters.foto.forEach(value => query.append('foto', value));
  if (filters.plaqueta) query.set('plaqueta', filters.plaqueta);
  if (filters.data_inicial) query.set('data_inicial', filters.data_inicial);
  if (filters.data_final) query.set('data_final', filters.data_final);
  query.set('pagina', String(page));
  query.set('por_pagina', String(tablePageSize));
  try {
    const data = await api(`/api/busca?${query.toString()}`);
    currentView = { type: 'search', page: data.pagina };
    lastSearchCount = data.total;
    const primeiro = data.total ? (data.pagina - 1) * data.por_pagina + 1 : 0;
    const ultimo = Math.min(data.pagina * data.por_pagina, data.total);
    $('#search-summary').textContent =
      `${primeiro}–${ultimo} de ${data.total} resultado(s).`;
    makeTable($('#search-table'), data.itens);
    renderPagination(
      $('#search-pagination'),
      data.pagina,
      data.por_pagina,
      data.total,
      nextPage => runSearch(null, nextPage),
    );
    $('#search-excel-button').disabled = data.total === 0;
    $('#search-pdf-button').disabled = data.total === 0;
    $('#search-report-result').replaceChildren();
    updateBulkDeleteButton();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

function updateBulkDeleteButton() {
  [
    ['#delete-selected-search-items', selectedSearchItems.size],
    ['#delete-selected-conference-items', selectedConferenceItems.size],
  ].forEach(([selector, count]) => {
    const button = $(selector);
    if (!button) return;
    button.disabled = count === 0;
    button.textContent = count
      ? `Excluir da planilha principal (${count})`
      : 'Excluir da planilha principal';
  });
}

async function deleteSelectedItems(viewType) {
  const selectedItems = viewType === 'sector' ? selectedConferenceItems : selectedSearchItems;
  const ids = Array.from(selectedItems);
  if (!ids.length) return;
  const contexto = viewType === 'sector' ? ' deste setor' : '';
  const avisoExclusao = [
    `ATENÇÃO: você está prestes a excluir ${ids.length} item(ns)${contexto} da PLANILHA PRINCIPAL / base patrimonial.`,
    'Essa alteração pode causar danos graves e perda de dados no sistema. Não use esta ação para apenas remover itens de uma lista ou resultado de busca.',
    'Confirme somente se tiver certeza de que deseja excluir esses registros da planilha principal.',
  ].join('\n\n');
  if (!window.confirm(avisoExclusao)) return;
  const button = viewType === 'sector'
    ? $('#delete-selected-conference-items')
    : $('#delete-selected-search-items');
  setBusy(button, true, 'Excluindo…');
  try {
    const result = await api('/api/itens/excluir-lote', { method: 'POST', body: { ids } });
    selectedItems.clear();
    notify(result.mensagem);
    updateBulkDeleteButton();
    await refreshCurrentView();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
    updateBulkDeleteButton();
  }
}

function renderPagination(container, page, pageSize, total, onPage) {
  container.replaceChildren();
  const pageCount = Math.ceil(total / pageSize);
  if (pageCount < 2) return;

  const previous = document.createElement('button');
  previous.type = 'button';
  previous.className = 'button button-muted';
  previous.textContent = 'Anterior';
  previous.disabled = page <= 1;
  previous.addEventListener('click', () => onPage(page - 1));

  const indicator = document.createElement('span');
  indicator.textContent = `Página ${page} de ${pageCount}`;

  const next = document.createElement('button');
  next.type = 'button';
  next.className = 'button button-muted';
  next.textContent = 'Próxima';
  next.disabled = page >= pageCount;
  next.addEventListener('click', () => onPage(page + 1));

  container.append(previous, indicator, next);
}

function collectSearchFilters() {
  const foto = $('#search-photo').value ? [$('#search-photo').value] : [];
  return {
    termo: $('#search-term').value.trim(),
    setores: selectedValues($('#search-sectors')),
    status: selectedValues($('#search-status')),
    foto,
    plaqueta: $('#search-plaqueta').value,
    data_inicial: $('#date-from').value,
    data_final: $('#date-to').value,
  };
}

function updateSearchFilterIndicators() {
  const filters = collectSearchFilters();
  const indicators = [
    ['#search-term-active', 'Texto', filters.termo ? [filters.termo] : []],
    ['#search-plaqueta-active', 'Plaqueta', filters.plaqueta ? [filters.plaqueta] : []],
    ['#search-photo-active', 'Foto', filters.foto],
    ['#date-from-active', 'Data inicial', filters.data_inicial ? [formatTableDate(filters.data_inicial)] : []],
    ['#date-to-active', 'Data final', filters.data_final ? [formatTableDate(filters.data_final)] : []],
  ];
  indicators.forEach(([selector, label, values]) => {
    const container = $(selector);
    container.replaceChildren();
    values.forEach(value => {
      const item = document.createElement('span');
      item.className = 'filter-selection';
      item.textContent = `${label}: ${value}`;
      container.append(item);
    });
  });
}

function clearSearchFilters() {
  $('#search-form').reset();
  $('#search-sectors').selectedIndex = -1;
  $('#search-status').selectedIndex = -1;
  renderMultiFilterOptions('search-sectors');
  renderMultiFilterOptions('search-status');
  Object.values(multiFilterConfigs).forEach(({ filter }) => {
    $(filter).open = false;
  });
  selectedSearchItems.clear();
  currentView = { type: 'none' };
  $('#search-summary').textContent = '';
  $('#search-table').replaceChildren();
  $('#search-pagination').replaceChildren();
  $('#search-report-result').replaceChildren();
  updateSearchFilterIndicators();
  invalidateSearch();
  updateBulkDeleteButton();
}

async function createSearchReport(formato, button) {
  if (!lastSearchCount) {
    notify('Faça uma pesquisa com resultados antes de gerar o relatório.', 'error');
    return;
  }
  setBusy(button, true, 'Gerando…');
  try {
    const result = await api(`/api/relatorios/busca/${formato}`, {
      method: 'POST',
      body: collectSearchFilters(),
    });
    const link = document.createElement('a');
    link.href = `/api/relatorios/${encodeURIComponent(result.arquivo)}`;
    link.download = result.arquivo;
    link.textContent = `Baixar ${result.arquivo}`;
    const info = document.createElement('span');
    info.textContent = `${result.total} item(ns) exportado(s). `;
    $('#search-report-result').replaceChildren(info, link);
    notify(`Relatório ${formato.toUpperCase()} da pesquisa gerado.`);
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
    button.disabled = lastSearchCount === 0;
  }
}

function stateFromStatus(status, select) {
  if (status === 'N_LOC' || status === 'BAIXA') {
    select.value = 'NAO_APLICAVEL';
    select.disabled = true;
  } else {
    if (select.value === 'NAO_APLICAVEL') select.value = 'EM USO';
    select.disabled = false;
  }
}

function openEdit(item) {
  $('#edit-index').value = item.id;
  $('#edit-identification').textContent =
    `${item['Plaqueta'] || 'Sem plaqueta'} · ${item['Descrição'] || ''}`;
  $('#edit-status').value = item.STATUS || 'PEND';
  $('#edit-state').value = item.ESTADO || 'EM USO';
  $('#edit-observation').value = item.OBSERVACAO || '';
  $('#edit-value').value = item.Valor || '';
  $('#edit-photo').value = '';
  const identifier = String(item['Identificador Provisório'] || '');
  const canEditDescription = isAdmin && identifier.startsWith('SEM-ID-')
    && !identifier.startsWith('SEM-ID-ODS-');
  $('#description-field').hidden = !canEditDescription;
  $('#edit-description').value = item['Descrição'] || '';
  $('#edit-description').disabled = !canEditDescription;
  stateFromStatus($('#edit-status').value, $('#edit-state'));

  const photo = $('#photo-current');
  photo.replaceChildren();
  if (item.Foto) {
    const image = document.createElement('img');
    image.src = photoUrl(item.Foto);
    image.alt = 'Foto anexada ao bem';
    image.onerror = () => { image.replaceWith(document.createTextNode('Arquivo de foto não encontrado.')); };
    photo.append(image);
    if (isAdmin) {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'button button-muted table-action';
      remove.textContent = 'Remover foto';
      remove.addEventListener('click', () => savePhoto(true));
      photo.append(remove);
    }
  } else {
    photo.textContent = 'Nenhuma foto anexada.';
  }
  $('#edit-dialog').showModal();
}

async function savePhoto(remove = false) {
  const index = $('#edit-index').value;
  const form = new FormData();
  if (remove) {
    form.append('remover', 'sim');
  } else {
    const file = $('#edit-photo').files[0];
    if (!file) {
      notify('Selecione uma imagem para anexar.', 'error');
      return;
    }
    form.append('arquivo', file);
  }
  form.append('status', $('#edit-status').value);
  form.append('estado', $('#edit-state').value);
  form.append('observacao', $('#edit-observation').value);
  if (isAdmin) form.append('valor', $('#edit-value').value);
  if (isAdmin && !$('#description-field').hidden) {
    form.append('descricao', $('#edit-description').value);
  }
  try {
    const result = await api(`/api/itens/${index}/foto`, { method: 'POST', body: form });
    $('#edit-dialog').close();
    notify(remove ? 'Foto removida e conferência salva.' : result.mensagem);
    await refreshCurrentView();
  } catch (error) {
    notify(error.message, 'error');
  }
}

async function sendUpload(form, url, type = null, label = 'Importação concluída.') {
  const file = form.querySelector('input[type="file"]').files[0];
  if (!file) {
    notify('Selecione um arquivo.', 'error');
    return;
  }
  const data = new FormData();
  data.append('arquivo', file);
  if (type) data.append('tipo', type);
  const inputId = form.querySelector('input[type="file"]').id;
  const sourceKey = {
    'csv-file': 'csv',
    'alienados-file': 'alienados',
    'baixados-file': 'baixados',
  }[inputId];
  if (currentStatus?.fontes?.[sourceKey]) {
    const confirmed = window.confirm(
      'Já há um arquivo desta fonte. Deseja substituí-lo? O arquivo selecionado só será instalado se for válido.'
    );
    if (!confirmed) return;
    data.append('confirmar_substituicao', 'sim');
  }
  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true);
  try {
    const result = await api(url, { method: 'POST', body: data });
    form.reset();
    if (sourceKey === 'csv') {
      $('#csv-import-followup').hidden = false;
      notify(
        `CSV importado. A base de conferência ainda não foi atualizada. ` +
        `${result.registros} registros validados.`
      );
    } else {
      notify(`${label} ${result.registros !== undefined ? `${result.registros} registros validados.` : ''}`);
    }
    await loadStatus();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

async function importAuctionList(form) {
  const file = form.querySelector('input[type="file"]').files[0];
  if (!file) {
    notify('Selecione um arquivo Excel ou CSV.', 'error');
    return;
  }
  clearAuctionImportPreview(form);
  const body = new FormData();
  body.append('arquivo', file);
  body.append('lote_id', form.querySelector('.auction-target-lot').value);
  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Analisando plaquetas…');
  try {
    const result = await api('/api/leilao/importar/analisar', { method: 'POST', body });
    renderAuctionImportPreview(form, result.analise, result.token_analise);
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

function clearAuctionImportPreview(form) {
  const preview = form.querySelector('.auction-import-preview');
  preview.replaceChildren();
  preview.hidden = true;
}

function renderAuctionImportPreview(form, analise, token) {
  const preview = form.querySelector('.auction-import-preview');
  const { resumo, propostas } = analise;
  const mensagem = document.createElement('p');
  const detalhes = [
    `${resumo.total} item(ns) analisado(s).`,
    `${resumo.propostas} setor(es) sugerido(s) pela plaqueta.`,
    `${resumo.sem_plaqueta} item(ns) sem plaqueta permanecerão como estão.`,
  ];
  if (resumo.descricao_provisoria) {
    detalhes.push(
      `${resumo.descricao_provisoria} item(ns) sem descrição usarão a plaqueta como identificação provisória; descrições encontradas no cadastro serão salvas.`,
    );
  }
  if (resumo.descricoes_encontradas) {
    detalhes.push(
      `${resumo.descricoes_encontradas} descrição(ões) serão preenchidas pelo cadastro patrimonial.`,
    );
  }
  if (!analise.base_disponivel) {
    detalhes.push(
      `${resumo.sem_base} item(ns) com plaqueta não puderam ser analisados porque não há base patrimonial preparada.`,
    );
  } else {
    if (resumo.nao_encontrada) {
      detalhes.push(`${resumo.nao_encontrada} plaqueta(s) não encontrada(s); setor mantido.`);
    }
    if (resumo.ambiguas) {
      detalhes.push(`${resumo.ambiguas} plaqueta(s) duplicada(s) na base; setor mantido para conferência manual.`);
    }
    if (resumo.sem_setor) {
      detalhes.push(`${resumo.sem_setor} item(ns) encontrados sem setor cadastrado; setor mantido.`);
    }
    if (resumo.setor_igual) {
      detalhes.push(`${resumo.setor_igual} item(ns) já estão no setor cadastrado.`);
    }
  }
  mensagem.textContent = detalhes.join(' ');
  preview.append(mensagem);

  const lotesImportacao = analise.lotes_importacao || {};
  const gruposLote = lotesImportacao.grupos || [];
  const resumoLotes = document.createElement('p');
  if (lotesImportacao.coluna_detectada) {
    resumoLotes.textContent =
      `Coluna LOTE identificada. Distribuição prevista: ${gruposLote.map(grupo =>
        `${grupo.nome}: ${grupo.quantidade} item(ns)`
      ).join('; ')}.`;
  } else if (lotesImportacao.lote_formulario) {
    resumoLotes.textContent =
      `Coluna LOTE não identificada; todos os itens serão atribuídos ao lote “${lotesImportacao.lote_formulario}”.`;
  } else {
    resumoLotes.textContent =
      'Coluna LOTE não identificada e nenhum lote foi escolhido; todos os itens permanecerão sem lote.';
  }
  preview.append(resumoLotes);

  const descricoesResolvidas = analise.descricoes_resolvidas || [];
  if (descricoesResolvidas.length) {
    const avisoDescricoes = document.createElement('p');
    avisoDescricoes.textContent =
      'A descrição provisória será substituída quando a plaqueta corresponder a um único item do cadastro:';
    preview.append(avisoDescricoes);
    const listaDescricoes = document.createElement('ul');
    listaDescricoes.className = 'auction-import-preview-list';
    descricoesResolvidas.forEach(resolvida => {
      const linha = document.createElement('li');
      linha.textContent =
        `Plaqueta ${resolvida.plaqueta}: ${resolvida.descricao_cadastro}`;
      listaDescricoes.append(linha);
    });
    preview.append(listaDescricoes);
  }

  const lotesCriados = lotesImportacao.lotes_criados || [];
  if (lotesCriados.length) {
    const criacaoLotes = document.createElement('p');
    criacaoLotes.textContent =
      `Após a confirmação, serão criados ${lotesCriados.length} lote(s): ${lotesCriados.join(', ')}.`;
    preview.append(criacaoLotes);
  }

  const divergenciasLote = lotesImportacao.divergencias || [];
  if (divergenciasLote.length) {
    const quantidadeDivergente = divergenciasLote.reduce(
      (total, divergencia) => total + divergencia.quantidade,
      0,
    );
    const avisoLote = document.createElement('p');
    avisoLote.className = 'auction-import-warning';
    avisoLote.setAttribute('role', 'alert');
    avisoLote.textContent =
      `Aviso: ${quantidadeDivergente} item(ns) têm na coluna LOTE um valor diferente do lote escolhido no formulário (“${lotesImportacao.lote_formulario}”). A coluna prevalece nas linhas preenchidas; o lote do formulário será usado quando LOTE estiver vazio. Nada será gravado até você confirmar.`;
    preview.append(avisoLote);

    const listaDivergencias = document.createElement('ul');
    listaDivergencias.className = 'auction-import-lot-conflicts';
    divergenciasLote.forEach(divergencia => {
      const linha = document.createElement('li');
      const valores = divergencia.valores_planilha.length
        ? ` [valores: ${divergencia.valores_planilha.join(', ')}]`
        : '';
      linha.textContent =
        `${divergencia.quantidade} item(ns): ${divergencia.lote_planilha}${valores}`;
      listaDivergencias.append(linha);
    });
    preview.append(listaDivergencias);
  }

  const explicacao = document.createElement('p');
  explicacao.textContent =
    'Confira as sugestões. Somente os itens marcados terão o setor atualizado na lista de leilão; o cadastro patrimonial não será alterado.';
  preview.append(explicacao);

  const lista = document.createElement('div');
  lista.className = 'auction-import-preview-list';
  propostas.forEach(proposta => {
    const linha = document.createElement('label');
    linha.className = 'auction-import-preview-item';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = true;
    checkbox.dataset.proposalIndex = String(proposta.indice);
    checkbox.setAttribute(
      'aria-label',
      `Aprovar setor ${proposta.setor_sugerido} para ${proposta.descricao}`,
    );
    const texto = document.createElement('span');
    texto.textContent =
      `${proposta.descricao} · Plaqueta ${proposta.plaqueta} · ` +
      `${proposta.setor_atual || 'Sem setor'} → ${proposta.setor_sugerido}` +
      (proposta.descricao_cadastro
        ? ` · Cadastro: ${proposta.descricao_cadastro}`
        : '');
    linha.append(checkbox, texto);
    lista.append(linha);
  });
  if (propostas.length) {
    preview.append(lista);
  } else {
    const semSugestoes = document.createElement('p');
    semSugestoes.textContent =
      'Nenhum setor será alterado. Você ainda pode aprovar a importação dos itens como estão.';
    preview.append(semSugestoes);
  }

  const acoes = document.createElement('div');
  acoes.className = 'search-actions';
  const aprovar = createAuctionButton(
    '',
    () => confirmarImportacaoLeilao(form, token, analise, aprovar),
    'button button-primary',
  );
  const atualizarBotao = () => {
    const quantidade = lista.querySelectorAll('input[type="checkbox"]:checked').length;
    aprovar.textContent = quantidade
      ? `Aprovar ${quantidade} setor(es) e importar`
      : 'Aprovar análise e importar sem alterar setores';
  };
  lista.addEventListener('change', atualizarBotao);
  atualizarBotao();
  const cancelar = createAuctionButton(
    'Cancelar análise',
    () => clearAuctionImportPreview(form),
    'button button-muted',
  );
  acoes.append(aprovar, cancelar);
  preview.append(acoes);
  preview.hidden = false;
}

async function confirmarImportacaoLeilao(form, token, analise, button) {
  const file = form.querySelector('input[type="file"]').files[0];
  if (!file) {
    clearAuctionImportPreview(form);
    notify('Selecione novamente o arquivo para continuar.', 'error');
    return;
  }
  const preview = form.querySelector('.auction-import-preview');
  const indicesAprovados = Array.from(
    preview.querySelectorAll('input[data-proposal-index]:checked'),
    checkbox => Number(checkbox.dataset.proposalIndex),
  );
  const quantidade = indicesAprovados.length;
  let confirmacao = quantidade
    ? `Aprovar a atualização de setor para ${quantidade} item(ns) e importar o arquivo?`
    : 'Importar os itens sem alterar os setores?';
  const divergenciasLote = analise.lotes_importacao
    ? analise.lotes_importacao.divergencias || []
    : [];
  if (divergenciasLote.length) {
    confirmacao =
      `Atenção: a coluna LOTE diverge do lote escolhido no formulário em ` +
      `${divergenciasLote.reduce((total, divergencia) =>
        total + divergencia.quantidade, 0)} item(ns).\n\n${confirmacao}`;
  }
  if (!window.confirm(confirmacao)) return;

  const corpo = new FormData();
  corpo.append('arquivo', file);
  corpo.append('lote_id', form.querySelector('.auction-target-lot').value);
  corpo.append('token_analise', token);
  corpo.append('indices_aprovados', JSON.stringify(indicesAprovados));
  const botaoAnalise = form.querySelector('button[type="submit"]');
  botaoAnalise.disabled = true;
  setBusy(button, true, 'Importando…');
  try {
    const result = await api('/api/leilao/importar', { method: 'POST', body: corpo });
    form.reset();
    clearAuctionImportPreview(form);
    notify(`${result.mensagem} Total na lista: ${result.total}.`);
    await loadAuctionList();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
    botaoAnalise.disabled = false;
  }
}

async function loadAuctionList() {
  try {
    const data = await api('/api/leilao');
    const lotes = data.lotes || [];
    auctionItems = data.itens || [];
    auctionLots = lotes;
    $('#auction-summary').textContent =
      `${data.total} item(ns) · ${lotes.length} lote(s)`;
    updateAuctionLotSelects(lotes);
    updateAuctionListLotFilter(lotes);
    renderAuctionList(auctionItems, auctionLots);
  } catch (error) {
    notify(`Não foi possível carregar a lista de leilão: ${error.message}`, 'error');
  }
}

function updateAuctionLotSelects(lotes) {
  document.querySelectorAll('.auction-target-lot').forEach(select => {
    const selecionado = select.value;
    select.replaceChildren();
    const disponivel = document.createElement('option');
    disponivel.value = '';
    disponivel.textContent = 'Disponíveis, sem lote';
    select.append(disponivel);
    lotes.forEach(lote => {
      const option = document.createElement('option');
      option.value = lote.id;
      option.textContent = `Lote ${lote.numero} · ${lote.nome}`;
      select.append(option);
    });
    select.value = selecionado;
  });
}

function updateAuctionListLotFilter(lotes) {
  const select = $('#auction-list-lot');
  const selecionado = select.value;
  select.replaceChildren();
  [
    ['', 'Todos os lotes'],
    ['__sem_lote__', 'Disponíveis, sem lote'],
  ].forEach(([value, label]) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    select.append(option);
  });
  lotes.forEach(lote => {
    const option = document.createElement('option');
    option.value = lote.id;
    option.textContent = `Lote ${lote.numero} · ${lote.nome}`;
    select.append(option);
  });
  select.value = Array.from(select.options).some(option => option.value === selecionado)
    ? selecionado
    : '';
}

function createAuctionButton(text, handler, className = 'button button-muted') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className;
  button.textContent = text;
  button.addEventListener('click', handler);
  return button;
}

function renderAuctionList(items, lots) {
  const container = $('#auction-list');
  container.replaceChildren();
  const itemIds = new Set(items.map(item => item.id));
  selectedAuctionItems.forEach(id => {
    if (!itemIds.has(id)) selectedAuctionItems.delete(id);
  });
  const termo = normalizeAuctionFilter($('#auction-list-search').value);
  const loteSelecionado = $('#auction-list-lot').value;
  const ordemLotes = new Map(lots.map((lot, index) => [lot.id, index]));
  const filtrados = items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => {
      if (loteSelecionado === '__sem_lote__' && item.lote_id) return false;
      if (loteSelecionado && loteSelecionado !== '__sem_lote__'
        && item.lote_id !== loteSelecionado) return false;
      if (!termo) return true;
      const pesquisavel = normalizeAuctionFilter(
        `${item.descricao || ''} ${item.plaqueta_informada || ''}`,
      );
      return pesquisavel.includes(termo);
    })
    .sort((a, b) => (
      (ordemLotes.get(a.item.lote_id) ?? lots.length)
      - (ordemLotes.get(b.item.lote_id) ?? lots.length)
      || a.index - b.index
    ))
    .map(({ item }) => item);
  const totalFiltrado = filtrados.length;
  const totalPaginas = Math.max(1, Math.ceil(totalFiltrado / auctionPageSize));
  auctionCurrentPage = Math.min(Math.max(auctionCurrentPage, 1), totalPaginas);
  const primeiroIndice = (auctionCurrentPage - 1) * auctionPageSize;
  const itensDaPagina = filtrados.slice(
    primeiroIndice,
    primeiroIndice + auctionPageSize,
  );
  $('#auction-list-summary').textContent = totalFiltrado
    ? `Mostrando ${primeiroIndice + 1}–${primeiroIndice + itensDaPagina.length} de ${totalFiltrado} item(ns).`
    : 'Nenhum item corresponde aos filtros.';
  $('#auction-export-xlsx').disabled = totalFiltrado === 0;
  renderPagination(
    $('#auction-pagination'),
    auctionCurrentPage,
    auctionPageSize,
    totalFiltrado,
    page => {
      auctionCurrentPage = page;
      renderAuctionList(items, lots);
    },
  );

  if (!items.length && !lots.length) {
    container.textContent = 'Nenhum item adicionado à lista de leilão.';
    return;
  }
  if (termo || loteSelecionado) {
    if (!totalFiltrado) {
      container.textContent = 'Tente outro texto ou selecione um lote diferente.';
      return;
    }
  }
  if (isAdmin && itensDaPagina.length) {
    const bulkActions = document.createElement('div');
    bulkActions.className = 'search-actions';
    const selectAllButton = createAuctionButton(
      `Selecionar todos os resultados (${totalFiltrado})`,
      () => {
        selectedAuctionItems.clear();
        filtrados.forEach(item => selectedAuctionItems.add(item.id));
        renderAuctionList(items, lots);
      },
      'button button-secondary',
    );
    const deleteButton = createAuctionButton(
      'Excluir selecionados',
      deleteSelectedAuctionItems,
      'button button-muted',
    );
    deleteButton.id = 'delete-selected-auction-items';
    deleteButton.disabled = selectedAuctionItems.size === 0;
    const help = document.createElement('span');
    help.className = 'muted';
    help.textContent =
      'A seleção inclui todos os resultados filtrados, mesmo em outras páginas, e substitui a seleção anterior.';
    bulkActions.append(selectAllButton, deleteButton, help);
    container.append(bulkActions);
  }

  lots.forEach((lot, index) => {
    const lotItems = itensDaPagina.filter(item => item.lote_id === lot.id);
    if (!lotItems.length && (totalFiltrado || termo || loteSelecionado)) return;
    const group = document.createElement('section');
    group.className = 'auction-lot-group';
    const heading = document.createElement('div');
    heading.className = 'auction-lot-heading';
    const title = document.createElement('h3');
    title.textContent = `Lote ${lot.numero} · ${lot.nome}`;
    const lotValue = document.createElement('span');
    lotValue.className = 'muted';
    lotValue.textContent = lot.valor
      ? `Valor do lote: ${formatAuctionValue(lot.valor)} (ajuste manual)`
      : `Valor calculado do lote: ${formatAuctionValue(lot.valor_calculado)}`;
    title.append(document.createElement('br'), lotValue);
    const controls = document.createElement('div');
    controls.className = 'auction-row-actions admin-only';
    controls.append(
      createAuctionButton('Editar', () => renameAuctionLot(lot)),
      createAuctionButton('↑', () => reorderAuctionLot(lots, index, -1), 'button button-muted auction-order-button'),
      createAuctionButton('↓', () => reorderAuctionLot(lots, index, 1), 'button button-muted auction-order-button'),
    );
    controls.children[1].disabled = index === 0;
    controls.children[2].disabled = index === lots.length - 1;
    controls.append(createAuctionButton('Excluir lote', async () => {
      if (lotItems.length) {
        const quantidade = lotItems.length === 1
          ? '1 item'
          : `${lotItems.length} itens`;
        notify(
          `Este lote contém ${quantidade}. Mova ou remova os itens antes de excluir o lote.`,
          'error',
        );
        return;
      }
      if (!window.confirm(`Excluir o lote "${lot.nome}"?`)) return;
      try {
        const result = await api(`/api/leilao/lotes/${encodeURIComponent(lot.id)}/excluir`, {
          method: 'POST',
          body: {},
        });
        notify(result.mensagem);
        await loadAuctionList();
      } catch (error) {
        notify(error.message, 'error');
      }
    }));
    heading.append(title, controls);
    group.append(heading);
    if (lotItems.length) {
      appendAuctionItemsTable(group, lotItems, lots);
    } else {
      const empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = 'Este lote ainda não tem itens.';
      group.append(empty);
    }
    container.append(group);
  });

  const availableItems = itensDaPagina.filter(item => !item.lote_id);
  if (availableItems.length) {
    const group = document.createElement('section');
    group.className = 'auction-lot-group';
    const title = document.createElement('h3');
    title.textContent = `Itens disponíveis para organizar (${availableItems.length})`;
    group.append(title);
    appendAuctionItemsTable(group, availableItems, lots);
    container.append(group);
  }
}

function normalizeAuctionFilter(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim()
    .toLocaleLowerCase('pt-BR')
    .replace(/\s+/g, ' ');
}

async function exportAuctionList(button) {
  const params = new URLSearchParams({
    q: $('#auction-list-search').value.trim(),
    lote_id: $('#auction-list-lot').value,
  });
  setBusy(button, true, 'Gerando Excel…');
  try {
    const response = await fetch(`/api/leilao/exportar?${params.toString()}`);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || `Falha na solicitação (${response.status}).`);
    }
    const arquivo = await response.blob();
    const url = URL.createObjectURL(arquivo);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'itens_leilao.xlsx';
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify('Arquivo Excel exportado.');
  } catch (error) {
    notify(`Não foi possível exportar os itens: ${error.message}`, 'error');
  } finally {
    setBusy(button, false);
  }
}

function appendAuctionItemsTable(container, items, lots) {
  const table = document.createElement('table');
  const header = document.createElement('tr');
  ['Selecionar', 'Descrição', 'Plaqueta informada', 'Setor', 'Data', 'Valor', 'Observação', 'Situação', 'Organizar em lote', 'Vincular ao patrimônio', 'Ações'].forEach(text => {
    const cell = document.createElement('th');
    cell.textContent = text;
    if (!isAdmin && [
      'Selecionar',
      'Organizar em lote',
      'Vincular ao patrimônio',
      'Ações',
    ].includes(text)) {
      cell.className = 'admin-only';
    }
    header.append(cell);
  });
  const thead = document.createElement('thead');
  thead.append(header);
  table.append(thead);
  const body = document.createElement('tbody');

  items.forEach(item => {
    const row = document.createElement('tr');
    const selectionCell = document.createElement('td');
    selectionCell.className = 'admin-only';
    selectionCell.dataset.label = 'Selecionar';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = selectedAuctionItems.has(item.id);
    checkbox.setAttribute('aria-label', `Selecionar ${item.descricao || 'item'} para exclusão`);
    checkbox.addEventListener('change', () => {
      checkbox.checked
        ? selectedAuctionItems.add(item.id)
        : selectedAuctionItems.delete(item.id);
      updateAuctionBulkDeleteButton();
    });
    selectionCell.append(checkbox);
    row.append(selectionCell);
    [
      ['Descrição', item.descricao],
      ['Plaqueta informada', item.plaqueta_informada],
      ['Setor', item.setor],
      ['Data', item.data],
      ['Valor', formatAuctionValue(item.valor)],
      ['Observação', item.observacao],
    ].forEach(([label, value]) => {
      const cell = document.createElement('td');
      cell.dataset.label = label;
      cell.textContent = value || '—';
      row.append(cell);
    });
    const status = document.createElement('td');
    status.dataset.label = 'Situação';
    const vinculo = item.plaqueta_vinculada
      ? `Plaqueta ${item.plaqueta_vinculada}`
      : `Sem plaqueta · ${item.descricao_vinculada || 'bem patrimonial'}`;
    status.textContent = item.situacao === 'IDENTIFICADO'
      ? `Identificado · ${vinculo}`
      : 'Pendente de identificação';
    row.append(status);

    const organizeCell = document.createElement('td');
    organizeCell.className = 'admin-only';
    organizeCell.dataset.label = 'Organizar em lote';
    const organizeControls = document.createElement('div');
    organizeControls.className = 'auction-link-controls';
    const lotSelect = document.createElement('select');
    lotSelect.setAttribute('aria-label', `Lote para ${item.descricao}`);
    const unassigned = document.createElement('option');
    unassigned.value = '';
    unassigned.textContent = 'Disponíveis, sem lote';
    lotSelect.append(unassigned);
    lots.forEach(lot => {
      const option = document.createElement('option');
      option.value = lot.id;
      option.textContent = `Lote ${lot.numero} · ${lot.nome}`;
      lotSelect.append(option);
    });
    lotSelect.value = item.lote_id || '';
    const moveButton = createAuctionButton('Mover item', async () => {
      moveButton.disabled = true;
      try {
        const result = await api(`/api/leilao/${encodeURIComponent(item.id)}/lote`, {
          method: 'POST',
          body: { lote_id: lotSelect.value },
        });
        notify(result.mensagem);
        await loadAuctionList();
      } catch (error) {
        notify(error.message, 'error');
        moveButton.disabled = false;
      }
    }, 'button button-secondary');
    organizeControls.append(lotSelect, moveButton);
    organizeCell.append(organizeControls);
    row.append(organizeCell);

    const linkAction = document.createElement('td');
    linkAction.className = 'admin-only';
    linkAction.dataset.label = 'Vincular ao patrimônio';
    if (item.situacao !== 'IDENTIFICADO') {
      const field = document.createElement('div');
      field.className = 'auction-link-controls';
      const searchButton = createAuctionButton('Buscar possíveis correspondências', async () => {
        setBusy(searchButton, true, 'Buscando…');
        try {
          const result = await api(
            `/api/leilao/${encodeURIComponent(item.id)}/sugestoes`
          );
          field.replaceChildren();
          renderAuctionLinkControls(item, field, result.sugestoes);
        } catch (error) {
          notify(error.message, 'error');
          setBusy(searchButton, false);
        }
      }, 'button button-secondary');
      field.append(searchButton);
      linkAction.append(field);
    } else {
      linkAction.textContent = 'Vínculo confirmado manualmente.';
    }
    row.append(linkAction);

    const action = document.createElement('td');
    action.className = 'admin-only';
    action.dataset.label = 'Ações';
    const controls = document.createElement('div');
    controls.className = 'auction-row-actions';
    controls.append(createAuctionButton('Editar', () => openAuctionEdit(item)));
    if (item.lote_id) {
      controls.append(createAuctionButton('Remover do lote', async () => {
        try {
          const result = await api(`/api/leilao/${encodeURIComponent(item.id)}/lote`, {
            method: 'POST',
            body: { lote_id: null },
          });
          notify(result.mensagem);
          await loadAuctionList();
        } catch (error) {
          notify(error.message, 'error');
        }
      }));
    }
    controls.append(createAuctionButton('Excluir da lista', async () => {
      if (!window.confirm(
        `Excluir "${item.descricao}" da lista de leilão? O cadastro patrimonial não será alterado.`
      )) return;
      try {
        const result = await api(`/api/leilao/${encodeURIComponent(item.id)}/excluir`, {
          method: 'POST',
          body: {},
        });
        notify(result.mensagem);
        await loadAuctionList();
      } catch (error) {
        notify(error.message, 'error');
      }
    }));
    action.append(controls);
    row.append(action);
    body.append(row);
  });

  table.append(body);
  const scrollHint = document.createElement('p');
  scrollHint.className = 'auction-scroll-hint';
  scrollHint.textContent = 'Use a barra horizontal abaixo para ver todas as colunas.';
  const scrollArea = document.createElement('div');
  scrollArea.className = 'auction-table-scroll';
  scrollArea.tabIndex = 0;
  scrollArea.setAttribute('role', 'region');
  scrollArea.setAttribute(
    'aria-label',
    'Tabela de itens do leilão. Use a barra horizontal para ver todas as colunas.',
  );
  scrollArea.append(table);
  container.append(scrollHint, scrollArea);
}

function formatAuctionValue(value) {
  if (value === '' || value === null || value === undefined) return '—';
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'BRL',
  }).format(Number(value));
}

function updateAuctionBulkDeleteButton() {
  const button = $('#delete-selected-auction-items');
  if (!button) return;
  const count = selectedAuctionItems.size;
  button.disabled = count === 0;
  button.textContent = count
    ? `Excluir selecionados (${count})`
    : 'Excluir selecionados';
}

async function deleteSelectedAuctionItems() {
  const ids = Array.from(selectedAuctionItems);
  if (!ids.length) return;
  const quantidade = ids.length === 1
    ? '1 item selecionado'
    : `${ids.length} itens selecionados`;
  if (!window.confirm(
    `Excluir ${quantidade} da lista de leilão? O cadastro patrimonial não será alterado.`
  )) return;
  const button = $('#delete-selected-auction-items');
  setBusy(button, true, 'Excluindo…');
  try {
    const result = await api('/api/leilao/excluir-selecionados', {
      method: 'POST',
      body: { ids },
    });
    selectedAuctionItems.clear();
    notify(result.mensagem);
    await loadAuctionList();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
    updateAuctionBulkDeleteButton();
  }
}

async function renameAuctionLot(lot) {
  const nomeResposta = window.prompt('Nome do lote:', lot.nome);
  if (nomeResposta === null) return;
  const valorResposta = window.prompt(
    'Valor total do lote em R$:',
    lot.valor || '',
  );
  if (valorResposta === null) return;
  const nome = nomeResposta.trim();
  if (!nome) {
    notify('Informe um nome para o lote.', 'error');
    return;
  }
  try {
    const result = await api(`/api/leilao/lotes/${encodeURIComponent(lot.id)}/editar`, {
      method: 'POST',
      body: { nome, valor: valorResposta },
    });
    notify(result.mensagem);
    await loadAuctionList();
  } catch (error) {
    notify(error.message, 'error');
  }
}

async function reorderAuctionLot(lots, index, direction) {
  const ids = lots.map(lot => lot.id);
  const nextIndex = index + direction;
  [ids[index], ids[nextIndex]] = [ids[nextIndex], ids[index]];
  try {
    const result = await api('/api/leilao/lotes/reordenar', {
      method: 'POST',
      body: { ids },
    });
    notify(result.mensagem);
    await loadAuctionList();
  } catch (error) {
    notify(error.message, 'error');
  }
}

function openAuctionEdit(item) {
  const sectorSelect = $('#auction-edit-sector');
  const sectors = currentStatus?.lista_setores || [];
  setSelectOptions(sectorSelect, sectors, 'Sem setor informado');
  if (item.setor && !sectors.includes(item.setor)) {
    const option = document.createElement('option');
    option.value = item.setor;
    option.textContent = item.setor;
    sectorSelect.append(option);
  }
  $('#auction-edit-id').value = item.id;
  $('#auction-edit-description').value = item.descricao;
  $('#auction-edit-plaqueta').value = item.plaqueta_informada || '';
  $('#auction-edit-value').value = item.valor || '';
  sectorSelect.value = item.setor || '';
  $('#auction-edit-date').value = item.data || '';
  $('#auction-edit-observation').value = item.observacao || '';
  $('#auction-edit-dialog').showModal();
}

function renderAuctionLinkControls(item, container, suggestions) {
  const statusesDivergentes = new Set(['DIVERG', 'DIV', 'DIVERGENTE']);
  const contemDivergente = suggestions.some(candidate =>
    statusesDivergentes.has(String(candidate.status || '').toUpperCase())
  );
  if (contemDivergente) {
    const warning = document.createElement('p');
    warning.className = 'auction-link-warning';
    warning.setAttribute('role', 'alert');
    warning.textContent =
      'Atenção: há sugestões marcadas como DIVERG. Confira o setor antes de vincular; o bem pode estar cadastrado em um setor diferente do correto.';
    container.append(warning);
  }

  const select = document.createElement('select');
  select.setAttribute('aria-label', `Sugestões para ${item.descricao}`);
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = suggestions.length
    ? 'Selecione uma sugestão'
    : 'Sem sugestão; informe a plaqueta';
  select.append(placeholder);
  suggestions.forEach(candidate => {
    const option = document.createElement('option');
    option.value = String(candidate.id);
    const identificacao = candidate.plaqueta || `Sem plaqueta · ID ${candidate.id}`;
    const alertaSetor = statusesDivergentes.has(
      String(candidate.status || '').toUpperCase()
    )
      ? ' · DIVERG — confira o setor'
      : candidate.status
        ? ` · ${candidate.status}`
        : '';
    option.textContent =
      `${identificacao} · ${candidate.descricao} · ${candidate.setor || 'Sem setor'} · ${candidate.similaridade}%${alertaSetor}`;
    select.append(option);
  });

  const manualPlaqueta = document.createElement('input');
  manualPlaqueta.type = 'text';
  manualPlaqueta.maxLength = 100;
  manualPlaqueta.placeholder = 'Plaqueta confirmada (se não selecionar sugestão)';
  manualPlaqueta.setAttribute('aria-label', `Plaqueta confirmada para ${item.descricao}`);
  select.addEventListener('change', () => {
    const candidate = suggestions.find(suggestion =>
      String(suggestion.id) === select.value
    );
    manualPlaqueta.value = candidate ? candidate.plaqueta : '';
  });

  const linkButton = document.createElement('button');
  linkButton.type = 'button';
  linkButton.className = 'button button-secondary';
  linkButton.textContent = 'Confirmar vínculo';
  linkButton.addEventListener('click', async () => {
    const candidate = suggestions.find(suggestion =>
      String(suggestion.id) === select.value
    );
    const plaquetaDigitada = manualPlaqueta.value.trim();
    const usarIdentificador = candidate && (
      !plaquetaDigitada || plaquetaDigitada === candidate.plaqueta
    );
    const plaqueta = plaquetaDigitada || item.plaqueta_informada;
    if (!usarIdentificador && !plaqueta) {
      notify('Informe ou selecione uma plaqueta para confirmar o vínculo.', 'error');
      return;
    }
    const alvo = usarIdentificador
      ? candidate.plaqueta
        ? `à plaqueta ${candidate.plaqueta} · ${candidate.descricao} · ${candidate.setor || 'Sem setor'}`
        : `ao bem sem plaqueta "${candidate.descricao}" · ${candidate.setor || 'Sem setor'}`
      : `à plaqueta ${plaqueta}`;
    const alertaDivergente = usarIdentificador && statusesDivergentes.has(
      String(candidate.status || '').toUpperCase()
    )
      ? ' Atenção: status DIVERG; confira se o setor está correto.'
      : '';
    if (!window.confirm(
      `Confirma que "${item.descricao}" corresponde ${alvo}? Nenhuma baixa será realizada.${alertaDivergente}`
    )) return;
    linkButton.disabled = true;
    try {
      const result = await api(`/api/leilao/${encodeURIComponent(item.id)}/vincular`, {
        method: 'POST',
        body: usarIdentificador
          ? { identificador: candidate.id }
          : { plaqueta },
      });
      notify(result.mensagem);
      await loadAuctionList();
    } catch (error) {
      notify(error.message, 'error');
      linkButton.disabled = false;
    }
  });
  container.append(select, manualPlaqueta, linkButton);
}

async function createReport() {
  const button = $('#report-button');
  setBusy(button, true, 'Gerando…');
  try {
    const result = await api('/api/relatorios/pendencias', { method: 'POST', body: {} });
    const link = document.createElement('a');
    link.href = `/api/relatorios/${encodeURIComponent(result.arquivo)}`;
    link.textContent = `Baixar ${result.arquivo}`;
    link.download = result.arquivo;
    const text = document.createElement('p');
    text.textContent = `${result.total} bens · ${result.pendencias} pendências · ${result.abas} abas`;
    $('#report-result').replaceChildren(text, link);
    notify('Relatório gerado.');
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function loadBackups() {
  try {
    const data = await api('/api/backups');
    const container = $('#backup-list');
    container.replaceChildren();
    if (!data.backups.length) {
      container.textContent = 'Nenhum backup web criado ainda.';
      return;
    }
    data.backups.forEach(backup => {
      const row = document.createElement('div');
      row.className = 'backup-row';
      const info = document.createElement('span');
      info.textContent = `${backup.data} · ${formatBytes(backup.tamanho)}`;
      const actions = document.createElement('div');
      actions.className = 'backup-actions';
      const download = document.createElement('a');
      download.href = `/api/backups/${encodeURIComponent(backup.arquivo)}`;
      download.download = backup.arquivo;
      download.textContent = 'Baixar';
      const restore = document.createElement('button');
      restore.type = 'button';
      restore.className = 'button button-warning table-action';
      restore.textContent = 'Restaurar';
      restore.addEventListener('click', () => restoreBackup(backup.arquivo));
      actions.append(download, restore);
      row.append(info, actions);
      container.append(row);
    });
  } catch (error) {
    notify(error.message, 'error');
  }
}

async function restoreBackup(filename) {
  const confirmed = window.confirm(
    `Restaurar ${filename} substituirá os dados web atuais (entradas, conferência e relatórios). Antes da substituição, a aplicação criará um backup do estado atual. Continuar?`
  );
  if (!confirmed) return;
  try {
    const result = await api(
      `/api/backups/${encodeURIComponent(filename)}/restaurar`,
      { method: 'POST', body: { confirmado: true } },
    );
    notify(result.mensagem);
    await loadStatus();
    await loadBackups();
  } catch (error) {
    notify(error.message, 'error');
  }
}

async function loadManagedUsers() {
  const list = $('#user-list');
  if (!list) return;
  try {
    const data = await api('/api/admin/usuarios');
    list.replaceChildren();
    if (!data.usuarios.length) {
      list.textContent = 'Nenhum usuário operador cadastrado.';
      return;
    }
    data.usuarios.forEach(user => {
      const row = document.createElement('div');
      row.className = 'backup-row';
      const info = document.createElement('span');
      info.textContent = `${user.nome_usuario} · ${user.ativo ? 'Ativo' : 'Desativado'}`
        + (user.trocar_senha ? ' · aguardando troca de senha' : '');
      const actions = document.createElement('div');
      actions.className = 'backup-actions';
      const reset = document.createElement('button');
      reset.type = 'button';
      reset.className = 'button button-muted table-action';
      reset.textContent = 'Redefinir senha';
      reset.addEventListener('click', async () => {
        const password = window.prompt('Informe uma senha temporária (mínimo 12 caracteres):');
        if (password === null) return;
        const confirmation = window.prompt('Digite novamente para confirmar:');
        if (password !== confirmation) {
          notify('As senhas temporárias não conferem.', 'error');
          return;
        }
        setBusy(reset, true, 'Salvando…');
        try {
          const result = await api(`/api/admin/usuarios/${user.id}/senha`, {
            method: 'POST',
            body: { senha_temporaria: password },
          });
          notify(result.mensagem);
          await loadManagedUsers();
        } catch (error) {
          notify(error.message, 'error');
        } finally {
          setBusy(reset, false);
        }
      });
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = `button ${user.ativo ? 'button-warning' : 'button-secondary'} table-action`;
      toggle.textContent = user.ativo ? 'Desativar' : 'Reativar';
      toggle.addEventListener('click', async () => {
        const action = user.ativo ? 'desativar' : 'reativar';
        if (!window.confirm(`Deseja ${action} a conta de ${user.nome_usuario}?`)) return;
        setBusy(toggle, true, 'Salvando…');
        try {
          const result = await api(`/api/admin/usuarios/${user.id}/estado`, {
            method: 'POST',
            body: { ativo: !user.ativo },
          });
          notify(result.mensagem);
          await loadManagedUsers();
        } catch (error) {
          notify(error.message, 'error');
        } finally {
          setBusy(toggle, false);
        }
      });
      actions.append(reset, toggle);
      row.append(info, actions);
      list.append(row);
    });
  } catch (error) {
    notify(`Não foi possível carregar os usuários: ${error.message}`, 'error');
  }
}

$('#csv-form').addEventListener('submit', event => {
  event.preventDefault();
  sendUpload(event.currentTarget, '/api/importar/csv');
});
$('#auction-lot-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Criando…');
  try {
    const result = await api('/api/leilao/lotes', {
      method: 'POST',
      body: {
        nome: $('#auction-lot-name').value,
        valor: $('#auction-lot-value').value,
      },
    });
    form.reset();
    notify(result.mensagem);
    await loadAuctionList();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
});
$('#auction-xlsx-form').addEventListener('submit', event => {
  event.preventDefault();
  importAuctionList(event.currentTarget);
});
$('#auction-csv-form').addEventListener('submit', event => {
  event.preventDefault();
  importAuctionList(event.currentTarget);
});
['#auction-xlsx-form', '#auction-csv-form'].forEach(selector => {
  const form = $(selector);
  form.querySelector('input[type="file"]').addEventListener('change', () => {
    clearAuctionImportPreview(form);
  });
  form.querySelector('.auction-target-lot').addEventListener('change', () => {
    clearAuctionImportPreview(form);
  });
});
$('#auction-list-search').addEventListener('input', () => {
  auctionCurrentPage = 1;
  renderAuctionList(auctionItems, auctionLots);
});
$('#auction-list-lot').addEventListener('change', () => {
  auctionCurrentPage = 1;
  renderAuctionList(auctionItems, auctionLots);
});
$('#auction-export-xlsx').addEventListener('click', event => {
  exportAuctionList(event.currentTarget);
});
$('#auction-manual-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  const body = {
    descricao: $('#auction-description').value,
    plaqueta: $('#auction-plaqueta').value,
    setor: $('#auction-sector').value,
    data: $('#auction-date').value,
    valor: $('#auction-value').value,
    quantidade: $('#auction-quantity').value,
    observacao: $('#auction-observation').value,
    lote_id: $('#auction-manual-lot').value || null,
  };
  setBusy(button, true, 'Salvando…');
  try {
    const result = await api('/api/leilao/manual', { method: 'POST', body });
    form.reset();
    notify(result.mensagem);
    await loadAuctionList();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
});
$('#auction-edit-form').addEventListener('submit', async event => {
  event.preventDefault();
  const id = $('#auction-edit-id').value;
  const button = event.currentTarget.querySelector('button[type="submit"]');
  const body = {
    descricao: $('#auction-edit-description').value,
    plaqueta: $('#auction-edit-plaqueta').value,
    setor: $('#auction-edit-sector').value,
    data: $('#auction-edit-date').value,
    valor: $('#auction-edit-value').value,
    observacao: $('#auction-edit-observation').value,
  };
  setBusy(button, true, 'Salvando…');
  try {
    const result = await api(`/api/leilao/${encodeURIComponent(id)}/editar`, {
      method: 'POST',
      body,
    });
    $('#auction-edit-dialog').close();
    notify(result.mensagem);
    await loadAuctionList();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
});
$('#alienados-form').addEventListener('submit', event => {
  event.preventDefault();
  sendUpload(event.currentTarget, '/api/importar/auxiliar', 'alienados');
});
$('#baixados-form').addEventListener('submit', event => {
  event.preventDefault();
  sendUpload(
    event.currentTarget,
    '/api/importar/auxiliar',
    'baixados',
    'Planilha de bens baixados importada.'
  );
});
$('#sector-form').addEventListener('submit', event => {
  event.preventDefault();
  startConference();
});
$('#sector-select').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    startConference();
  }
});
$('#search-form').addEventListener('submit', runSearch);
$('#clear-search').addEventListener('click', clearSearchFilters);
$('#search-photo').addEventListener('change', runSearch);
$('#search-plaqueta').addEventListener('change', runSearch);
$('#search-excel-button').addEventListener('click', event => {
  createSearchReport('excel', event.currentTarget);
});
$('#search-pdf-button').addEventListener('click', event => {
  createSearchReport('pdf', event.currentTarget);
});
$('#delete-selected-search-items').addEventListener('click', () => deleteSelectedItems('search'));
$('#delete-selected-conference-items').addEventListener('click', () => deleteSelectedItems('sector'));
$('#search-form').querySelectorAll('input, select').forEach(control => {
  control.addEventListener('input', invalidateSearch);
  control.addEventListener('change', invalidateSearch);
  control.addEventListener('input', updateSearchFilterIndicators);
  control.addEventListener('change', updateSearchFilterIndicators);
});
Object.keys(multiFilterConfigs).forEach(renderMultiFilterOptions);
document.addEventListener('click', event => {
  Object.values(multiFilterConfigs).forEach(({ filter }) => {
    const details = $(filter);
    if (!details.contains(event.target)) details.open = false;
  });
});
$('#add-item-button').addEventListener('click', () => {
  $('#add-sector').value = $('#sector-select').value;
  $('#add-dialog').showModal();
});
$('#report-button').addEventListener('click', createReport);
$('#backup-button').addEventListener('click', async event => {
  const button = event.currentTarget;
  setBusy(button, true, 'Criando…');
  try {
    const result = await api('/api/backups', { method: 'POST', body: {} });
    notify(`${result.mensagem} ${result.arquivo}`);
    await loadBackups();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
});
async function prepareData() {
  const buttons = [$('#prepare-button'), $('#prepare-after-csv')];
  const hasBase = currentStatus?.estado === 'disponivel';
  if (hasBase && !window.confirm(
    'A preparação reconstruirá a base operacional. As alterações atuais serão mescladas e um backup web será criado antes da operação. Continuar?'
  )) return;
  const body = new FormData();
  if (hasBase) body.append('confirmado', 'sim');
  buttons.forEach(button => setBusy(button, true, 'Preparando…'));
  try {
    const result = await api('/api/preparar', { method: 'POST', body });
    const notices = [
      result.mensagem,
      `${result.total} bens.`,
      result.baixados
        ? `Baixados: ${result.baixados.na_planilha} plaquetas na lista, ` +
          `${result.baixados.encontrados_no_cadastro} encontradas no cadastro, ` +
          `${result.baixados.marcados_baixa} marcadas como BAIXA e ` +
          `${result.baixados.nao_encontrados} não encontradas.`
        : '',
    ].filter(Boolean).concat(result.avisos || []);
    notify(notices.join(' '));
    $('#csv-import-followup').hidden = true;
    await loadStatus();
    await loadBackups();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    buttons.forEach(button => setBusy(button, false));
  }
}
$('#prepare-button').addEventListener('click', prepareData);
$('#prepare-after-csv').addEventListener('click', prepareData);
const userCreateForm = $('#user-create-form');
if (userCreateForm) {
  userCreateForm.addEventListener('submit', async event => {
    event.preventDefault();
    const button = userCreateForm.querySelector('button[type="submit"]');
    setBusy(button, true, 'Criando…');
    try {
      const result = await api('/api/admin/usuarios', {
        method: 'POST',
        body: {
          nome_usuario: $('#new-username').value,
          senha_temporaria: $('#new-user-password').value,
        },
      });
      userCreateForm.reset();
      notify(result.mensagem);
      await loadManagedUsers();
    } catch (error) {
      notify(error.message, 'error');
    } finally {
      setBusy(button, false);
    }
  });
  loadManagedUsers();
}

$('#edit-status').addEventListener('change', event => stateFromStatus(event.target.value, $('#edit-state')));
$('#edit-form').addEventListener('submit', async event => {
  event.preventDefault();
  const index = $('#edit-index').value;
  const body = {
    status: $('#edit-status').value,
    estado: $('#edit-state').value,
    observacao: $('#edit-observation').value,
  };
  if (isAdmin) body.valor = $('#edit-value').value;
  if (isAdmin && !$('#description-field').hidden) {
    body.descricao = $('#edit-description').value;
  }
  try {
    const result = await api(`/api/itens/${index}/editar`, { method: 'POST', body });
    $('#edit-dialog').close();
    notify(result.mensagem);
    await refreshCurrentView();
  } catch (error) {
    notify(error.message, 'error');
  }
});
$('#save-photo').addEventListener('click', () => savePhoto(false));
$('#add-status').addEventListener('change', event => stateFromStatus(event.target.value, $('#add-state')));
$('#add-form').addEventListener('submit', async event => {
  event.preventDefault();
  const body = {
    setor: $('#add-sector').value,
    status: $('#add-status').value,
    estado: $('#add-state').value,
    descricao: $('#add-description').value,
    quantidade: $('#add-quantity').value,
    observacao: $('#add-observation').value,
  };
  try {
    const result = await api('/api/conferencia/incluir', { method: 'POST', body });
    $('#add-dialog').close();
    notify(result.mensagem);
    $('#sector-select').value = body.setor;
    currentView = { type: 'sector', setor: body.setor };
    await startConference();
    await loadStatus();
  } catch (error) {
    notify(error.message, 'error');
  }
});
document.querySelectorAll('[data-close]').forEach(button => {
  button.addEventListener('click', () => $(`#${button.dataset.close}`).close());
});

function invalidateSearch() {
  lastSearchCount = 0;
  $('#search-excel-button').disabled = true;
  $('#search-pdf-button').disabled = true;
}

loadStatus();
if (isAdmin) loadBackups();
loadAuctionList();
updateSearchFilterIndicators();

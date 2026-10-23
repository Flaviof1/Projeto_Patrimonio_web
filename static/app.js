const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const statusUrl = '/api/status';
let currentStatus = null;
let currentView = { type: 'none' };
let lastSearchCount = 0;
const tablePageSize = 50;

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
  thead.append(heading);
  table.append(thead);

  const tbody = document.createElement('tbody');
  items.forEach(item => {
    const row = document.createElement('tr');
    columns.forEach(([key]) => {
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
      } else {
        cell.textContent = key === 'Data' || key === 'ALTERADO_EM'
          ? formatTableDate(value, key === 'ALTERADO_EM')
          : value;
      }
      cell.dataset.field = key;
      row.append(cell);
    });
    const photoCell = document.createElement('td');
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
    actionCell.className = 'table-actions-cell';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'button button-secondary table-action';
    button.textContent = 'Editar';
    button.addEventListener('click', () => openEdit(item));
    actionCell.append(button);
    row.append(actionCell);
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
  }
}

async function runSearch(event, page = 1) {
  if (event) event.preventDefault();
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
  } catch (error) {
    notify(error.message, 'error');
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

function updateSearchFilterSummary() {
  const filters = collectSearchFilters();
  const criteria = [
    ['Texto', filters.termo ? [filters.termo] : []],
    ['Setor', filters.setores],
    ['Status', filters.status],
    ['Plaqueta', filters.plaqueta ? [filters.plaqueta] : []],
    ['Foto', filters.foto],
    ['Data inicial', filters.data_inicial ? [formatTableDate(filters.data_inicial)] : []],
    ['Data final', filters.data_final ? [formatTableDate(filters.data_final)] : []],
  ].filter(([, values]) => values.length > 0);
  const summary = $('#search-active-filters');
  summary.replaceChildren();

  const label = document.createElement('strong');
  label.textContent = criteria.length ? 'Filtros selecionados:' : 'Nenhum filtro selecionado.';
  summary.append(label);

  criteria.forEach(([name, values]) => {
    const item = document.createElement('span');
    item.className = 'filter-selection';
    item.textContent = `${name}: ${values.join(', ')}`;
    summary.append(item);
  });
}

function clearSearchFilters() {
  $('#search-form').reset();
  $('#search-sectors').selectedIndex = -1;
  $('#search-status').selectedIndex = -1;
  updateSearchFilterSummary();
  invalidateSearch();
  runSearch();
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
  $('#edit-photo').value = '';
  const identifier = String(item['Identificador Provisório'] || '');
  const canEditDescription = identifier.startsWith('SEM-ID-')
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
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'button button-muted table-action';
    remove.textContent = 'Remover foto';
    remove.addEventListener('click', () => savePhoto(true));
    photo.append(remove);
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
  if (!$('#description-field').hidden) {
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
    notify(`${label} ${result.registros !== undefined ? `${result.registros} registros validados.` : ''}`);
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
  const body = new FormData();
  body.append('arquivo', file);
  const button = form.querySelector('button[type="submit"]');
  setBusy(button, true, 'Importando…');
  try {
    const result = await api('/api/leilao/importar', { method: 'POST', body });
    form.reset();
    notify(`${result.mensagem} Total na lista: ${result.total}.`);
    await loadAuctionList();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

async function loadAuctionList() {
  try {
    const data = await api('/api/leilao');
    $('#auction-summary').textContent = `${data.total} item(ns) na lista.`;
    renderAuctionList(data.itens);
  } catch (error) {
    notify(`Não foi possível carregar a lista de leilão: ${error.message}`, 'error');
  }
}

function renderAuctionList(items) {
  const container = $('#auction-list');
  container.replaceChildren();
  if (!items.length) {
    container.textContent = 'Nenhum item adicionado à lista de leilão.';
    return;
  }

  const table = document.createElement('table');
  const header = document.createElement('tr');
  ['Descrição', 'Plaqueta informada', 'Setor', 'Data', 'Situação', 'Vincular ao patrimônio'].forEach(text => {
    const cell = document.createElement('th');
    cell.textContent = text;
    header.append(cell);
  });
  const thead = document.createElement('thead');
  thead.append(header);
  table.append(thead);
  const body = document.createElement('tbody');

  items.forEach(item => {
    const row = document.createElement('tr');
    [item.descricao, item.plaqueta_informada, item.setor, item.data].forEach(value => {
      const cell = document.createElement('td');
      cell.textContent = value || '—';
      row.append(cell);
    });
    const status = document.createElement('td');
    status.textContent = item.situacao === 'IDENTIFICADO'
      ? `Identificado · Plaqueta ${item.plaqueta_vinculada}`
      : 'Pendente de identificação';
    row.append(status);

    const action = document.createElement('td');
    if (item.situacao !== 'IDENTIFICADO') {
      const field = document.createElement('div');
      field.className = 'auction-link-controls';
      const searchButton = document.createElement('button');
      searchButton.type = 'button';
      searchButton.className = 'button button-secondary';
      searchButton.textContent = 'Buscar possíveis correspondências';
      searchButton.addEventListener('click', async () => {
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
      });
      field.append(searchButton);
      action.append(field);
    } else {
      action.textContent = 'Vínculo confirmado manualmente.';
    }
    row.append(action);
    body.append(row);
  });

  table.append(body);
  container.append(table);
}

function renderAuctionLinkControls(item, container, suggestions) {
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
    option.value = candidate.plaqueta;
    option.textContent =
      `${candidate.plaqueta} · ${candidate.descricao} · ${candidate.setor || 'Sem setor'} · ${candidate.similaridade}%`;
    select.append(option);
  });

  const manualPlaqueta = document.createElement('input');
  manualPlaqueta.type = 'text';
  manualPlaqueta.maxLength = 100;
  manualPlaqueta.placeholder = 'Plaqueta confirmada';
  manualPlaqueta.setAttribute('aria-label', `Plaqueta confirmada para ${item.descricao}`);
  select.addEventListener('change', () => {
    if (select.value) manualPlaqueta.value = select.value;
  });

  const linkButton = document.createElement('button');
  linkButton.type = 'button';
  linkButton.className = 'button button-secondary';
  linkButton.textContent = 'Confirmar vínculo';
  linkButton.addEventListener('click', async () => {
    const plaqueta = manualPlaqueta.value.trim() || select.value || item.plaqueta_informada;
    if (!plaqueta) {
      notify('Informe ou selecione uma plaqueta para confirmar o vínculo.', 'error');
      return;
    }
    if (!window.confirm(
      `Confirma que "${item.descricao}" corresponde à plaqueta ${plaqueta}? Nenhuma baixa será realizada.`
    )) return;
    linkButton.disabled = true;
    try {
      const result = await api(`/api/leilao/${encodeURIComponent(item.id)}/vincular`, {
        method: 'POST',
        body: { plaqueta },
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

$('#csv-form').addEventListener('submit', event => {
  event.preventDefault();
  sendUpload(event.currentTarget, '/api/importar/csv');
});
$('#auction-xlsx-form').addEventListener('submit', event => {
  event.preventDefault();
  importAuctionList(event.currentTarget);
});
$('#auction-csv-form').addEventListener('submit', event => {
  event.preventDefault();
  importAuctionList(event.currentTarget);
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
$('#search-form').querySelectorAll('input, select').forEach(control => {
  control.addEventListener('input', invalidateSearch);
  control.addEventListener('change', invalidateSearch);
  control.addEventListener('input', updateSearchFilterSummary);
  control.addEventListener('change', updateSearchFilterSummary);
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
$('#prepare-button').addEventListener('click', async event => {
  const button = event.currentTarget;
  const hasBase = currentStatus?.estado === 'disponivel';
  if (hasBase && !window.confirm(
    'A preparação reconstruirá a base operacional. As alterações atuais serão mescladas e um backup web será criado antes da operação. Continuar?'
  )) return;
  const body = new FormData();
  if (hasBase) body.append('confirmado', 'sim');
  setBusy(button, true, 'Preparando…');
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
    await loadStatus();
    await loadBackups();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
});

$('#edit-status').addEventListener('change', event => stateFromStatus(event.target.value, $('#edit-state')));
$('#edit-form').addEventListener('submit', async event => {
  event.preventDefault();
  const index = $('#edit-index').value;
  const body = {
    status: $('#edit-status').value,
    estado: $('#edit-state').value,
    observacao: $('#edit-observation').value,
  };
  if (!$('#description-field').hidden) body.descricao = $('#edit-description').value;
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
loadBackups();
loadAuctionList();
updateSearchFilterSummary();

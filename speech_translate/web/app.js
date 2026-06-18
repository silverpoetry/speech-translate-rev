const state = {
  data: null,
  taskTimer: null,
  uiRefreshTimer: null,
  logRefreshTimer: null,
  initialized: false,
  bridgeReady: false,
  eventsBound: false,
  uiEventsBound: false,
  pageScrollBound: false,
  modelPollTimer: null,
  modelCheckedOnce: false,
  modelManagerCheckedEngines: {},
  seleniumSaveInFlight: false,
  detachedModeSelected: 'tc',
  detachedOpen: { tc: false, tl: false },
  initInFlight: null,
  autoSaveBound: false,
  autoSaveTimers: {},
  fileImportQueue: [],
  fileProcessingState: null,
  modelManagerState: null,
  modalBound: false,
  modalResolver: null,
};

const els = {};

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatBytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size <= 0) {
    return '0 B';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let next = size;
  let unitIndex = 0;
  while (next >= 1024 && unitIndex < units.length - 1) {
    next /= 1024;
    unitIndex += 1;
  }
  return unitIndex === 0 ? `${Math.round(next)} ${units[unitIndex]}` : `${next.toFixed(1)} ${units[unitIndex]}`;
}

function summarizeSettingText(value, fallback = '未设置', maxLength = 40) {
  const text = String(value ?? '').trim();
  if (!text) {
    return fallback;
  }
  return text.length > maxLength ? `${text.slice(0, Math.max(0, maxLength - 1))}…` : text;
}

function summarizeFileSliceRange(start, end) {
  const from = String(start ?? '').trim();
  const to = String(end ?? '').trim();
  if (!from && !to) {
    return '全量';
  }
  return `${from || '起点'} → ${to || '末尾'}`;
}

function summarizeFilterDictionaryPath(pathValue) {
  const normalized = String(pathValue ?? '').trim();
  if (!normalized || normalized.toLowerCase() === 'auto') {
    return '词典 auto';
  }
  return `词典 ${summarizeSettingText(normalized, '词典 auto', 22)}`;
}

function summarizeModelDevicePreference(value) {
  const normalized = String(value ?? 'auto').trim().toLowerCase();
  if (normalized === 'cuda') {
    return { label: 'CUDA', meta: '优先使用 GPU 进行推理' };
  }
  if (normalized === 'cpu') {
    return { label: 'CPU', meta: '固定使用 CPU，避免 GPU 依赖' };
  }
  return { label: 'AUTO', meta: '自动选择可用设备' };
}

function syncToolbarMirrorValue(target, source, fallback = '') {
  if (!target || !source) {
    return;
  }
  target.value = source.value ?? fallback;
}

function syncToolbarMirrorChecked(target, source, fallback = false) {
  if (!target || !source) {
    return;
  }
  target.checked = typeof source.checked === 'boolean' ? source.checked : fallback;
}

function isBooleanInput(node) {
  if (!(node instanceof HTMLInputElement)) {
    return false;
  }
  const type = String(node.type || '').toLowerCase();
  return type === 'checkbox' || type === 'radio';
}

function readInputValue(node, fallback = '') {
  if (!node) {
    return fallback;
  }
  if (isBooleanInput(node)) {
    return Boolean(node.checked);
  }
  if (node instanceof HTMLInputElement) {
    const type = String(node.type || '').toLowerCase();
    if (type === 'number' || type === 'range') {
      const parsed = Number(node.value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }
  }
  if (typeof node.value !== 'undefined') {
    return node.value;
  }
  return fallback;
}

function writeInputValue(node, value, fallback = '') {
  if (!node) {
    return;
  }
  const resolved = value ?? fallback;
  if (isBooleanInput(node)) {
    node.checked = Boolean(resolved);
    return;
  }
  if (typeof node.value !== 'undefined') {
    node.value = String(resolved);
  }
}

function readStringValue(node, fallback = '') {
  const value = readInputValue(node, fallback);
  return value == null ? String(fallback ?? '') : String(value);
}

function readBooleanValue(node, fallback = false) {
  return Boolean(readInputValue(node, fallback));
}

function readNumberValue(node, fallback = 0) {
  const parsed = Number(readInputValue(node, fallback));
  return Number.isFinite(parsed) ? parsed : fallback;
}

function readSelectedLabel(node, fallback = '') {
  if (!node || !node.selectedOptions || !node.selectedOptions[0]) {
    return fallback;
  }
  return String(node.selectedOptions[0].textContent || node.value || fallback);
}

function bindToolbarMirror(source, target, kind = 'value') {
  if (!source || !target) {
    return;
  }
  const bindDirection = (from, to) => {
    const eventName = from.tagName === 'SELECT' || kind === 'checked' ? 'change' : 'input';
    from.addEventListener(eventName, () => {
      if (kind === 'checked') {
        to.checked = from.checked;
        return;
      }
      to.value = from.value;
    });
  };
  bindDirection(source, target);
  bindDirection(target, source);
}

function bindPairedInputValues(pairs = []) {
  for (const [left, right] of pairs) {
    bindToolbarMirror(left, right, 'value');
  }
}

function bindPairedInputChecks(pairs = []) {
  for (const [left, right] of pairs) {
    bindToolbarMirror(left, right, 'checked');
  }
}

function bindToolbarMirrorValues(pairs = []) {
  bindPairedInputValues(pairs);
}

function bindToolbarMirrorChecks(pairs = []) {
  bindPairedInputChecks(pairs);
}

function syncMirroredFieldState() {
  syncToolbarMirrorValues([
    [els.dirExportFile, els.dirExport, 'auto'],
    [els.exportFormatToolbar, els.exportFormat, '%Y-%m-%d %f {file}/{task-lang}'],
  ]);
  syncToolbarMirrorChecks([
    [els.autoOpenDirExportFile, els.autoOpenDirExport, true],
  ]);
}

function bindSharedFieldMirrors() {
  bindPairedInputValues([
    [els.dirExport, els.dirExportFile],
  ]);
  bindPairedInputChecks([
    [els.autoOpenDirExport, els.autoOpenDirExportFile],
  ]);
}

function syncToolbarMirrorValues(pairs = []) {
  for (const [target, source, fallback = ''] of pairs) {
    syncToolbarMirrorValue(target, source, fallback);
  }
}

function syncToolbarMirrorChecks(pairs = []) {
  for (const [target, source, fallback = false] of pairs) {
    syncToolbarMirrorChecked(target, source, fallback);
  }
}

function collectCheckedValues(definitions = []) {
  const values = [];
  for (const [value, node] of definitions) {
    if (readBooleanValue(node, false)) {
      values.push(value);
    }
  }
  return values;
}

async function persistApiPairs(apiName, updates = []) {
  for (const [key, value] of updates) {
    await apiCall(apiName, key, value);
  }
}

async function persistRecordDeviceSelection(hostApiValue, micValue, speakerValue) {
  await persistApiPairs('set_record_setting', [
    ['hostAPI', hostApiValue],
    ['mic', micValue],
    ['speaker', speakerValue],
  ]);
}

function collectRecordDeviceSelection() {
  return {
    hostAPI: readStringValue(els.hostAPI, ''),
    mic: readStringValue(els.mic, ''),
    speaker: readStringValue(els.speaker, ''),
  };
}

async function persistCurrentRecordDeviceSelection() {
  const selection = collectRecordDeviceSelection();
  await persistRecordDeviceSelection(selection.hostAPI, selection.mic, selection.speaker);
  return selection;
}

function bindSelectSettingPersistence(node, apiName, key, options = {}) {
  if (!node) {
    return;
  }
  const read = typeof options.read === 'function' ? options.read : ((currentNode) => currentNode.value);
  const onError = typeof options.onError === 'function' ? options.onError : null;
  node.addEventListener('change', async () => {
    try {
      await apiCall(apiName, key, read(node));
    } catch (error) {
      if (onError) {
        onError(error);
        return;
      }
      console.error(`保存 ${apiName}:${key} 失败`, error);
    }
  });
}

function setAppModalHidden(hidden) {
  if (!els.appModalBackdrop || !els.appModalCard) {
    return;
  }
  els.appModalBackdrop.classList.toggle('is-hidden', Boolean(hidden));
  els.appModalBackdrop.setAttribute('aria-hidden', hidden ? 'true' : 'false');
}

function resolveAppModal(result) {
  if (typeof state.modalResolver === 'function') {
    const resolver = state.modalResolver;
    state.modalResolver = null;
    resolver(Boolean(result));
  }
  setAppModalHidden(true);
}

function bindAppModalEvents() {
  if (state.modalBound || !els.appModalBackdrop) {
    return;
  }
  state.modalBound = true;

  els.appModalConfirm?.addEventListener('click', () => resolveAppModal(true));
  els.appModalCancel?.addEventListener('click', () => resolveAppModal(false));
  els.appModalClose?.addEventListener('click', () => resolveAppModal(false));
  els.appModalBackdrop.addEventListener('click', (event) => {
    if (event.target === els.appModalBackdrop && !els.appModalBackdrop.dataset.locked) {
      resolveAppModal(false);
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && state.modalResolver) {
      resolveAppModal(false);
    }
  });
}

async function showAppModal({
  title = '确认操作',
  message = '',
  kicker = '确认操作',
  confirmLabel = '确认',
  cancelLabel = '取消',
  tone = 'default',
  dismissible = true,
  singleAction = false,
} = {}) {
  if (!els.appModalBackdrop || !els.appModalCard) {
    if (singleAction) {
      if (typeof window !== 'undefined' && typeof window.alert === 'function') {
        window.alert(message || title);
      }
      return true;
    }
    if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
      return window.confirm(message || title);
    }
    return true;
  }

  bindAppModalEvents();
  if (state.modalResolver) {
    resolveAppModal(false);
  }

  els.appModalKicker.textContent = kicker;
  els.appModalTitle.textContent = title;
  els.appModalMessage.textContent = message;
  els.appModalConfirm.textContent = confirmLabel;
  els.appModalCancel.textContent = cancelLabel;
  els.appModalConfirm.classList.toggle('btn-danger-soft', tone === 'danger');
  els.appModalConfirm.classList.toggle('btn-major', tone !== 'danger');
  els.appModalCancel.classList.toggle('is-hidden', Boolean(singleAction));
  els.appModalClose.classList.toggle('is-hidden', !dismissible);
  if (dismissible) {
    delete els.appModalBackdrop.dataset.locked;
  } else {
    els.appModalBackdrop.dataset.locked = 'true';
  }
  setAppModalHidden(false);

  return new Promise((resolve) => {
    state.modalResolver = resolve;
  });
}

function showConfirmDialog(options = {}) {
  return showAppModal({
    kicker: '请确认',
    confirmLabel: '确认',
    cancelLabel: '取消',
    ...options,
  });
}

async function showAlertDialog(options = {}) {
  await showAppModal({
    kicker: '提示',
    confirmLabel: '知道了',
    cancelLabel: '取消',
    singleAction: true,
    ...options,
  });
}

const DETACHED_SETTINGS_SECTION_TITLE = '独立窗口（TC / TL）';

// Default languages to show as individual prompt fields (keeps UI concise)
const DEFAULT_PROMPT_LANGS = ['en','zh','ja','ko','es','fr','de','pt','id'];
const PROMPT_LANG_NAMES = {
  en: 'English',
  zh: '中文',
  ja: '日本語',
  ko: '한국어',
  es: 'Español',
  fr: 'Français',
  de: 'Deutsch',
  pt: 'Português',
  id: 'Bahasa',
};

const DETACHED_WINDOW_PANEL_DEFAULTS = {
  ex_tc_geometry: '900x240',
  ex_tc_always_on_top: true,
  ex_tc_no_title_bar: true,
  ex_tc_click_through: false,
  ex_tc_opacity: 1,
  tb_ex_tc_font: 'Arial',
  tb_ex_tc_font_bold: true,
  tb_ex_tc_font_size: 13,
  tb_ex_tc_font_color: '#FFFFFF',
  tb_ex_tc_bg_color: '#000000',
  tb_ex_tc_limit_max: false,
  tb_ex_tc_limit_max_per_line: false,
  tb_ex_tc_max: 120,
  tb_ex_tc_max_per_line: 30,
  tb_ex_tc_use_conf_color: true,
  ex_tl_geometry: '900x240',
  ex_tl_always_on_top: true,
  ex_tl_no_title_bar: true,
  ex_tl_click_through: false,
  ex_tl_opacity: 1,
  tb_ex_tl_font: 'Arial',
  tb_ex_tl_font_bold: true,
  tb_ex_tl_font_size: 13,
  tb_ex_tl_font_color: '#FFFFFF',
  tb_ex_tl_bg_color: '#000000',
  tb_ex_tl_limit_max: false,
  tb_ex_tl_limit_max_per_line: false,
  tb_ex_tl_max: 120,
  tb_ex_tl_max_per_line: 30,
  tb_ex_tl_use_conf_color: true,
};

const DETACHED_WINDOW_PANEL_KEYS = Object.keys(DETACHED_WINDOW_PANEL_DEFAULTS);
const DETACHED_WINDOW_MIRROR_PAIRS = {
  tc: [
    ['ex_tc_geometry_main', 'ex_tc_geometry'],
    ['ex_tc_opacity_main', 'ex_tc_opacity'],
    ['ex_tc_always_on_top_main', 'ex_tc_always_on_top'],
    ['ex_tc_no_title_bar_main', 'ex_tc_no_title_bar'],
    ['ex_tc_click_through_main', 'ex_tc_click_through'],
    ['tb_ex_tc_use_conf_color_main', 'tb_ex_tc_use_conf_color'],
  ],
  tl: [
    ['ex_tl_geometry_main', 'ex_tl_geometry'],
    ['ex_tl_opacity_main', 'ex_tl_opacity'],
    ['ex_tl_always_on_top_main', 'ex_tl_always_on_top'],
    ['ex_tl_no_title_bar_main', 'ex_tl_no_title_bar'],
    ['ex_tl_click_through_main', 'ex_tl_click_through'],
    ['tb_ex_tl_use_conf_color_main', 'tb_ex_tl_use_conf_color'],
  ],
};

function buildPromptCard({ code = '', value = '', custom = false } = {}) {
  const card = document.createElement('div');
  card.className = 'prompt-card field-span-2';
  card.setAttribute('data-prompt-row', custom ? 'custom' : 'default');

  const head = document.createElement('div');
  head.className = 'prompt-card-head';

  if (custom) {
    const inline = document.createElement('div');
    inline.className = 'prompt-inline';

    const codeLabel = document.createElement('span');
    codeLabel.textContent = '语言代码';

    const codeInput = document.createElement('input');
    codeInput.type = 'text';
    codeInput.className = 'input card-input';
    codeInput.placeholder = '例如 it / ru / zh-TW';
    codeInput.value = String(code || '');
    codeInput.setAttribute('data-lang-code', 'true');
    codeInput.addEventListener('change', async () => {
      try {
        await saveInitialPromptsSettings(false);
      } catch (err) {
        console.error('保存自定义语言代码失败', err);
      }
    });

    inline.appendChild(codeLabel);
    inline.appendChild(codeInput);
    head.appendChild(inline);

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'btn-icon btn-icon-sm';
    removeBtn.title = '删除自定义语言行';
    removeBtn.setAttribute('data-remove-custom-prompt', 'true');
    removeBtn.innerHTML = '<span class="btn-glyph glyph-trash" aria-hidden="true"></span>';
    removeBtn.addEventListener('click', async () => {
      card.remove();
      try {
        await saveInitialPromptsSettings(false);
      } catch (err) {
        console.error('删除自定义语言行后保存失败', err);
      }
    });
    head.appendChild(removeBtn);
  } else {
    const title = document.createElement('span');
    title.textContent = `${PROMPT_LANG_NAMES[code] || code} (${code})`;
    head.appendChild(title);
    card.setAttribute('data-lang', code);
  }

  const ta = document.createElement('textarea');
  ta.rows = 3;
  ta.className = 'input card-input';
  ta.placeholder = custom ? '输入该语言的自定义引导词，留空则不保存' : '留空表示使用内置默认引导词';
  ta.value = String(value || '');
  ta.setAttribute('data-prompt-text', 'true');
  if (!custom) {
    ta.setAttribute('data-lang', code);
  }
  ta.addEventListener('change', async () => {
    try {
      await saveInitialPromptsSettings(false);
    } catch (err) {
      console.error('保存引导词内容失败', err);
    }
  });

  card.appendChild(head);
  card.appendChild(ta);
  return card;
}

function addCustomInitialPromptRow(code = '', value = '') {
  if (!els.initialPromptsContainer) {
    return null;
  }
  const card = buildPromptCard({ code, value, custom: true });
  els.initialPromptsContainer.appendChild(card);
  return card;
}

function buildInitialPromptsUi(container, map) {
  try {
    container.innerHTML = '';
    const next = map || {};
    for (const code of DEFAULT_PROMPT_LANGS) {
      container.appendChild(buildPromptCard({ code, value: next[code] || '', custom: false }));
    }

    // Also include any custom language keys present in user map
    for (const k of Object.keys(next)) {
      if (!DEFAULT_PROMPT_LANGS.includes(k)) {
        container.appendChild(buildPromptCard({ code: k, value: next[k] || '', custom: true }));
      }
    }
  } catch (e) {
    console.debug('Failed to build initial prompts UI', e);
  }
}

async function apiCall(name, ...args) {
  if (!window.pywebview || !window.pywebview.api) {
    throw new Error('pywebview API 尚未就绪');
  }
  return window.pywebview.api[name](...args);
}

async function startupMark(marker) {
  try {
    await apiCall('mark_startup', marker);
  } catch (_error) {
    // Startup marker is best-effort and should never block init flow.
  }
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function nextUiTurn() {
  return new Promise((resolve) => window.setTimeout(resolve, 0));
}

function isCompactViewport(maxWidth = 780) {
  return window.innerWidth <= maxWidth;
}

function updatePageScrollIndicator() {
  const track = els.pageScrollIndicator;
  const thumb = els.pageScrollThumb;
  const scroller = els.dashboardContent;
  if (!track || !thumb) {
    return;
  }
  if (!scroller) {
    track.style.opacity = '0';
    return;
  }

  const scrollTop = scroller.scrollTop;
  const scrollHeight = scroller.scrollHeight;
  const clientHeight = scroller.clientHeight;
  const maxScroll = Math.max(1, scrollHeight - clientHeight);

  if (maxScroll <= 1) {
    track.style.opacity = '0';
    return;
  }

  track.style.opacity = '1';

  const trackHeight = track.clientHeight;
  const ratio = clientHeight / scrollHeight;
  const thumbHeight = Math.max(28, Math.min(trackHeight, Math.round(trackHeight * ratio)));
  const travel = Math.max(0, trackHeight - thumbHeight);
  const top = Math.round((scrollTop / maxScroll) * travel);

  thumb.style.height = `${thumbHeight}px`;
  thumb.style.top = `${top}px`;
}

function bindPageScrollIndicator() {
  if (state.pageScrollBound) {
    return;
  }
  if (els.dashboardContent) {
    els.dashboardContent.addEventListener('scroll', updatePageScrollIndicator, { passive: true });
  }
  window.addEventListener('resize', updatePageScrollIndicator);
  state.pageScrollBound = true;
}

async function waitForBridge(timeoutMs = 12000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (window.pywebview && window.pywebview.api) {
      state.bridgeReady = true;
      return true;
    }
    await sleep(80);
  }
  return false;
}

function scheduleUiRefresh(sections = []) {
  const sectionSet = new Set(Array.isArray(sections) ? sections : [sections]);
  if (state.uiRefreshTimer !== null) {
    window.clearTimeout(state.uiRefreshTimer);
  }

  state.uiRefreshTimer = window.setTimeout(() => {
    state.uiRefreshTimer = null;
    const needsFullRefresh = sectionSet.has('settings') || sectionSet.has('state');
    const refreshPromise = needsFullRefresh ? refreshState() : refreshTaskState();
    refreshPromise.catch((error) => console.error(error));
  }, 50);
}

function bindTooltipLayer() {
  if (state.tooltipBound || typeof document === 'undefined') {
    return;
  }

  const tooltip = document.createElement('div');
  tooltip.className = 'ui-tooltip';
  tooltip.setAttribute('role', 'tooltip');
  document.body.appendChild(tooltip);
  els.uiTooltip = tooltip;

  let activeTrigger = null;

  const hideTooltip = () => {
    activeTrigger = null;
    tooltip.classList.remove('is-visible');
    tooltip.textContent = '';
    tooltip.style.left = '-9999px';
    tooltip.style.top = '-9999px';
  };

  const positionTooltip = (trigger) => {
    if (!trigger || !tooltip.textContent) {
      return;
    }
    const rect = trigger.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const margin = 10;
    let left = rect.left + (rect.width / 2) - (tooltipRect.width / 2);
    left = Math.max(margin, Math.min(left, window.innerWidth - tooltipRect.width - margin));
    let top = rect.top - tooltipRect.height - 10;
    if (top < margin) {
      top = rect.bottom + 10;
      tooltip.style.setProperty('--tooltip-arrow-flip', '1');
    } else {
      tooltip.style.setProperty('--tooltip-arrow-flip', '0');
    }
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
  };

  const showTooltip = (trigger) => {
    const label = String(trigger?.getAttribute('data-tooltip') || '').trim();
    if (!label) {
      hideTooltip();
      return;
    }
    activeTrigger = trigger;
    tooltip.textContent = label;
    tooltip.classList.add('is-visible');
    positionTooltip(trigger);
  };

  document.addEventListener('pointerover', (event) => {
    const trigger = event.target && event.target.closest ? event.target.closest('[data-tooltip]') : null;
    if (!trigger) {
      return;
    }
    showTooltip(trigger);
  });

  document.addEventListener('pointerout', (event) => {
    if (!activeTrigger) {
      return;
    }
    const related = event.relatedTarget;
    if (related && activeTrigger.contains && activeTrigger.contains(related)) {
      return;
    }
    const leaving = event.target && event.target.closest ? event.target.closest('[data-tooltip]') : null;
    if (leaving === activeTrigger) {
      hideTooltip();
    }
  });

  document.addEventListener('focusin', (event) => {
    const trigger = event.target && event.target.closest ? event.target.closest('[data-tooltip]') : null;
    if (trigger) {
      showTooltip(trigger);
    }
  });

  document.addEventListener('focusout', (event) => {
    const trigger = event.target && event.target.closest ? event.target.closest('[data-tooltip]') : null;
    if (trigger && trigger === activeTrigger) {
      hideTooltip();
    }
  });

  window.addEventListener('scroll', () => {
    if (activeTrigger) {
      positionTooltip(activeTrigger);
    }
  }, true);

  window.addEventListener('resize', () => {
    if (activeTrigger) {
      positionTooltip(activeTrigger);
    }
  });

  state.tooltipBound = true;
}

function bindUiEvents() {
  if (state.uiEventsBound) {
    return;
  }

  window.addEventListener('speechtranslate-ui-update', (event) => {
    const detail = event && event.detail ? event.detail : {};
    const sections = Array.isArray(detail.sections) ? detail.sections : ['task'];
    scheduleUiRefresh(sections);
    if (sections.includes('import')) {
      refreshFileProcessingState().catch((error) => {
        console.error('Import queue refresh failed', error);
      });
    }
  });

  bindTooltipLayer();
  state.uiEventsBound = true;
}

function populateSelect(selectEl, options, selectedValue, keepMissingSelection = true) {
  const normalizedOptions = (Array.isArray(options) ? options : []).map((option) => {
    if (option && typeof option === 'object') {
      const value = String(option.value ?? '');
      const label = String(option.label ?? option.value ?? '');
      return { value, label };
    }
    const value = String(option ?? '');
    return { value, label: value };
  });
  const currentValue = String(selectedValue ?? '');
  selectEl.innerHTML = normalizedOptions
    .map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`)
    .join('');

  if (keepMissingSelection && currentValue && !normalizedOptions.some((option) => option.value === currentValue)) {
    const option = document.createElement('option');
    option.value = currentValue;
    option.textContent = currentValue;
    selectEl.appendChild(option);
  }

  selectEl.value = currentValue;
}

function patchLocalSettings(updates) {
  if (!updates || typeof updates !== 'object') {
    return;
  }
  state.data = state.data || {};
  state.data.settings = {
    ...(state.data.settings || {}),
    ...updates,
  };
}

function patchLocalImportUi(updates) {
  if (!updates || typeof updates !== 'object') {
    return;
  }
  state.data = state.data || {};
  state.data.import_ui = {
    ...(state.data.import_ui || {}),
    ...updates,
  };
}

function readExportToSelection() {
  return collectCheckedValues([
    ['txt', els.exportTxt],
    ['srt', els.exportSrt],
    ['vtt', els.exportVtt],
    ['ass', els.exportAss],
    ['json', els.exportJson],
    ['csv', els.exportCsv],
    ['tsv', els.exportTsv],
    ['mp4', els.exportMp4],
  ]);
}

function collectSharedFileSettings() {
  const currentSettings = state.data?.settings || {};
  const exportDir = els.dirExport
    ? readStringValue(els.dirExport, String(currentSettings.dir_export ?? 'auto'))
    : String(currentSettings.dir_export ?? 'auto');
  const autoOpenDirOnTaskDone = readBooleanValue(els.autoOpenDirExport, true);
  return {
    dir_export: exportDir,
    export_to: readExportToSelection(),
    auto_open_dir_export: autoOpenDirOnTaskDone,
    auto_open_dir_translate: autoOpenDirOnTaskDone,
    auto_open_dir_refinement: autoOpenDirOnTaskDone,
    auto_open_dir_alignment: autoOpenDirOnTaskDone,
    export_format: readStringValue(els.exportFormat, '%Y-%m-%d %f {file}/{task-lang}'),
    path_filter_file_import: readStringValue(els.pathFilterFileImport, 'auto'),
    remove_repetition_file_import: readBooleanValue(els.removeRepetitionFileImport, false),
    remove_repetition_amount: readNumberValue(els.removeRepetitionAmount, 1),
    segment_max_words: readStringValue(els.segmentMaxWords, ''),
    segment_max_chars: readStringValue(els.segmentMaxChars, ''),
    segment_split_or_newline: readStringValue(els.segmentSplitOrNewline, 'split'),
    segment_even_split: readBooleanValue(els.segmentEvenSplit, true),
    segment_level: readBooleanValue(els.segmentLevel, true),
    word_level: readBooleanValue(els.wordLevel, true),
    file_slice_start: readStringValue(els.fileSliceStart, ''),
    file_slice_end: readStringValue(els.fileSliceEnd, ''),
  };
}

async function persistSharedFileSettings() {
  const updates = collectSharedFileSettings();
  await persistApiPairs('set_setting', Object.entries(updates));
  patchLocalSettings(updates);
  return updates;
}

function resolveModelRuntimeSummary(data) {
  const importUi = data?.import_ui || {};
  const runtime = data?.runtime_model || {};
  const selectedModelKey = String(importUi.selected_model_key || importUi.selected_model || '').trim();
  const selectedBackend = String(importUi.selected_backend || '').trim() || '后端未知';
  const runtimeKey = String(runtime.key || '').trim();
  const runtimeLoaded = Boolean(runtime.loaded);
  const runtimeLoading = Boolean(runtime.loading);
  const runtimeMessage = String(runtime.message || '').trim();
  const effectiveModelKey = runtimeKey || selectedModelKey || '未知';
  return {
    runtimeLoaded,
    runtimeLoading,
    runtimeMessage,
    selectedModelKey,
    selectedBackend,
    effectiveModelKey,
    runtimeStateLabel: runtimeLoaded ? '运行：已加载' : runtimeLoading ? '运行：加载中' : '运行：未加载',
    runtimeMetaLabel: runtimeMessage || (runtimeLoaded ? `当前运行：${effectiveModelKey}` : '等待加载'),
    taskPillLabel: `${runtimeLoaded ? effectiveModelKey : (selectedModelKey || '未选择')} / ${selectedBackend}`,
  };
}

function resolveFileProcessingSummary() {
  const processing = state.fileProcessingState || {};
  const queue = Array.isArray(state.fileImportQueue) ? state.fileImportQueue : [];
  const total = Number(processing.files_total || queue.length || 0) || 0;
  const completed = Number(processing.files_completed || 0) || 0;
  const active = Boolean(processing.active);
  const failed = Number(processing.files_failed || 0) || 0;
  return {
    total,
    completed,
    active,
    failed,
    queueLabel: total > 0 ? `队列：${total}，完成 ${completed}` : '队列：0',
    stateLabel: active
      ? (failed > 0 ? `状态：处理中，失败 ${failed}` : `状态：处理中，剩余 ${Math.max(total - completed, 0)}`)
      : (failed > 0 ? `状态：失败 ${failed}` : (total > 0 ? '状态：待启动' : '状态：空闲')),
  };
}

function renderSettings(data) {
  const settings = data.settings || {};
  for (const [key, fallback] of Object.entries(DETACHED_WINDOW_PANEL_DEFAULTS)) {
    const node = $(key);
    if (!node) {
      continue;
    }
    writeInputValue(node, settings[key], fallback);
  }
  for (const pairs of Object.values(DETACHED_WINDOW_MIRROR_PAIRS)) {
    for (const [mirrorId, sourceId] of pairs) {
      const sourceNode = $(sourceId);
      const mirrorNode = $(mirrorId);
      if (!sourceNode || !mirrorNode) {
        continue;
      }
      writeInputValue(mirrorNode, readInputValue(sourceNode, ''), '');
    }
  }
  if (els.dirExport) {
    els.dirExport.value = settings.dir_export ?? 'auto';
  }
  if (els.dirExportFile) {
    els.dirExportFile.value = settings.dir_export ?? 'auto';
  }
  if (els.dirModel) {
    els.dirModel.value = data?.about?.model_dir || '';
  }
  if (els.dirLog) {
    els.dirLog.value = String(settings.dir_log ?? 'auto');
  }
  if (els.currentLog) {
    els.currentLog.value = data.current_log || data?.about?.log_file || '';
  }
  if (els.logLevel) {
    els.logLevel.value = String(settings.log_level ?? 'DEBUG');
  }
  if (els.autoScrollLog) {
    els.autoScrollLog.checked = Boolean(settings.auto_scroll_log ?? true);
  }
  if (els.autoRefreshLog) {
    els.autoRefreshLog.checked = Boolean(settings.auto_refresh_log ?? false);
  }
  if (els.logContent) {
    const shouldStickToBottom =
      Boolean(settings.auto_scroll_log ?? true) ||
      Math.abs((els.logContent.scrollTop + els.logContent.clientHeight) - els.logContent.scrollHeight) < 24;
    els.logContent.textContent = String(data.log_content || '') || '当前日志为空。';
    if (shouldStickToBottom) {
      els.logContent.scrollTop = els.logContent.scrollHeight;
    }
  }
  if (els.dirExportFile) {
    els.dirExportFile.value = String(settings.dir_export ?? 'auto');
  }
  if (els.autoOpenDirExportFile) {
    els.autoOpenDirExportFile.checked = Boolean(settings.auto_open_dir_export ?? true);
  }
  if (els.autoOpenDirTranslateFile) {
    els.autoOpenDirTranslateFile.checked = Boolean(settings.auto_open_dir_translate ?? true);
  }
  if (els.autoOpenDirRefinementFile) {
    els.autoOpenDirRefinementFile.checked = Boolean(settings.auto_open_dir_refinement ?? true);
  }
  if (els.autoOpenDirAlignmentFile) {
    els.autoOpenDirAlignmentFile.checked = Boolean(settings.auto_open_dir_alignment ?? true);
  }
  const exportTo = settings.export_to || ['txt', 'srt', 'vtt', 'json', 'ass'];
  if (els.exportTxt) els.exportTxt.checked = exportTo.includes('txt');
  if (els.exportSrt) els.exportSrt.checked = exportTo.includes('srt');
  if (els.exportVtt) els.exportVtt.checked = exportTo.includes('vtt');
  if (els.exportAss) els.exportAss.checked = exportTo.includes('ass');
  if (els.exportJson) els.exportJson.checked = exportTo.includes('json');
  if (els.exportCsv) els.exportCsv.checked = exportTo.includes('csv');
  if (els.exportTsv) els.exportTsv.checked = exportTo.includes('tsv');
  if (els.exportMp4) els.exportMp4.checked = exportTo.includes('mp4');
  if (els.mainWindowSize) {
    els.mainWindowSize.value = String(settings.mw_size ?? '1140x680');
  }
  if (els.seleniumCompactLevel) {
    els.seleniumCompactLevel.value = String(settings.selenium_compact_level ?? 2);
  }
  if (els.seleniumZOrderMode) {
    els.seleniumZOrderMode.value = String(settings.selenium_z_order_mode ?? 'behind-main');
  }
  if (els.seleniumAutoCloseOnTaskDone) {
    els.seleniumAutoCloseOnTaskDone.checked = Boolean(settings.selenium_auto_close_on_task_done ?? true);
  }
  if (els.seleniumChromeUserDataDir) {
    els.seleniumChromeUserDataDir.value = String(settings.selenium_chrome_user_data_dir ?? '');
  }
  if (els.httpProxyEnable) {
    els.httpProxyEnable.checked = Boolean(settings.http_proxy_enable ?? false);
  }
  if (els.httpProxy) {
    els.httpProxy.value = String(settings.http_proxy ?? '');
  }
  if (els.httpsProxyEnable) {
    els.httpsProxyEnable.checked = Boolean(settings.https_proxy_enable ?? false);
  }
  if (els.httpsProxy) {
    els.httpsProxy.value = String(settings.https_proxy ?? '');
  }
  if (els.libreLink) {
    els.libreLink.value = String(settings.libre_link ?? '');
  }
  if (els.libreApiKey) {
    els.libreApiKey.value = String(settings.libre_api_key ?? '');
  }
  if (els.autoOpenDirExport) {
    els.autoOpenDirExport.checked = Boolean(settings.auto_open_dir_export ?? true);
  }
  if (els.autoOpenDirExportFile) {
    els.autoOpenDirExportFile.checked = Boolean(settings.auto_open_dir_export ?? true);
  }
  if (els.exportFormat) {
    els.exportFormat.value = String(settings.export_format ?? '');
  }
  if (els.removeRepetitionFileImport) {
    els.removeRepetitionFileImport.checked = Boolean(settings.remove_repetition_file_import ?? false);
  }
  if (els.removeRepetitionAmount) {
    els.removeRepetitionAmount.value = Number(settings.remove_repetition_amount ?? 1);
  }
  if (els.segmentMaxWords) {
    els.segmentMaxWords.value = String(settings.segment_max_words ?? '');
  }
  if (els.segmentMaxChars) {
    els.segmentMaxChars.value = String(settings.segment_max_chars ?? '');
  }
  if (els.segmentSplitOrNewline) {
    els.segmentSplitOrNewline.value = String(settings.segment_split_or_newline ?? 'split');
  }
  if (els.segmentEvenSplit) {
    els.segmentEvenSplit.checked = Boolean(settings.segment_even_split ?? true);
  }
  if (els.segmentLevel) {
    els.segmentLevel.checked = Boolean(settings.segment_level ?? true);
  }
  if (els.wordLevel) {
    els.wordLevel.checked = Boolean(settings.word_level ?? true);
  }
  if (els.useEnModel) {
    els.useEnModel.checked = Boolean(settings.use_en_model ?? true);
  }
  if (els.decodingPreset) {
    els.decodingPreset.value = String(settings.decoding_preset ?? 'beam search');
  }
  if (els.temperature) {
    els.temperature.value = String(settings.temperature ?? '0.0, 0.2, 0.4, 0.6, 0.8, 1.0');
  }
  if (els.bestOf) {
    els.bestOf.value = Number(settings.best_of ?? 3);
  }
  if (els.beamSize) {
    els.beamSize.value = Number(settings.beam_size ?? 3);
  }
  if (els.patience) {
    els.patience.value = Number(settings.patience ?? 1.0);
  }
  if (els.compressionRatioThreshold) {
    els.compressionRatioThreshold.value = Number(settings.compression_ratio_threshold ?? 2.4);
  }
  if (els.logprobThreshold) {
    els.logprobThreshold.value = Number(settings.logprob_threshold ?? -1.0);
  }
  if (els.noSpeechThreshold) {
    els.noSpeechThreshold.value = Number(settings.no_speech_threshold ?? 0.72);
  }
  if (els.suppressTokens) {
    els.suppressTokens.value = String(settings.suppress_tokens ?? '');
  }
  if (els.suppressBlank) {
    els.suppressBlank.checked = Boolean(settings.suppress_blank ?? true);
  }
  if (els.fp16) {
    els.fp16.checked = Boolean(settings.fp16 ?? true);
  }
  if (els.initialPrompt) {
    els.initialPrompt.value = String(settings.initial_prompt ?? '');
  }
  if (els.prefix) {
    els.prefix.value = String(settings.prefix ?? '');
  }
  if (els.maxInitialTimestamp) {
    els.maxInitialTimestamp.value = Number(settings.max_initial_timestamp ?? 1.0);
  }
  if (els.whisperArgs) {
    els.whisperArgs.value = String(settings.whisper_args ?? '');
  }
  if (els.fileSliceStart) {
    els.fileSliceStart.value = String(settings.file_slice_start ?? '');
  }
  if (els.fileSliceEnd) {
    els.fileSliceEnd.value = String(settings.file_slice_end ?? '');
  }
  if (els.autoOpenDirTranslate) {
    els.autoOpenDirTranslate.checked = Boolean(settings.auto_open_dir_translate ?? true);
  }
  if (els.autoOpenDirTranslateFile) {
    els.autoOpenDirTranslateFile.checked = Boolean(settings.auto_open_dir_translate ?? true);
  }
  if (els.autoOpenDirRefinement) {
    els.autoOpenDirRefinement.checked = Boolean(settings.auto_open_dir_refinement ?? true);
  }
  if (els.autoOpenDirRefinementFile) {
    els.autoOpenDirRefinementFile.checked = Boolean(settings.auto_open_dir_refinement ?? true);
  }
  if (els.autoOpenDirAlignment) {
    els.autoOpenDirAlignment.checked = Boolean(settings.auto_open_dir_alignment ?? true);
  }
  if (els.autoOpenDirAlignmentFile) {
    els.autoOpenDirAlignmentFile.checked = Boolean(settings.auto_open_dir_alignment ?? true);
  }
  if (els.recAskConfirmationFirst) {
    els.recAskConfirmationFirst.checked = Boolean(settings.rec_ask_confirmation_first ?? false);
  }
  if (els.closeToTrayOnClose) {
    els.closeToTrayOnClose.checked = Boolean(settings.close_to_tray_on_close ?? true);
  }
  if (els.supressHiddenToTray) {
    els.supressHiddenToTray.checked = Boolean(settings.supress_hidden_to_tray ?? false);
  }
  if (els.supressRecordWarning) {
    els.supressRecordWarning.checked = Boolean(settings.supress_record_warning ?? false);
  }
  if (els.debugRealtimeRecord) {
    els.debugRealtimeRecord.checked = Boolean(settings.debug_realtime_record ?? false);
  }
  if (els.debugTranslate) {
    els.debugTranslate.checked = Boolean(settings.debug_translate ?? false);
  }
  if (els.pathFilterRec) {
    els.pathFilterRec.value = String(settings.path_filter_rec ?? 'auto');
  }
  if (els.pathFilterFileImport) {
    els.pathFilterFileImport.value = String(settings.path_filter_file_import ?? 'auto');
  }
  if (els.colorizePerSegment) {
    els.colorizePerSegment.checked = Boolean(settings.colorize_per_segment ?? true);
  }
  if (els.colorizePerWord) {
    els.colorizePerWord.checked = Boolean(settings.colorize_per_word ?? false);
  }
  if (els.gradientLowConf) {
    els.gradientLowConf.value = String(settings.gradient_low_conf ?? '#FF0000');
  }
  if (els.gradientHighConf) {
    els.gradientHighConf.value = String(settings.gradient_high_conf ?? '#00FF00');
  }
  if (els.tbMwTcAutoScroll) {
    els.tbMwTcAutoScroll.checked = Boolean(settings.tb_mw_tc_auto_scroll ?? true);
  }
  if (els.tbMwTcLimitMax) {
    els.tbMwTcLimitMax.checked = Boolean(settings.tb_mw_tc_limit_max ?? false);
  }
  if (els.tbMwTcLimitMaxPerLine) {
    els.tbMwTcLimitMaxPerLine.checked = Boolean(settings.tb_mw_tc_limit_max_per_line ?? false);
  }
  if (els.tbMwTcMax) {
    els.tbMwTcMax.value = Number(settings.tb_mw_tc_max ?? 300);
  }
  if (els.tbMwTcMaxPerLine) {
    els.tbMwTcMaxPerLine.value = Number(settings.tb_mw_tc_max_per_line ?? 30);
  }
  if (els.tbMwTcFont) {
    els.tbMwTcFont.value = String(settings.tb_mw_tc_font ?? 'TKDefaultFont');
  }
  if (els.tbMwTcFontBold) {
    els.tbMwTcFontBold.checked = Boolean(settings.tb_mw_tc_font_bold ?? false);
  }
  if (els.tbMwTcFontSize) {
    els.tbMwTcFontSize.value = Number(settings.tb_mw_tc_font_size ?? 10);
  }
  if (els.tbMwTcFontColor) {
    els.tbMwTcFontColor.value = String(settings.tb_mw_tc_font_color ?? '#FFFFFF');
  }
  if (els.tbMwTcUseConfColor) {
    els.tbMwTcUseConfColor.checked = Boolean(settings.tb_mw_tc_use_conf_color ?? true);
  }
  if (els.tbMwTlAutoScroll) {
    els.tbMwTlAutoScroll.checked = Boolean(settings.tb_mw_tl_auto_scroll ?? true);
  }
  if (els.tbMwTlLimitMax) {
    els.tbMwTlLimitMax.checked = Boolean(settings.tb_mw_tl_limit_max ?? false);
  }
  if (els.tbMwTlLimitMaxPerLine) {
    els.tbMwTlLimitMaxPerLine.checked = Boolean(settings.tb_mw_tl_limit_max_per_line ?? false);
  }
  if (els.tbMwTlMax) {
    els.tbMwTlMax.value = Number(settings.tb_mw_tl_max ?? 300);
  }
  if (els.tbMwTlMaxPerLine) {
    els.tbMwTlMaxPerLine.value = Number(settings.tb_mw_tl_max_per_line ?? 30);
  }
  if (els.tbMwTlFont) {
    els.tbMwTlFont.value = String(settings.tb_mw_tl_font ?? 'TKDefaultFont');
  }
  if (els.tbMwTlFontBold) {
    els.tbMwTlFontBold.checked = Boolean(settings.tb_mw_tl_font_bold ?? false);
  }
  if (els.tbMwTlFontSize) {
    els.tbMwTlFontSize.value = Number(settings.tb_mw_tl_font_size ?? 10);
  }
  if (els.tbMwTlFontColor) {
    els.tbMwTlFontColor.value = String(settings.tb_mw_tl_font_color ?? '#FFFFFF');
  }
  if (els.tbMwTlUseConfColor) {
    els.tbMwTlUseConfColor.checked = Boolean(settings.tb_mw_tl_use_conf_color ?? true);
  }

  // Hallucination Filters
  if (els.filterRec) els.filterRec.checked = Boolean(settings.filter_rec ?? true);
  if (els.filterRecCaseSensitive) els.filterRecCaseSensitive.checked = Boolean(settings.filter_rec_case_sensitive ?? false);
  if (els.filterRecStrip) els.filterRecStrip.checked = Boolean(settings.filter_rec_strip ?? true);
  if (els.filterRecExactMatch) els.filterRecExactMatch.checked = Boolean(settings.filter_rec_exact_match ?? false);
  if (els.filterRecIgnorePunctuations) els.filterRecIgnorePunctuations.value = String(settings.filter_rec_ignore_punctuations ?? "\"',.?!");
  if (els.filterRecSimilarity) els.filterRecSimilarity.value = Number(settings.filter_rec_similarity ?? 0.75);

  if (els.filterFileImport) els.filterFileImport.checked = Boolean(settings.filter_file_import ?? true);
  if (els.filterFileImportCaseSensitive) els.filterFileImportCaseSensitive.checked = Boolean(settings.filter_file_import_case_sensitive ?? false);
  if (els.filterFileImportStrip) els.filterFileImportStrip.checked = Boolean(settings.filter_file_import_strip ?? true);
  if (els.filterFileImportExactMatch) els.filterFileImportExactMatch.checked = Boolean(settings.filter_file_import_exact_match ?? false);
  if (els.filterFileImportIgnorePunctuations) els.filterFileImportIgnorePunctuations.value = String(settings.filter_file_import_ignore_punctuations ?? "\"',.?!");
  if (els.filterFileImportSimilarity) els.filterFileImportSimilarity.value = Number(settings.filter_file_import_similarity ?? 0.75);

  // Per-language initial prompts
    if (els.enableInitialPrompts) {
      els.enableInitialPrompts.checked = Boolean(settings.enable_initial_prompt ?? false);
      // 自动保存：开关变化即保存
      els.enableInitialPrompts.onchange = async (e) => {
        try {
          await saveInitialPromptsSettings(false);
        } catch (err) {
          console.error('保存引导词开关失败', err);
        }
      };
    }
    // Condition on previous text (web UI)
    if (els.conditionOnPreviousText) {
      els.conditionOnPreviousText.checked = Boolean(settings.condition_on_previous_text ?? true);
      els.conditionOnPreviousText.onchange = async (e) => {
        try {
          const val = Boolean(e.target.checked);
          await apiCall('set_setting', 'condition_on_previous_text', val);
          if (state.data && state.data.settings) state.data.settings.condition_on_previous_text = val;
          renderModelSelectionOverview(state.data || {});
        } catch (err) {
          console.error('保存 condition_on_previous_text 失败', err);
        }
      };
    }
  if (els.initialPromptsContainer) {
    try {
      const map = settings.initial_prompts_map || {};
      buildInitialPromptsUi(els.initialPromptsContainer, map);
    } catch (e) {
      // fallback: clear container
      try { els.initialPromptsContainer.innerHTML = ''; } catch (_e) {}
    }
  }
  syncMirroredFieldState();
}

function renderAbout(data) {
  if (!els.aboutCard) {
    return;
  }

  const about = data?.about || {};
  const settings = data?.settings || {};
  const exportDir = settings.dir_export || about.export_dir || 'auto';
  const logDir = settings.dir_log || 'auto';
  const modelDir = settings.dir_model || about.model_dir || 'auto';
  const mainSize = settings.mw_size || '未知';
  const rows = [
    ['应用', about.name || data?.app_name || 'Speech Translate'],
    ['版本', about.version || data?.version || '未知'],
    ['系统', about.os || [data?.os_name, data?.os_release, data?.os_version].filter(Boolean).join(' ') || '未知'],
    ['CPU', about.cpu || data?.cpu || '未知'],
    ['主窗口', mainSize],
    ['模型目录', modelDir],
    ['导出目录', exportDir],
    ['日志目录', logDir],
    ['日志文件', about.log_file || data?.current_log || '未知'],
  ];

  els.aboutCard.innerHTML = `
    <div class="about-card-grid">
      ${rows
        .map(([label, value]) => `
          <div class="state-row state-row-compact">
            <div class="state-key">${escapeHtml(label)}</div>
            <div class="state-value">${previewValue(value)}</div>
          </div>
        `)
        .join('')}
    </div>
    <div class="inline-actions compact-gap about-card-actions">
      <button type="button" class="btn-with-icon" data-action="save-window-geometry">保存窗口尺寸</button>
      <button type="button" class="btn-with-icon" data-action="show-main-window">显示主窗口</button>
      <button type="button" class="btn-with-icon" data-open-dir="model">打开模型目录</button>
      <button type="button" class="btn-with-icon" data-open-dir="export">打开导出目录</button>
      <button type="button" class="btn-with-icon" data-open-dir="log">打开日志目录</button>
      <button type="button" class="btn-with-icon" data-action="open-current-log">打开当前日志</button>
    </div>
  `;
}

function stopLogAutoRefresh() {
  if (state.logRefreshTimer !== null) {
    window.clearInterval(state.logRefreshTimer);
    state.logRefreshTimer = null;
  }
}

function syncLogAutoRefresh() {
  stopLogAutoRefresh();
  if (!els.autoRefreshLog || !els.autoRefreshLog.checked) {
    return;
  }

  state.logRefreshTimer = window.setInterval(async () => {
    try {
      const payload = await apiCall('refresh_log');
      if (els.currentLog) {
        els.currentLog.value = String(payload?.file || '');
      }
      if (els.logContent) {
        const shouldStickToBottom =
          Boolean(els.autoScrollLog && els.autoScrollLog.checked) ||
          Math.abs((els.logContent.scrollTop + els.logContent.clientHeight) - els.logContent.scrollHeight) < 24;
        els.logContent.textContent = String(payload?.content || '') || '当前日志为空。';
        if (shouldStickToBottom) {
          els.logContent.scrollTop = els.logContent.scrollHeight;
        }
      }
    } catch (error) {
      console.debug('Log refresh skipped', error);
    }
  }, 2500);
}

function renderMainControls(data) {
  const mainUi = data.main_ui || {};
  const recordUi = data.record_ui || {};
  if (els.inputMode) populateSelect(els.inputMode, mainUi.input_options || [], mainUi.selected_input || '');
  if (els.hostAPI) populateSelect(els.hostAPI, recordUi.host_api_options || [], recordUi.selected_host_api || recordUi.host_api || '');
  if (els.mic) populateSelect(els.mic, recordUi.mic_options || [], recordUi.selected_mic || recordUi.mic || '');
  if (els.speaker) populateSelect(els.speaker, recordUi.speaker_options || [], recordUi.selected_speaker || recordUi.speaker || '');
  if (els.backendMain) {
    populateSelect(els.backendMain, mainUi.backend_options || ['whisper', 'faster-whisper'], mainUi.selected_backend || 'faster-whisper', false);
  }
  if (els.modelMain) {
    populateSelect(els.modelMain, mainUi.model_options || [], mainUi.selected_model || '', false);
  }
  if (els.sourceLangMain) populateSelect(els.sourceLangMain, mainUi.source_options || [], mainUi.selected_source || '');
  if (els.targetLangMain) populateSelect(els.targetLangMain, mainUi.target_options || [], mainUi.selected_target || '');
  if (els.translateEngineMain) populateSelect(els.translateEngineMain, mainUi.engine_options || [], mainUi.selected_engine || '');

  if (els.transcribeMain) els.transcribeMain.checked = Boolean(mainUi.transcribe ?? true);
  if (els.translateMain) els.translateMain.checked = Boolean(mainUi.translate ?? true);
  els.mainInputPill.textContent = mainUi.selected_input || '未设置';
  if (els.mainModelPill) {
    const modelLabel = mainUi.selected_model || '未设置';
    const backendLabel = mainUi.selected_backend || '未设置';
    els.mainModelPill.textContent = `${modelLabel} · ${backendLabel}`;
  }
  els.mainLangPill.textContent = `${mainUi.selected_source || '自动'} → ${mainUi.selected_target || '自动'}`;
  els.mainEnginePill.textContent = mainUi.selected_engine || '已关闭';
  if (els.btnLoadMainModel) {
    const hasModel = Array.isArray(mainUi.model_options) && mainUi.model_options.length > 0;
    els.btnLoadMainModel.disabled = !hasModel;
    els.btnLoadMainModel.title = hasModel ? '加载当前实时模型' : '当前后端没有可用模型';
  }
}

async function refreshAudioSourceOptions(hostApiValue, persistSelection = false) {
  const selectedHostApi = hostApiValue ?? (els.hostAPI ? els.hostAPI.value : '');
  const previousMic = els.mic ? els.mic.value : '';
  const previousSpeaker = els.speaker ? els.speaker.value : '';
  const payload = await apiCall('get_audio_source_options', selectedHostApi);

  const hostOptions = payload.host_api_options || [];
  const micOptions = payload.mic_options || [];
  const speakerOptions = payload.speaker_options || [];

  const nextHostApi = payload.selected_host_api || selectedHostApi || '';
  const nextMic = micOptions.includes(previousMic)
    ? previousMic
    : (payload.selected_mic || micOptions[0] || '');
  const nextSpeaker = speakerOptions.includes(previousSpeaker)
    ? previousSpeaker
    : (payload.selected_speaker || speakerOptions[0] || '');

  populateSelect(els.hostAPI, hostOptions, nextHostApi);
  populateSelect(els.mic, micOptions, nextMic);
  populateSelect(els.speaker, speakerOptions, nextSpeaker);

  if (persistSelection) {
    await persistRecordDeviceSelection(
      els.hostAPI ? els.hostAPI.value : nextHostApi,
      els.mic ? els.mic.value : nextMic,
      els.speaker ? els.speaker.value : nextSpeaker
    );
  }
}

function renderImportSettings(data) {
  const importUi = data.import_ui || {};
  const settings = state.data?.settings || data.settings || {};
  setSelectedImportModelEngine(importUi.selected_backend || 'whisper');
  populateSelect(els.modelImport, importUi.model_options || [], importUi.selected_model || '', false);
  populateSelect(els.engineImport, importUi.engine_options || [], importUi.selected_engine || '');
  populateSelect(els.sourceImport, importUi.source_options || [], importUi.selected_source || '');
  populateSelect(els.targetImport, importUi.target_options || [], importUi.selected_target || '');

  els.transcribeImport.checked = Boolean(importUi.transcribe);
  els.translateImport.checked = Boolean(importUi.translate);
  els.importModelPill.textContent = `模型：${importUi.selected_model_label || importUi.selected_model_key || importUi.selected_model || '未下载'}`;
  if (els.importEnginePill) {
    els.importEnginePill.textContent = `引擎：${importUi.selected_engine || '未知'}`;
  }
  if (els.fileImportLanguageState) {
    els.fileImportLanguageState.textContent = `语言：${importUi.selected_source || '自动'} → ${importUi.selected_target || '自动'}`;
  }
  if (els.fileImportExportDir) {
    const exportDir = settings.dir_export ?? 'auto';
    els.fileImportExportDir.textContent = summarizeSettingText(exportDir, 'auto', 30);
  }
  if (els.fileImportExportMeta) {
    const engine = importUi.selected_engine || '未知';
    els.fileImportExportMeta.textContent = `引擎：${engine}`;
  }
  if (els.fileImportExportFormat) {
    els.fileImportExportFormat.textContent = summarizeSettingText(
      settings.export_format,
      '%Y-%m-%d %f {file}/{task-lang}',
      34
    );
  }
  if (els.fileImportExportFormatMeta) {
    const formats = Array.isArray(settings.export_to) ? settings.export_to : [];
    const autoOpen = settings.auto_open_dir_export ? '自动打开目录' : '不自动打开';
    const exportFormats = formats.length > 0
      ? formats.map((item) => String(item).toUpperCase()).join(' / ')
      : '未设置格式';
    els.fileImportExportFormatMeta.textContent = `${exportFormats} · ${autoOpen}`;
  }
  if (els.fileImportSliceRange) {
    els.fileImportSliceRange.textContent = summarizeFileSliceRange(
      settings.file_slice_start,
      settings.file_slice_end
    );
  }
  if (els.fileImportSliceMeta) {
    const splitMode = String(settings.segment_split_or_newline || 'split');
    const limits = [];
    if (String(settings.segment_max_words ?? '').trim()) {
      limits.push(`${settings.segment_max_words}词`);
    }
    if (String(settings.segment_max_chars ?? '').trim()) {
      limits.push(`${settings.segment_max_chars}字`);
    }
    const limitText = limits.length > 0 ? limits.join(' / ') : '不限长';
    els.fileImportSliceMeta.textContent = `${splitMode} · ${limitText}`;
  }
  if (els.fileImportFilterState) {
    els.fileImportFilterState.textContent = settings.filter_file_import ? '已启用' : '已关闭';
  }
  if (els.fileImportFilterMeta) {
    const mode = settings.filter_file_import_exact_match ? '精准匹配' : `相似度 ${Number(settings.filter_file_import_similarity ?? 0.75).toFixed(2)}`;
    els.fileImportFilterMeta.textContent = `${summarizeFilterDictionaryPath(settings.path_filter_file_import)} · ${mode}`;
  }

  if (els.btnLoadModel) {
    const hasModel = Array.isArray(importUi.model_options) && importUi.model_options.length > 0;
    els.btnLoadModel.disabled = !hasModel;
    els.btnLoadModel.title = hasModel ? '加载模型' : '当前后端没有已下载模型';
  }

  // render queued files list if provided by backend
  try {
    const queued = Array.isArray(importUi.queued_files) ? importUi.queued_files : [];
    if (els.fileImportList) {
      updateFileImportListUI(queued);
      state.fileImportQueue = queued;
    }
    renderFileImportProcessingOverview();
  } catch (e) {
    console.debug('Failed to render import queue', e);
  }
}

function baseName(p) {
  if (!p) return '';
  const parts = String(p).split(/[/\\\\]/);
  return parts[parts.length - 1] || p;
}

function updateFileImportListUI(files) {
  if (!els.fileImportList) return;
  const list = Array.isArray(files) ? files : [];
  if (!list || list.length === 0) {
    els.fileImportList.classList.add('is-empty');
    els.fileImportList.innerHTML = `
      <li class="file-queue-empty">
        <div class="file-queue-empty-title">队列为空</div>
        <div class="file-queue-empty-meta">可以拖入音频/视频文件，或点击左上角导入按钮。</div>
      </li>
    `;
    return;
  }

  els.fileImportList.classList.remove('is-empty');

  // 辅助函数：根据状态文字生成对应的 HTML（包含进度条动画）
  const renderStatusCell = (statusText) => {
    const s = String(statusText || '').trim();
    if (!s) return '<span class="status-badge muted">无</span>';

    const sLower = s.toLowerCase();

    // 如果包含失败、错误字眼
    if (sLower.includes('fail') || sLower.includes('error') || sLower.includes('parse')) {
      return `<span class="status-badge error">${escapeHtml(s)}</span>`;
    }

    // 如果已经完成
    if (sLower.includes('transcribed') || sLower.includes('translated') || sLower.includes('refined') || sLower.includes('aligned')) {
      return `<span class="status-badge success">${escapeHtml(s)}</span>`;
    }

    // 如果正在处理中 (显示微型进度条/动画效果)
    if (sLower.includes('please wait') || sLower.includes('processing') || sLower.includes('re-transcribing')) {
      return `
        <div class="file-queue-processing">
          <div class="mini-spinner"></div>
          <span class="status-badge active">处理中...</span>
        </div>
      `;
    }

    // 默认情况 (Waiting)
    return `<span class="status-badge muted">${escapeHtml(s)}</span>`;
  };

  els.fileImportList.innerHTML = list
    .map((item, idx) => {
      let name = '';
      let statusStr = '';

      if (Array.isArray(item)) {
        name = String(item[0] || '');
        statusStr = String(item[1] || '');
      } else if (item && typeof item === 'object') {
        name = item.name || baseName(item.path || '');
        statusStr = item.status || '';
      }

      // 解析来自后端的组合状态 (用逗号分隔的)
      // 例如: "Transcribing please wait..., Waiting" 
      const parts = statusStr.split(',').map(p => p.trim());
      const tcStatus = parts[0] || 'Waiting';
      const tlStatus = parts[1] || 'Waiting'; // 如果没有翻译部分，默认显示等待

      return `
      <li class="file-queue-item" data-index="${idx}">
        <span class="file-queue-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
        <span class="file-queue-status-tc">${renderStatusCell(tcStatus)}</span>
        <span class="file-queue-status-tl">${renderStatusCell(tlStatus)}</span>
        <div class="file-queue-actions">
          <button class="btn-icon btn-icon-sm" data-action="remove-file-from-queue" data-index="${idx}" title="删除"><span class="btn-glyph glyph-trash" aria-hidden="true"></span></button>
        </div>
      </li>`;
    })
    .join('');
}
async function refreshFileProcessingState() {
  if (!window.pywebview || !window.pywebview.api) return;
  try {
    const res = await apiCall('get_file_processing_state');
    if (!res || res.ok === false) return;
    state.fileProcessingState = res;
    const files = Array.isArray(res.files) ? res.files : [];
    state.fileImportQueue = files;
    updateFileImportListUI(files);

    // synchronize import start/stop button based on backend 'active' flag
    try {
      if (typeof res.active !== 'undefined') {
        syncImportButton(Boolean(res.active));
      }
    } catch (e) {
      console.debug('syncImportButton failed', e);
    }

    const total = Number(res.files_total || (files ? files.length : 0)) || 0;
    const completed = Number(res.files_completed || 0) || 0;
    const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
    if (els.globalTaskProgressText) els.globalTaskProgressText.textContent = `${pct}%`;
    if (els.globalTaskProgressFill) els.globalTaskProgressFill.style.width = `${pct}%`;
    if (els.globalTaskProgressWrap) els.globalTaskProgressWrap.style.display = total > 0 ? 'inline-flex' : 'none';
    renderFileImportProcessingOverview();
  } catch (err) {
    console.debug('Failed to refresh file processing state', err);
  }
}

function renderModelSelectionOverview(data) {
  const summary = resolveModelRuntimeSummary(data);

  if (els.modelSelectionRuntime) {
    els.modelSelectionRuntime.textContent = summary.runtimeStateLabel;
  }
  if (els.modelSelectionRuntimeMeta) {
    els.modelSelectionRuntimeMeta.textContent = summary.runtimeMetaLabel;
  }
}

function renderFileImportProcessingOverview() {
  const summary = resolveFileProcessingSummary();

  if (els.fileImportQueueCount) {
    els.fileImportQueueCount.textContent = summary.queueLabel;
  }
  if (els.fileImportProcessingState) {
    els.fileImportProcessingState.textContent = summary.stateLabel;
  }
}

function previewValue(value) {
  if (value === null || value === undefined) {
    return '<span class="alert">null</span>';
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false';
  }
  if (Array.isArray(value)) {
    return escapeHtml(JSON.stringify(value));
  }
  if (typeof value === 'object') {
    return escapeHtml(JSON.stringify(value, null, 2));
  }
  return escapeHtml(String(value));
}

function summarizeExportFormats(settings) {
  const formats = Array.isArray(settings?.export_to) ? settings.export_to : [];
  return formats.length > 0 ? formats.map((item) => String(item).toUpperCase()).join(' / ') : '未设置';
}

function summarizeInitialPromptState(settings) {
  const enabled = Boolean(settings?.enable_initial_prompt);
  const map = settings?.initial_prompts_map && typeof settings.initial_prompts_map === 'object'
    ? settings.initial_prompts_map
    : {};
  const customCount = Object.keys(map).filter((key) => String(map[key] || '').trim()).length;
  return `${enabled ? '已启用' : '未启用'} · 自定义 ${customCount} 项`;
}

function renderSettingsToolbarOverview(data) {
  const settings = data?.settings || {};

  syncToolbarMirrorValues([
    [els.httpProxyToolbar, els.httpProxy, ''],
    [els.httpsProxyToolbar, els.httpsProxy, ''],
    [els.libreLinkToolbar, els.libreLink, ''],
    [els.libreApiKeyToolbar, els.libreApiKey, ''],
    [els.exportFormatToolbar, els.exportFormat, '%Y-%m-%d %f {file}/{task-lang}'],
    [els.segmentMaxWordsToolbar, els.segmentMaxWords, ''],
    [els.segmentMaxCharsToolbar, els.segmentMaxChars, ''],
    [els.segmentSplitOrNewlineToolbar, els.segmentSplitOrNewline, 'Split'],
    [els.decodingPresetToolbar, els.decodingPreset, 'beam search'],
    [els.temperatureToolbar, els.temperature, '0.0, 0.2, 0.4, 0.6, 0.8, 1.0'],
    [els.bestOfToolbar, els.bestOf, '3'],
    [els.beamSizeToolbar, els.beamSize, '3'],
    [els.noSpeechThresholdToolbar, els.noSpeechThreshold, '0.72'],
    [els.logprobThresholdToolbar, els.logprobThreshold, '-1.0'],
    [els.patienceToolbar, els.patience, '1.0'],
    [els.compressionRatioThresholdToolbar, els.compressionRatioThreshold, '2.4'],
    [els.suppressTokensToolbar, els.suppressTokens, ''],
    [els.modelDevicePreferenceToolbar, els.modelDevicePreference, 'auto'],
    [els.transcribeRateToolbar, els.transcribeRate, '300'],
  ]);
  syncToolbarMirrorChecks([
    [els.httpProxyEnableToolbar, els.httpProxyEnable, false],
    [els.httpsProxyEnableToolbar, els.httpsProxyEnable, false],
    [els.autoOpenDirExportToolbar, els.autoOpenDirExport, true],
    [els.exportTxtToolbar, els.exportTxt, true],
    [els.exportSrtToolbar, els.exportSrt, true],
    [els.exportVttToolbar, els.exportVtt, true],
    [els.exportJsonToolbar, els.exportJson, true],
    [els.exportAssToolbar, els.exportAss, true],
    [els.exportCsvToolbar, els.exportCsv, false],
    [els.exportTsvToolbar, els.exportTsv, false],
    [els.exportMp4Toolbar, els.exportMp4, false],
    [els.recAskConfirmationFirstToolbar, els.recAskConfirmationFirst, true],
    [els.supressHiddenToTrayToolbar, els.supressHiddenToTray, false],
    [els.useEnModelToolbar, els.useEnModel, true],
    [els.fp16Toolbar, els.fp16, true],
    [els.suppressBlankToolbar, els.suppressBlank, true],
    [els.useTempAltToolbar, els.useTempAlt, false],
    [els.keepTempToolbar, els.keepTemp, false],
    [els.fileUseOfficialWhisperToolbar, els.fileUseOfficialWhisper, false],
    [els.supressRecordWarningToolbar, els.supressRecordWarning, false],
    [els.debugRealtimeRecordToolbar, els.debugRealtimeRecord, false],
    [els.debugTranslateToolbar, els.debugTranslate, false],
    [els.segmentEvenSplitToolbar, els.segmentEvenSplit, true],
    [els.segmentLevelToolbar, els.segmentLevel, true],
    [els.wordLevelToolbar, els.wordLevel, true],
  ]);
  if (els.hostAPIToolbar && els.hostAPI) {
    populateSelect(
      els.hostAPIToolbar,
      Array.from(els.hostAPI.options || []).map((option) => option.value),
      els.hostAPI.value || ''
    );
  }
  if (els.decodePresetKpi) {
    els.decodePresetKpi.textContent = String(settings.decoding_preset || 'greedy');
  }
  if (els.decodeTemperatureKpi) {
    const temperature = String(settings.temperature ?? '').trim();
    els.decodeTemperatureKpi.textContent = temperature || '默认';
  }
  if (els.decodeOutputKpi) {
    const precision = settings.fp16 ? 'FP16' : 'FP32';
    els.decodeOutputKpi.textContent = `${settings.use_en_model ? '.en' : '多语'} · ${precision}`;
  }
  if (els.seleniumModeKpi) {
    const levelMap = {
      0: '标准窗口',
      1: '紧凑模式',
      2: '紧凑+低干扰',
      3: '最小化干扰',
    };
    const rawLevel = Number(settings.selenium_compact_level ?? 2);
    els.seleniumModeKpi.textContent = levelMap[rawLevel] || '紧凑+低干扰';
  }
  if (els.seleniumZorderKpi) {
    const zOrderMap = {
      'behind-main': '主窗体后层',
      bottom: '全局底层',
      normal: '常规层级',
    };
    els.seleniumZorderKpi.textContent = zOrderMap[String(settings.selenium_z_order_mode || 'behind-main')] || '主窗体后层';
  }
  if (els.seleniumAutoCloseKpi) {
    els.seleniumAutoCloseKpi.textContent = settings.selenium_auto_close_on_task_done ? '任务完成后' : '手动关闭';
  }
}

function renderTaskRuntimePills(data) {
  const settings = data?.settings || {};
  const modelSummary = resolveModelRuntimeSummary(data);
  const exportText = settings.dir_export && settings.dir_export !== 'auto' ? settings.dir_export : '导出:auto';
  const logText = [
    `日志:${String(settings.log_level || 'INFO').toUpperCase()}`,
    settings.auto_refresh_log ? '自动刷新' : '手动刷新',
  ].join(' · ');

  if (els.taskRuntimeModelPill) {
    els.taskRuntimeModelPill.textContent = `模型：${modelSummary.taskPillLabel}`;
  }
  if (els.taskRuntimeExportPill) {
    els.taskRuntimeExportPill.textContent = `导出：${exportText}`;
  }
  if (els.taskRuntimeLogPill) {
    els.taskRuntimeLogPill.textContent = logText;
  }
}

function renderDetachedWindowOverview(data) {
  const settings = data?.settings || {};
  const detachedConfig = data?.detached_config || {};
  const tc = detachedConfig.tc || {};
  const tl = detachedConfig.tl || {};
  const resolveSettingValue = (settingKey, configValue) => {
    const fallback = DETACHED_WINDOW_PANEL_DEFAULTS[settingKey];
    const configured = settings[settingKey];
    if (configured !== undefined) {
      return configured;
    }
    if (configValue !== undefined) {
      return configValue;
    }
    return fallback;
  };
  const modeEntries = [
    ['tc', tc, els.detachedTcState, els.detachedTcGeometry],
    ['tl', tl, els.detachedTlState, els.detachedTlGeometry],
  ];
  for (const [mode, config, stateNode, geometryNode] of modeEntries) {
    if (stateNode) {
      const open = Boolean(state.detachedOpen[mode]);
      stateNode.textContent = open ? '已打开' : '未打开';
    }
    if (geometryNode) {
      const geometry = mode === 'tc'
        ? String(resolveSettingValue('ex_tc_geometry', config.geometry))
        : String(resolveSettingValue('ex_tl_geometry', config.geometry));
      const onTop = mode === 'tc'
        ? Boolean(resolveSettingValue('ex_tc_always_on_top', config.always_on_top))
        : Boolean(resolveSettingValue('ex_tl_always_on_top', config.always_on_top));
      const clickThrough = mode === 'tc'
        ? Boolean(resolveSettingValue('ex_tc_click_through', config.click_through))
        : Boolean(resolveSettingValue('ex_tl_click_through', config.click_through));
      geometryNode.textContent = `${geometry} · ${onTop ? '置顶' : '常规'} · ${clickThrough ? '穿透' : '可交互'}`;
    }
  }
}

function renderSettingsPanelSummaries(data) {
  const settings = data?.settings || {};
  const runtime = data?.runtime_model || {};
  const importUi = data?.import_ui || {};
  const recordUi = data?.record_ui || {};
  const taskActive = Boolean(data?.task_state?.active);
  const panelSummaryMap = new Map([
    ['系统与日志', [
      `日志 ${String(settings.log_level || 'INFO').toUpperCase()}`,
      settings.auto_refresh_log ? '自动刷新' : '手动刷新',
      settings.dir_log || 'log:auto',
    ]],
    ['任务与环境', [
      runtime.loaded ? `模型 ${runtime.key || importUi.selected_model || '已加载'}` : '模型未加载',
      taskActive ? '任务进行中' : '当前空闲',
    ]],
    ['翻译网络与 LibreTranslate', [
      settings.http_proxy_enable || settings.https_proxy_enable ? '代理已启用' : '无代理',
      settings.libre_link ? 'LibreTranslate 已配置' : 'LibreTranslate 未配置',
    ]],
    ['导出与切分', [
      summarizeExportFormats(settings),
      settings.auto_open_dir_export ? '自动打开目录' : '不自动打开',
    ]],
    ['主界面文本显示', [
      settings.colorize_per_word ? '按词着色' : settings.colorize_per_segment ? '按段着色' : '纯文本',
      `TC ${settings.tb_mw_tc_font_size || 10}px · TL ${settings.tb_mw_tl_font_size || 10}px`,
    ]],
    ['运行与保护策略', [
      settings.rec_ask_confirmation_first ? '录制前确认' : '直接录制',
      settings.close_to_tray_on_close ? '关闭即托盘' : '关闭即退出',
    ]],
    ['Whisper 解码参数', [
      String(settings.decoding_preset || 'beam search'),
      `温度 ${String(settings.temperature ?? '') || '默认'}`,
    ]],
    ['Selenium 翻译窗口', [
      `级别 ${settings.selenium_compact_level ?? 2}`,
      String(settings.selenium_z_order_mode || 'behind-main'),
    ]],
    [DETACHED_SETTINGS_SECTION_TITLE, [
      `TC ${settings.ex_tc_geometry || '900x240'}`,
      `TL ${settings.ex_tl_geometry || '900x240'}`,
    ]],
    ['录制设置（麦克风 / 扬声器）', [
      `输入 ${recordUi.input || settings.input || 'mic'}`,
      `间隔 ${recordUi.transcribe_rate ?? settings.transcribe_rate ?? 300}ms`,
    ]],
    ['幻觉过滤 (Hallucination Filter)', [
      settings.filter_rec ? '实时过滤开' : '实时过滤关',
      settings.filter_file_import ? '文件过滤开' : '文件过滤关',
    ]],
    ['引导词', [
      summarizeInitialPromptState(settings),
      settings.condition_on_previous_text ? '沿用历史上下文' : '不沿用历史上下文',
    ]],
  ]);

  for (const panel of getSettingsPanels()) {
    const summary = panel.querySelector('summary');
    if (!summary) {
      continue;
    }
    const { title, meta: existingMeta } = ensureSettingsPanelSummaryStructure(summary);
    const summaryItems = panelSummaryMap.get(title);
    let meta = existingMeta;
    if (!summaryItems || summaryItems.length === 0) {
      if (meta) {
        meta.remove();
      }
      continue;
    }
    if (!meta) {
      meta = document.createElement('span');
      meta.className = 'settings-panel-meta';
      summary.appendChild(meta);
    }
    meta.textContent = summaryItems.filter(Boolean).join(' · ');
  }
}

function escapeStyleValue(value) {
  return String(value ?? '').replaceAll('"', '&quot;');
}

function buildPreviewHtml({ text, settings, mode }) {
  const font = settings?.[`tb_mw_${mode}_font`] ?? 'TKDefaultFont';
  const fontSize = Number(settings?.[`tb_mw_${mode}_font_size`] ?? 10);
  const fontColor = settings?.[`tb_mw_${mode}_font_color`] ?? '#FFFFFF';
  const fontBold = Boolean(settings?.[`tb_mw_${mode}_font_bold`] ?? false);
  const useConfColor = Boolean(settings?.[`tb_mw_${mode}_use_conf_color`] ?? true);
  const limitMax = Boolean(settings?.[`tb_mw_${mode}_limit_max`] ?? false);
  const limitMaxPerLine = Boolean(settings?.[`tb_mw_${mode}_limit_max_per_line`] ?? false);
  const maxChars = Number(settings?.[`tb_mw_${mode}_max`] ?? 300);
  const maxPerLine = Number(settings?.[`tb_mw_${mode}_max_per_line`] ?? 30);
  const lowColor = String(settings?.gradient_low_conf ?? '#FF0000');
  const highColor = String(settings?.gradient_high_conf ?? '#00FF00');
  const words = String(text || '').split(/\s+/).filter(Boolean);
  const visibleWords = limitMax ? words.slice(0, Math.max(1, Math.min(words.length, maxChars > 0 ? maxChars : words.length))) : words;
  const renderedWords = visibleWords.length > 0 ? visibleWords : [text || 'Preview text'];
  const lines = [];
  for (let i = 0; i < renderedWords.length; i += Math.max(1, maxPerLine || 1)) {
    lines.push(renderedWords.slice(i, i + Math.max(1, maxPerLine || 1)));
  }
  const pieces = lines.map((line) => {
    const lineHtml = line.map((word, wordIndex) => {
      const color = useConfColor
        ? `linear-gradient(90deg, ${escapeStyleValue(lowColor)}, ${escapeStyleValue(highColor)})`
        : escapeStyleValue(fontColor);
      if (useConfColor) {
        return `<span style="background: ${color}; -webkit-background-clip: text; background-clip: text; color: transparent;">${escapeHtml(word)}</span>`;
      }
      return `<span style="color: ${color}">${escapeHtml(word)}</span>`;
    }).join(' ');
    return lineHtml;
  }).join(limitMaxPerLine ? '<br>' : ' ');
  return `<div style="font-family: ${escapeStyleValue(font)}; font-size: ${fontSize}px; font-weight: ${fontBold ? 'bold' : 'normal'}; line-height: 1.55;">${pieces || escapeHtml(text || 'Preview text')}</div>`;
}

function renderLiveOutputNode(el, htmlValue, liveText, emptyMessage, previewText, settings, mode) {
  if (!el) {
    return false;
  }
  const html = htmlValue || '';
  const plain = liveText || '';
  if (html.trim()) {
    el.classList.remove('is-preview');
    el.classList.remove('is-empty');
    el.innerHTML = html;
    return true;
  }
  if (plain.trim()) {
    el.classList.remove('is-preview');
    el.classList.remove('is-empty');
    el.textContent = plain;
    return true;
  }
  el.classList.add('is-preview');
  el.classList.add('is-empty');
  el.textContent = emptyMessage || '等待输入。';
  return false;
}

function renderLiveOutputs(data) {
  const live = data.live_ui || {};
  const settings = data.settings || {};

  const applyOutputScroll = (el, enabled) => {
    if (!el || !enabled) {
      return;
    }
    el.scrollTop = el.scrollHeight;
  };

  const tcHasLive = renderLiveOutputNode(
    els.mainTranscribedOutput,
    live.main_transcribed_html,
    live.main_transcribed_text || '',
    '等待语音输入',
    settings,
    'tc'
  );
  const tlHasLive = renderLiveOutputNode(
    els.mainTranslatedOutput,
    live.main_translated_html,
    live.main_translated_text || '',
    '等待翻译输出',
    settings,
    'tl'
  );
  if (els.mainTranscribedLabel) {
    els.mainTranscribedLabel.classList.toggle('is-live', tcHasLive);
    els.mainTranscribedLabel.textContent = '转写';
  }
  if (els.mainTranslatedLabel) {
    els.mainTranslatedLabel.classList.toggle('is-live', tlHasLive);
    els.mainTranslatedLabel.textContent = '翻译';
  }
  applyOutputScroll(els.mainTranscribedOutput, Boolean(settings.tb_mw_tc_auto_scroll ?? true));
  applyOutputScroll(els.mainTranslatedOutput, Boolean(settings.tb_mw_tl_auto_scroll ?? true));
  
  // Update independent detached windows if they are open
  if (typeof pywebview !== 'undefined' && pywebview.api) {
    const tcHtml = live.detached_transcribed_html || live.detached_transcribed_text || '';
    const tlHtml = live.detached_translated_html || live.detached_translated_text || '';
    
    if (state.detachedOpen.tc && tcHtml) {
      pywebview.api.update_detached_content('tc', tcHtml).then((result) => {
        if (result && result.status === 'missing') {
          state.detachedOpen.tc = false;
        }
      }).catch(() => {
        state.detachedOpen.tc = false;
      });
    }
    
    if (state.detachedOpen.tl && tlHtml) {
      pywebview.api.update_detached_content('tl', tlHtml).then((result) => {
        if (result && result.status === 'missing') {
          state.detachedOpen.tl = false;
        }
      }).catch(() => {
        state.detachedOpen.tl = false;
      });
    }
  }
}

function renderRecordSettings(data) {
  const recordUi = data.record_ui || {};
  const mic = recordUi.mic_device || {};
  const speaker = recordUi.speaker_device || {};

  if (els.verboseRecord) els.verboseRecord.value = String(Boolean(recordUi.verbose_record));
  if (els.modelDevicePreference) {
    populateSelect(
      els.modelDevicePreference,
      recordUi.model_device_options || ['auto', 'cpu', 'cuda'],
      recordUi.model_device_preference || 'auto'
    );
  }
  if (els.transcribeRate) els.transcribeRate.value = recordUi.transcribe_rate ?? 300;
  if (els.separateWith) els.separateWith.value = recordUi.separate_with ?? '\n';
  if (els.useTemp) els.useTemp.checked = !Boolean(recordUi.use_temp);
  if (els.useTempAlt) els.useTempAlt.checked = Boolean(recordUi.use_temp);
  if (els.keepTemp) els.keepTemp.checked = Boolean(recordUi.keep_temp);
  if (els.fileUseOfficialWhisper) {
    els.fileUseOfficialWhisper.checked = Boolean(recordUi.file_use_official_whisper);
  }

  const fillDevice = (prefix, device) => {
    const setValue = (suffix, value) => {
      const node = els[`${prefix}${suffix}`];
      if (node) {
        node.value = value ?? '';
      }
      // If this is a threshold dB slider, also update the adjacent value display node.
      if (suffix === 'ThresholdDb') {
        const valueNode = els[`${prefix}ThresholdDbValue`];
        if (valueNode) {
          const n = Number(value);
          valueNode.textContent = `${Number.isFinite(n) ? n.toFixed(1) : ''} dB`;
        }
      }
    };
    const setChecked = (suffix, value) => {
      const node = els[`${prefix}${suffix}`];
      if (node) {
        node.checked = Boolean(value);
      }
    };

    setValue('SampleRate', device.sample_rate);
    setValue('ChunkSize', device.chunk_size);
    setValue('Channels', device.channels);
    setValue('MinInputLength', device.min_input);
    setValue('MaxBuffer', device.max_buffer);
    setValue('MaxSentences', device.max_sentences);
    setValue('ThresholdAutoLevel', device.threshold_auto_level);
    setValue('ThresholdSileroMin', device.threshold_silero_min);
    setValue('ThresholdDb', device.threshold_db);
    setChecked('AutoSampleRate', device.auto_sample_rate);
    setChecked('AutoChannels', device.auto_channels);
    setChecked('NoLimit', device.no_limit);
    setChecked('ThresholdEnable', device.threshold_enable);
    setChecked('ThresholdAuto', device.threshold_auto);
    setChecked('ThresholdAutoSilero', device.threshold_auto_silero);
    setChecked('AutoBreakBuffer', device.auto_break_buffer);
  };

  fillDevice('mic', mic);
  fillDevice('speaker', speaker);

  if (els.recordInputPill) {
    els.recordInputPill.textContent = `输入：${recordUi.input || '未知'}`;
  }
  if (els.recordModePill) {
    els.recordModePill.textContent = `模式：${recordUi.use_temp ? '临时 wav' : 'Numpy 数组'}`;
  }
  renderRecordingVisualizer(state.data?.recording_state || null, recordUi);
}

function renderRecordingVisualizer(recordingState, recordUi = null) {
  const visualizerCard = els.recordVisualizerCard;
  if (!visualizerCard) {
    return;
  }

  const toFiniteNumber = (value) => {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  };

  const recordSettings = recordUi || state.data?.record_ui || {};
  visualizerCard.classList.remove('is-hidden');

  const active = Boolean(recordingState?.active);
  const currentDb = toFiniteNumber(recordingState?.last_db);
  const fallbackThreshold = String(recordingState?.device || '').toLowerCase() === 'speaker'
    ? recordSettings.speaker_device?.threshold_db
    : recordSettings.mic_device?.threshold_db;
  const thresholdDb = toFiniteNumber(recordingState?.threshold_db)
    ?? toFiniteNumber(fallbackThreshold)
    ?? -20;
  const minDb = -61;
  const maxDb = 1;
  const normalizedLevel = Number.isFinite(currentDb)
    ? Math.max(0, Math.min(1, (currentDb - minDb) / (maxDb - minDb)))
    : 0;
  const normalizedThreshold = Number.isFinite(thresholdDb)
    ? Math.max(0, Math.min(1, (thresholdDb - minDb) / (maxDb - minDb)))
    : 0;

  if (els.recordVisualizerFill) {
    els.recordVisualizerFill.style.width = `${Math.round(normalizedLevel * 100)}%`;
  }
  visualizerCard.style.setProperty('--record-level', `${Math.round(normalizedLevel * 100)}%`);
  if (els.recordVisualizerThreshold) {
    els.recordVisualizerThreshold.style.left = `${Math.round(normalizedThreshold * 100)}%`;
  }
  if (els.recordVisualizerLabel) {
    els.recordVisualizerLabel.textContent = active
      ? (Number.isFinite(currentDb) ? '监听中' : '等待输入')
      : '等待录制';
  }
  if (els.recordVisualizerDb) {
    els.recordVisualizerDb.textContent = Number.isFinite(currentDb) ? `${currentDb.toFixed(1)} dB` : '- dB';
  }
  if (els.recordVisualizerThresholdText) {
    els.recordVisualizerThresholdText.textContent = Number.isFinite(thresholdDb)
      ? `阈值 ${thresholdDb.toFixed(1)} dB`
      : '阈值 - dB';
  }
  const stateLabel = active
    ? (Number.isFinite(currentDb) ? 'active' : 'waiting')
    : 'idle';
  visualizerCard.dataset.state = stateLabel;
  const visualizerTitle = [
    els.recordVisualizerLabel?.textContent || '输入电平',
    els.recordVisualizerDb?.textContent || '- dB',
    els.recordVisualizerThresholdText?.textContent || '阈值 - dB',
  ].join(' · ');
  visualizerCard.title = visualizerTitle;
  visualizerCard.setAttribute('aria-label', visualizerTitle);
}

function renderTaskState(task) {
  if (!els.taskCard) {
    return;
  }

  const progress = Math.max(0, Math.min(100, Number(task?.progress) || 0));
  const active = Boolean(task?.active);
  const title = task?.title || (active ? '执行中' : '空闲');
  const message = task?.message || (active ? '处理中...' : '等待操作。');
  const badgeText = task?.error ? '错误' : task?.finished ? '已完成' : active ? '执行中' : '空闲';
  const taskRows = Array.isArray(task?.rows) ? task.rows : [];

  if (els.taskBadge) {
    els.taskBadge.textContent = badgeText;
    els.taskBadge.classList.toggle('is-active', active && !task?.finished && !task?.error);
    els.taskBadge.classList.toggle('is-error', Boolean(task?.error));
    els.taskBadge.classList.toggle('is-finished', Boolean(task?.finished) && !task?.error);
  }
  if (els.taskTitle) {
    els.taskTitle.textContent = title;
  }
  if (els.taskMessage) {
    els.taskMessage.textContent = message;
  }
  if (els.taskProgressText) {
    els.taskProgressText.textContent = `${progress.toFixed(0)}%`;
  }
  if (els.taskProgressFill) {
    els.taskProgressFill.style.width = `${progress}%`;
  }

  if (!task) {
    els.taskCard.innerHTML = '<div class="state-row"><div class="state-key">任务</div><div class="state-value">空闲</div></div>';
    return;
  }

  const summaryRows = [
    ['状态', badgeText],
    ['标题', task.title || title],
    ['进度', `${progress.toFixed(0)}%`],
    ['消息', task.message || message],
  ];

  const infoHtml = `
    <div class="task-summary-grid">
      ${summaryRows
        .map(([label, value]) => `
          <div class="state-row state-row-compact">
            <div class="state-key">${escapeHtml(label)}</div>
            <div class="state-value">${previewValue(value)}</div>
          </div>
        `)
        .join('')}
    </div>
  `;

  const fileRowsHtml = taskRows.length > 0
    ? `
      <div class="task-rows-head">文件状态</div>
      <div class="task-rows-list">
        ${taskRows
          .map(([fileName, status]) => `
            <div class="task-row-item">
              <div class="task-row-file">${escapeHtml(fileName)}</div>
              <div class="task-row-status">${escapeHtml(status)}</div>
            </div>
          `)
          .join('')}
      </div>
    `
    : '';

  els.taskCard.innerHTML = `${infoHtml}${fileRowsHtml}`;
}

function renderGlobalStatusBar(task, data, recordingState = null) {
  const progress = Math.max(0, Math.min(100, Number(task?.progress) || 0));
  const active = Boolean(task?.active);
  const hasError = Boolean(task?.error);
  const isFinished = Boolean(task?.finished);
  const runtime = data?.runtime_model || {};
  const modelSummary = resolveModelRuntimeSummary(data);
  const modelKey = modelSummary.effectiveModelKey;
  const loading = modelSummary.runtimeLoading;
  const loaded = modelSummary.runtimeLoaded;
  const runtimeElapsed = Math.max(0, Number(runtime.elapsed_seconds) || 0);

  const normalizeMessage = (msg) => {
    const raw = String(msg || '').trim();
    if (!raw) return '';
    if (raw === 'Model not preloaded') return '模型未预加载';
    if (raw === '模型未预加载') return '模型未预加载';
    if (raw.startsWith('Model ready:')) return `模型就绪：${raw.slice('Model ready:'.length).trim()}`;
    if (raw.startsWith('Loading model cache for')) return `正在加载模型：${raw.slice('Loading model cache for'.length).trim()}`;
    if (raw.startsWith('Model load failed:')) return `模型加载失败：${raw.slice('Model load failed:'.length).trim()}`;
    return raw;
  };

  const recStatus = String(recordingState?.status || '').trim();
  const recActive = Boolean(recordingState?.active);
  if (els.globalStatusbar) {
    els.globalStatusbar.classList.toggle('is-recording', Boolean(recActive));
  }

  const mapRecordingPhase = (statusText) => {
    const s = String(statusText || '').toLowerCase();
    if (!s) return '录制中';
    if (s.includes('transcribing')) return '转写中';
    if (s.includes('translating')) return '翻译中';
    if (s.includes('waiting')) return '等待中';
    if (s.includes('paused')) return '已暂停';
    if (s.includes('stopping')) return '停止中';
    if (s.includes('recording')) return '录制中';
    return '录制中';
  };

  const taskState = recActive
    ? mapRecordingPhase(recStatus)
    : (hasError ? '错误' : active ? '执行中' : isFinished ? '已完成' : '空闲');
  const idleTaskState = hasError ? '错误' : '空闲';
  const recordingSummary = recActive
    ? [
        isCompactViewport()
          ? `${recordingState?.timer || '--:--:--'} · ${recordingState?.sentences || '0'}句`
          : `计时 ${recordingState?.timer || '--:--:--'}`,
        isCompactViewport()
          ? null
          : `缓冲 ${recordingState?.buffer || `${recordingState?.buffer_seconds || 0}/${recordingState?.max_buffer_seconds || 0} sec`}`,
        isCompactViewport()
          ? null
          : `句子 ${recordingState?.sentences || '0'}`,
      ].filter(Boolean).join(' | ')
    : '';

  const taskMessage = recActive
    ? recordingSummary
    : active
      ? (task?.message || '正在处理任务...')
      : (hasError ? String(task?.error || '任务异常') : '等待任务');
  const modelState = loaded
    ? `已加载 (${modelKey})`
    : loading
      ? `加载中 (${modelKey})`
      : `未加载 (${modelKey})`;

  if (els.globalModelState) {
    els.globalModelState.textContent = modelState;
  }
  if (els.globalModelMeta) {
    const runtimeMsg = normalizeMessage(runtime.message);
    const modelMeta = loaded
      ? (runtimeMsg || '模型缓存可用')
      : loading
        ? `${runtimeMsg || '正在准备模型缓存'}${runtimeElapsed > 0 ? ` · 已耗时 ${runtimeElapsed.toFixed(0)}s` : ''}`
        : (runtimeMsg && runtimeMsg.includes('失败') ? runtimeMsg : '可点击 Load Model 预加载');
    els.globalModelMeta.textContent = modelMeta;
  }

  if (els.globalTaskState) {
    els.globalTaskState.textContent = (active || recActive) ? taskState : idleTaskState;
  }
  if (els.globalTaskMessage) {
    els.globalTaskMessage.textContent = taskMessage;
  }
  if (els.globalTaskProgressText) {
    els.globalTaskProgressText.textContent = recActive ? '' : `${progress.toFixed(0)}%`;
  }
  if (els.globalTaskProgressFill) {
    els.globalTaskProgressFill.style.width = recActive ? '0%' : `${progress}%`;
  }
  if (els.globalTaskProgressWrap) {
    els.globalTaskProgressWrap.style.display = recActive ? 'none' : 'inline-flex';
  }

  if (els.realtimeModelState) {
    els.realtimeModelState.textContent = loaded
      ? modelKey
      : loading
        ? `${modelKey} 加载中`
        : '未加载';
  }
  if (els.realtimeModelMeta) {
    const globalModelMeta = els.globalModelMeta ? els.globalModelMeta.textContent : '';
    els.realtimeModelMeta.textContent = globalModelMeta
      .replace('模型缓存可用', '缓存可用')
      .replace('可点击 Load Model 预加载', '可预加载')
      .replace('正在准备模型缓存', '准备缓存')
      .replace('模型未预加载', '未预加载');
  }
  if (els.realtimeTaskState) {
    els.realtimeTaskState.textContent = (active || recActive) ? taskState : idleTaskState;
  }
  if (els.realtimeTaskMessage) {
    const realtimeBufferText = recordingState?.buffer
      || `${recordingState?.buffer_seconds || 0}/${recordingState?.max_buffer_seconds || 0}s`;
    const realtimeTaskMeta = recActive
      ? [
          `缓冲 ${realtimeBufferText}`,
          `已收 ${recordingState?.sentences || '0'} 段`,
        ].filter(Boolean).join(' · ')
      : String(taskMessage || '').replace(/^等待任务$/u, '等待开始');
    els.realtimeTaskMessage.textContent = realtimeTaskMeta;
  }
  if (els.realtimeRecordingTimer) {
    els.realtimeRecordingTimer.textContent = recordingState?.timer || '--:--:--';
  }
  if (els.realtimeRecordingBuffer) {
    const bufferText = recordingState?.buffer || `${recordingState?.buffer_seconds || 0}/${recordingState?.max_buffer_seconds || 0}s`;
    els.realtimeRecordingBuffer.textContent = `缓冲 ${bufferText}`;
  }
  if (els.realtimeRecordingSentences) {
    els.realtimeRecordingSentences.textContent = recordingState?.sentences || '0';
  }
  if (els.realtimeRecordingDevice) {
    const deviceKey = String(recordingState?.device || '').toLowerCase();
    const deviceLabel = deviceKey === 'mic'
      ? '麦克风'
      : deviceKey === 'speaker'
        ? '扬声器'
        : '输入未绑定';
    els.realtimeRecordingDevice.textContent = deviceLabel;
  }

  renderModelSelectionOverview(data);
  renderFileImportProcessingOverview();
}

function syncRecordingButton(recordingState) {
  if (!els.btnRecordingToggle) {
    return;
  }

  const active = Boolean(recordingState?.active);

  els.btnRecordingToggle.textContent = active ? '停止录制' : '开始录制';
  els.btnRecordingToggle.dataset.action = active ? 'stop-recording' : 'start-recording';
  els.btnRecordingToggle.classList.toggle('is-stop', active);
  if (active) {
    startTaskRefresh();
  } else {
    stopTaskRefresh();
  }
}

function syncImportButton(active) {
  if (!els.btnImportStart) return;
  const isActive = Boolean(active);
  els.btnImportStart.textContent = isActive ? '停止处理' : '开始处理';
  els.btnImportStart.dataset.action = isActive ? 'stop-import-queue' : 'start-import-queue';
  els.btnImportStart.classList.toggle('is-stop', isActive);
}

function getSelectedImportModelEngine() {
  const active = document.querySelector('#model-import-engine-bar .model-engine-tab.is-active');
  const value = active ? active.getAttribute('data-import-engine-option') : null;
  return value || 'whisper';
}

function setSelectedImportModelEngine(engine) {
  const targetEngine = engine || 'whisper';
  const tabs = document.querySelectorAll('#model-import-engine-bar .model-engine-tab');
  tabs.forEach((tab) => {
    const isActive = (tab.getAttribute('data-import-engine-option') || '') === targetEngine;
    tab.classList.toggle('is-active', isActive);
    tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });
}

function getSelectedModelManagerEngine() {
  const active = document.querySelector('#model-manager-engine-bar .model-engine-tab.is-active');
  const value = active ? active.getAttribute('data-engine-option') : null;
  return value || 'whisper';
}

function getModelSelectionEngine(data = state.data || {}) {
  const backend = String(data?.import_ui?.selected_backend || '').trim();
  return backend === 'faster-whisper' ? 'faster-whisper' : 'whisper';
}

function setSelectedModelManagerEngine(engine) {
  const targetEngine = engine || 'whisper';
  const tabs = document.querySelectorAll('#model-manager-engine-bar .model-engine-tab');
  tabs.forEach((tab) => {
    const isActive = (tab.getAttribute('data-engine-option') || '') === targetEngine;
    tab.classList.toggle('is-active', isActive);
    tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });
}

function renderModelManagerState(data) {
  const modelUi = data || {};
  state.modelManagerState = modelUi;
  const selectedEngine = modelUi.selected_engine || 'whisper';
  const selectedModel = String(modelUi.selected_model || 'small');
  const selectedEstimateBytes = Number(modelUi.selected_model_estimate_bytes || 0);
  const selectedEstimateText = selectedEstimateBytes > 0 ? formatBytes(selectedEstimateBytes) : '未知体积';
  const rows = Array.isArray(modelUi.rows) ? modelUi.rows : [];
  setSelectedModelManagerEngine(selectedEngine);

  if (els.modelManagerDirPill) {
    els.modelManagerDirPill.textContent = `目录：${modelUi.model_dir || '默认位置'}`;
  }
  if (els.modelManagerSelectionPill) {
    els.modelManagerSelectionPill.textContent = `当前：${selectedEngine} · ${selectedModel} · ${selectedEstimateText}`;
  }
  if (els.modelManagerCachePill) {
    const downloadedCount = rows.filter((row) => row && row.downloaded === true).length;
    const missingCount = rows.filter((row) => row && row.downloaded === false).length;
    const totalCount = rows.length || (Array.isArray(modelUi.model_options) ? modelUi.model_options.length : 0);
    if (totalCount > 0) {
      els.modelManagerCachePill.textContent = missingCount > 0
        ? `缓存：${downloadedCount} / ${totalCount}，缺 ${missingCount}`
        : `缓存：${totalCount} / ${totalCount}，已齐全`;
    } else {
      els.modelManagerCachePill.textContent = modelUi.download_running ? '缓存：刷新中' : '缓存：自动检查中';
    }
  }
  if (els.modelManagerDownloadPill) {
    els.modelManagerDownloadPill.textContent = `下载：${modelUi.download_running ? '进行中' : '空闲'}`;
  }
  if (els.modelManagerHint) {
    const missingCount = rows.filter((row) => row && row.downloaded === false).length;
    const checked = modelUi.checked || null;
    const checkedText = checked
      ? `最近检查：${checked.model} / ${checked.engine} / ${checked.downloaded ? '已下载' : (checked.error ? `失败：${checked.error}` : '缺失')}`
      : `当前模型：${selectedModel}，预计体积 ${selectedEstimateText}。`;
    const downloadText = modelUi.download_running
      ? '下载进行中，列表会持续刷新。'
      : missingCount > 0
        ? `当前仍缺 ${missingCount} 个模型。`
        : '当前引擎模型已全部就绪。';
    els.modelManagerHint.textContent = `当前展示 ${selectedEngine} 的全部模型。缺失项可直接下载；左侧按钮可检查当前模型或整个引擎。${downloadText} ${checkedText}`;
  }

  if (els.modelStatusCard) {
    const renderedRows = rows
      .map((row) => {
        const rowModel = String(row.model || '-');
        const rowEngine = String(row.engine || selectedEngine || 'whisper');
        const rowProgress = Math.max(0, Math.min(100, Number(row.progress) || 0));
        const rowSpeed = String(row.speed || '').trim();
        const note = row.error ? `错误：${row.error}` : '';
        const downloadAction = row.downloaded === true
          ? `<button class="model-download-btn model-downloaded-btn" disabled>已下载</button>`
          : row.downloaded === false && !row.downloading
            ? `<button class="model-download-btn" data-action="download-model-row" data-model="${escapeHtml(rowModel)}" data-engine="${escapeHtml(rowEngine)}" title="下载 ${escapeHtml(rowModel)}">下载</button>`
            : '';
        const rowProgressHtml = row.downloading
          ? `
            <div class="model-download-progress" aria-label="下载进度">
              <div class="model-download-progress-fill" style="--download-progress: ${rowProgress.toFixed(1)}%"></div>
            </div>
            <div class="model-download-meta">${rowProgress.toFixed(0)}%${rowSpeed ? ` | ${escapeHtml(rowSpeed)}` : ''}</div>
          `
          : '';

        return `
          <div class="model-status-item">
            <div class="model-status-head">
              <span class="model-status-name">${escapeHtml(rowModel)}</span>
            </div>
            ${downloadAction ? `<div class="model-status-value ${row.downloaded === false && !row.downloading ? 'is-missing' : ''}">${downloadAction}</div>` : ''}
            ${rowProgressHtml}
            ${note ? `<div class="error">${escapeHtml(note)}</div>` : ''}
          </div>
        `;
      })
      .join('');
    els.modelStatusCard.innerHTML = renderedRows
      ? `<div class="model-status-grid">${renderedRows}</div>`
      : `
        <div class="model-empty-state">
          <div class="model-empty-title">暂无模型状态</div>
          <div class="model-empty-meta">点击右上角检查按钮，扫描当前引擎的本地模型缓存和下载状态。</div>
        </div>
      `;
  }
}

async function refreshModelManagerState(engine) {
  const payload = await apiCall('get_model_manager_state', engine || null);
  renderModelManagerState(payload);
  return payload;
}

async function checkAllModelManagerState(engine) {
  const payload = await apiCall('check_all_models', engine || getSelectedModelManagerEngine());
  renderModelManagerState(payload);
  return payload;
}

async function checkCurrentModelManagerState(modelOverride = null, engineOverride = null) {
  const engine = engineOverride || getSelectedModelManagerEngine();
  const modelKey = modelOverride || els.modelImport?.value || state.data?.import_ui?.selected_model || 'small';
  const payload = await apiCall('check_model', modelKey, engine);
  renderModelManagerState(payload);
  return payload;
}

async function refreshImportUiDetails() {
  const payload = await apiCall('get_import_ui_details');
  state.data = state.data || {};
  state.data.import_ui = payload;
  renderImportSettings({ import_ui: payload });
}

function stopModelProgressPolling() {
  if (state.modelPollTimer !== null) {
    window.clearInterval(state.modelPollTimer);
    state.modelPollTimer = null;
  }
}

function startModelProgressPolling(engine) {
  stopModelProgressPolling();
  let sawRunning = false;
  state.modelPollTimer = window.setInterval(async () => {
    try {
      const payload = await refreshModelManagerState(engine || getSelectedModelManagerEngine());
      await refreshTaskState();
      if (payload && payload.download_running) {
        sawRunning = true;
        return;
      }

      if (!payload || !payload.download_running) {
        stopModelProgressPolling();
        if (sawRunning) {
          await refreshState();
        }
      }
    } catch (error) {
      console.debug('Model progress polling stopped', error);
      stopModelProgressPolling();
    }
  }, 800);
}

function startRuntimeModelLoadPolling() {
  stopModelProgressPolling();
  let sawLoading = false;
  state.modelPollTimer = window.setInterval(async () => {
    try {
      await refreshTaskState();
      const runtimeModel = state.data && state.data.runtime_model ? state.data.runtime_model : null;
      const loading = Boolean(runtimeModel && runtimeModel.loading);
      if (loading) {
        sawLoading = true;
        return;
      }

      stopModelProgressPolling();
      if (sawLoading) {
        await refreshState();
      }
    } catch (error) {
      console.debug('Runtime model polling stopped', error);
      stopModelProgressPolling();
    }
  }, 800);
}

async function refreshState(options = {}) {
  const deferHeavy = options.deferHeavy !== false;
  if (!state.bridgeReady) {
    const ready = await waitForBridge();
    if (!ready) {
      throw new Error('桥接初始化超时：pywebview API 不可用');
    }
  }

  const data = await apiCall('get_state');
  state.data = data;
  renderSettings(data);
  renderMainControls(data);
  renderRecordSettings(data);
  renderImportSettings(data);
  renderModelSelectionOverview(data);
  renderFileImportProcessingOverview();
  renderLiveOutputs(data);
  renderAbout(data);
  renderTaskRuntimePills(data);
  renderSettingsPanelSummaries(data);
  renderSettingsToolbarOverview(data);
  syncLogAutoRefresh();
  updatePageScrollIndicator();

  const runHeavyRefresh = async () => {
    await refreshTaskState();
    await refreshImportUiDetails();
    const modelEngine = getModelSelectionEngine(state.data || data);
    if (!state.modelManagerCheckedEngines[modelEngine]) {
      await checkAllModelManagerState(modelEngine);
      state.modelManagerCheckedEngines[modelEngine] = true;
      state.modelCheckedOnce = true;
    } else {
      await refreshModelManagerState(modelEngine);
    }
  };

  if (deferHeavy) {
    window.setTimeout(() => {
      runHeavyRefresh().catch((error) => console.debug('Deferred refresh skipped', error));
    }, 0);
  } else {
    await runHeavyRefresh();
  }
}

async function refreshTaskState() {
  const task = await apiCall('get_task_state');
  try {
    const runtimeModel = await apiCall('get_runtime_model_state');
    state.data = state.data || {};
    state.data.runtime_model = runtimeModel;
  } catch (error) {
    console.debug('Runtime model state refresh skipped', error);
  }
  let recordingState = null;
  renderTaskState(task);
  try {
    recordingState = await apiCall('get_recording_state');
    state.data = state.data || {};
    state.data.recording_state = recordingState;
    syncRecordingButton(recordingState);
  } catch (error) {
    console.debug('Recording button sync skipped', error);
  }
  renderGlobalStatusBar(task, state.data, recordingState || state.data?.recording_state || null);
  renderRecordingVisualizer(recordingState || state.data?.recording_state || null);
  renderModelSelectionOverview(state.data || {});
  renderDetachedWindowOverview(state.data || {});
  renderFileImportProcessingOverview();
  renderTaskRuntimePills(state.data || {});
  try {
    const live = await apiCall('get_live_state');
    renderLiveOutputs({ live_ui: live, settings: state.data?.settings || {} });
  } catch (error) {
    console.debug('Live state refresh skipped', error);
  }
  updatePageScrollIndicator();
}

async function saveSettings(shouldRefresh = true) {
  const currentSetting = (key, fallback = '') => {
    const settings = state.data && state.data.settings ? state.data.settings : null;
    const value = settings ? settings[key] : undefined;
    return value === undefined ? fallback : value;
  };

  syncToolbarMirrorValues([
    [els.httpProxy, els.httpProxyToolbar, ''],
    [els.httpsProxy, els.httpsProxyToolbar, ''],
    [els.libreLink, els.libreLinkToolbar, ''],
    [els.libreApiKey, els.libreApiKeyToolbar, ''],
    [els.exportFormat, els.exportFormatToolbar, '%Y-%m-%d %f {file}/{task-lang}'],
    [els.segmentMaxWords, els.segmentMaxWordsToolbar, ''],
    [els.segmentMaxChars, els.segmentMaxCharsToolbar, ''],
    [els.segmentSplitOrNewline, els.segmentSplitOrNewlineToolbar, 'Split'],
    [els.transcribeRate, els.transcribeRateToolbar, '300'],
    [els.decodingPreset, els.decodingPresetToolbar, 'beam search'],
    [els.temperature, els.temperatureToolbar, '0.0, 0.2, 0.4, 0.6, 0.8, 1.0'],
    [els.bestOf, els.bestOfToolbar, '3'],
    [els.beamSize, els.beamSizeToolbar, '3'],
    [els.noSpeechThreshold, els.noSpeechThresholdToolbar, '0.72'],
    [els.logprobThreshold, els.logprobThresholdToolbar, '-1.0'],
    [els.patience, els.patienceToolbar, '1.0'],
    [els.compressionRatioThreshold, els.compressionRatioThresholdToolbar, '2.4'],
    [els.suppressTokens, els.suppressTokensToolbar, ''],
    [els.modelDevicePreference, els.modelDevicePreferenceToolbar, 'auto'],
  ]);
  syncToolbarMirrorChecks([
    [els.httpProxyEnable, els.httpProxyEnableToolbar, false],
    [els.httpsProxyEnable, els.httpsProxyEnableToolbar, false],
    [els.autoOpenDirExport, els.autoOpenDirExportToolbar, true],
    [els.exportTxt, els.exportTxtToolbar, true],
    [els.exportSrt, els.exportSrtToolbar, true],
    [els.exportVtt, els.exportVttToolbar, true],
    [els.exportJson, els.exportJsonToolbar, true],
    [els.exportAss, els.exportAssToolbar, true],
    [els.exportCsv, els.exportCsvToolbar, false],
    [els.exportTsv, els.exportTsvToolbar, false],
    [els.exportMp4, els.exportMp4Toolbar, false],
    [els.recAskConfirmationFirst, els.recAskConfirmationFirstToolbar, true],
    [els.supressHiddenToTray, els.supressHiddenToTrayToolbar, false],
    [els.useEnModel, els.useEnModelToolbar, true],
    [els.fp16, els.fp16Toolbar, true],
    [els.suppressBlank, els.suppressBlankToolbar, true],
    [els.useTempAlt, els.useTempAltToolbar, false],
    [els.keepTemp, els.keepTempToolbar, false],
    [els.fileUseOfficialWhisper, els.fileUseOfficialWhisperToolbar, false],
    [els.supressRecordWarning, els.supressRecordWarningToolbar, false],
    [els.debugRealtimeRecord, els.debugRealtimeRecordToolbar, false],
    [els.debugTranslate, els.debugTranslateToolbar, false],
    [els.segmentEvenSplit, els.segmentEvenSplitToolbar, true],
    [els.segmentLevel, els.segmentLevelToolbar, true],
    [els.wordLevel, els.wordLevelToolbar, true],
  ]);
  if (els.hostAPI && els.hostAPIToolbar) {
    els.hostAPI.value = els.hostAPIToolbar.value;
  }

  const updates = [
    ['dir_log', readStringValue(els.dirLog, currentSetting('dir_log', 'auto'))],
    ['log_level', readStringValue(els.logLevel, currentSetting('log_level', 'DEBUG'))],
    ['mw_size', readStringValue(els.mainWindowSize, currentSetting('mw_size', '1140x680'))],
    ['input', readStringValue(els.inputMode, 'mic')],
    ['use_faster_whisper', readStringValue(els.backendMain, currentSetting('use_faster_whisper', true) ? 'faster-whisper' : 'whisper') === 'faster-whisper'],
    ['model_mw', readStringValue(els.modelMain, currentSetting('model_mw', ''))],
    ['source_lang_mw', readStringValue(els.sourceLangMain, 'English')],
    ['target_lang_mw', readStringValue(els.targetLangMain, 'Indonesian')],
    ['tl_engine_mw', readStringValue(els.translateEngineMain, 'Google Translate')],
    ['transcribe_mw', readBooleanValue(els.transcribeMain, true)],
    ['translate_mw', readBooleanValue(els.translateMain, true)],
    ['auto_scroll_log', readBooleanValue(els.autoScrollLog, true)],
    ['auto_refresh_log', readBooleanValue(els.autoRefreshLog, false)],
    ['filter_rec', readBooleanValue(els.filterRec, true)],
    ['filter_rec_case_sensitive', readBooleanValue(els.filterRecCaseSensitive, false)],
    ['filter_rec_strip', readBooleanValue(els.filterRecStrip, true)],
    ['filter_rec_exact_match', readBooleanValue(els.filterRecExactMatch, false)],
    ['filter_rec_ignore_punctuations', readStringValue(els.filterRecIgnorePunctuations, "\"',.?!")],
    ['filter_rec_similarity', readNumberValue(els.filterRecSimilarity, 0.75)],
    ['http_proxy_enable', readBooleanValue(els.httpProxyEnable, false)],
    ['http_proxy', readStringValue(els.httpProxy, '')],
    ['https_proxy_enable', readBooleanValue(els.httpsProxyEnable, false)],
    ['https_proxy', readStringValue(els.httpsProxy, '')],
    ['libre_link', readStringValue(els.libreLink, '')],
    ['libre_api_key', readStringValue(els.libreApiKey, '')],
    ['use_en_model', readBooleanValue(els.useEnModel, true)],
    ['decoding_preset', readStringValue(els.decodingPreset, 'beam search')],
    ['temperature', readStringValue(els.temperature, '0.0, 0.2, 0.4, 0.6, 0.8, 1.0')],
    ['best_of', readNumberValue(els.bestOf, 3)],
    ['beam_size', readNumberValue(els.beamSize, 3)],
    ['patience', readNumberValue(els.patience, 1.0)],
    ['compression_ratio_threshold', readNumberValue(els.compressionRatioThreshold, 2.4)],
    ['logprob_threshold', readNumberValue(els.logprobThreshold, -1.0)],
    ['no_speech_threshold', readNumberValue(els.noSpeechThreshold, 0.72)],
    ['suppress_tokens', readStringValue(els.suppressTokens, '')],
    ['suppress_blank', readBooleanValue(els.suppressBlank, true)],
    ['fp16', readBooleanValue(els.fp16, true)],
    ['initial_prompt', readStringValue(els.initialPrompt, '') || null],
    ['prefix', readStringValue(els.prefix, '') || null],
    ['max_initial_timestamp', readNumberValue(els.maxInitialTimestamp, 1.0)],
    ['whisper_args', readStringValue(els.whisperArgs, '')],
    ['path_filter_rec', readStringValue(els.pathFilterRec, 'auto')],
    ['rec_ask_confirmation_first', readBooleanValue(els.recAskConfirmationFirst, false)],
    ['close_to_tray_on_close', readBooleanValue(els.closeToTrayOnClose, true)],
    ['supress_hidden_to_tray', readBooleanValue(els.supressHiddenToTray, false)],
    ['supress_record_warning', readBooleanValue(els.supressRecordWarning, false)],
    ['debug_realtime_record', readBooleanValue(els.debugRealtimeRecord, false)],
    ['debug_translate', readBooleanValue(els.debugTranslate, false)],
    ['colorize_per_segment', readBooleanValue(els.colorizePerSegment, true)],
    ['colorize_per_word', readBooleanValue(els.colorizePerWord, false)],
    ['gradient_low_conf', readStringValue(els.gradientLowConf, '#FF0000')],
    ['gradient_high_conf', readStringValue(els.gradientHighConf, '#00FF00')],
    ['tb_mw_tc_auto_scroll', readBooleanValue(els.tbMwTcAutoScroll, true)],
    ['tb_mw_tc_limit_max', readBooleanValue(els.tbMwTcLimitMax, false)],
    ['tb_mw_tc_limit_max_per_line', readBooleanValue(els.tbMwTcLimitMaxPerLine, false)],
    ['tb_mw_tc_max', readNumberValue(els.tbMwTcMax, 300)],
    ['tb_mw_tc_max_per_line', readNumberValue(els.tbMwTcMaxPerLine, 30)],
    ['tb_mw_tc_font', readStringValue(els.tbMwTcFont, 'TKDefaultFont')],
    ['tb_mw_tc_font_bold', readBooleanValue(els.tbMwTcFontBold, false)],
    ['tb_mw_tc_font_size', readNumberValue(els.tbMwTcFontSize, 10)],
    ['tb_mw_tc_font_color', readStringValue(els.tbMwTcFontColor, '#FFFFFF')],
    ['tb_mw_tc_use_conf_color', readBooleanValue(els.tbMwTcUseConfColor, true)],
    ['tb_mw_tl_auto_scroll', readBooleanValue(els.tbMwTlAutoScroll, true)],
    ['tb_mw_tl_limit_max', readBooleanValue(els.tbMwTlLimitMax, false)],
    ['tb_mw_tl_limit_max_per_line', readBooleanValue(els.tbMwTlLimitMaxPerLine, false)],
    ['tb_mw_tl_max', readNumberValue(els.tbMwTlMax, 300)],
    ['tb_mw_tl_max_per_line', readNumberValue(els.tbMwTlMaxPerLine, 30)],
    ['tb_mw_tl_font', readStringValue(els.tbMwTlFont, 'TKDefaultFont')],
    ['tb_mw_tl_font_bold', readBooleanValue(els.tbMwTlFontBold, false)],
    ['tb_mw_tl_font_size', readNumberValue(els.tbMwTlFontSize, 10)],
    ['tb_mw_tl_font_color', readStringValue(els.tbMwTlFontColor, '#FFFFFF')],
    ['tb_mw_tl_use_conf_color', readBooleanValue(els.tbMwTlUseConfColor, true)],
  ];

  for (const key of DETACHED_WINDOW_PANEL_KEYS) {
    const node = $(key);
    if (!node) {
      continue;
    }
    const fallback = DETACHED_WINDOW_PANEL_DEFAULTS[key];
    updates.push([key, readInputValue(node, fallback)]);
  }

  await persistApiPairs('set_setting', updates);
  await persistSharedFileSettings();
  await persistCurrentRecordDeviceSelection();

  try {
    await apiCall('rerender_live_text');
    await refreshTaskState();
  } catch (error) {
    console.debug('Live text rerender skipped', error);
  }

  await pushDetachedConfigUpdates();

  if (shouldRefresh) {
    await refreshState();
  }
}

async function saveAllSettings() {
  await saveSettings(false);
  await persistApiPairs('set_record_setting', collectRecordSettingUpdates({ includeDeviceSelection: false }));
  await persistApiPairs('set_import_setting', collectImportSpecificSettingUpdates());
  await saveSeleniumSettings(false);
  await saveInitialPromptsSettings(false);
  await refreshState();
}

async function saveSeleniumSettings(shouldRefresh = true) {
  if (state.seleniumSaveInFlight) {
    return;
  }

  state.seleniumSaveInFlight = true;
  const saveButtons = Array.from(document.querySelectorAll('button[data-action="save-selenium-settings"]'));
  saveButtons.forEach((btn) => {
    btn.disabled = true;
  });

  const compactEl = $('selenium_compact_level');
  const zOrderEl = $('selenium_z_order_mode');
  const autoCloseEl = $('selenium_auto_close_on_task_done');
  const chromeUserDataDirEl = $('selenium_chrome_user_data_dir');

  const compactRaw = Number(compactEl ? compactEl.value : 2);
  const compactLevel = Number.isFinite(compactRaw) ? Math.max(0, Math.min(3, Math.trunc(compactRaw))) : 2;
  const zOrderRaw = String(zOrderEl ? zOrderEl.value : 'behind-main');
  const zOrderMode = ['normal', 'behind-main', 'bottom'].includes(zOrderRaw) ? zOrderRaw : 'behind-main';
  const autoClose = Boolean(autoCloseEl && autoCloseEl.checked);
  const chromeUserDataDir = String(chromeUserDataDirEl ? chromeUserDataDirEl.value : '').trim();

  try {
    const res = await apiCall('set_setting', 'selenium_settings', {
      compact_level: compactLevel,
      z_order_mode: zOrderMode,
      auto_close_on_task_done: autoClose,
      chrome_user_data_dir: chromeUserDataDir,
    });

    const saved = res && res.value
      ? res.value
      : {
          selenium_chrome_user_data_dir: chromeUserDataDir,
          selenium_compact_level: compactLevel,
          selenium_z_order_mode: zOrderMode,
          selenium_auto_close_on_task_done: autoClose,
        };

    if (compactEl) compactEl.value = String(saved.selenium_compact_level ?? compactLevel);
    if (zOrderEl) zOrderEl.value = String(saved.selenium_z_order_mode ?? zOrderMode);
    if (autoCloseEl) autoCloseEl.checked = Boolean(saved.selenium_auto_close_on_task_done ?? autoClose);
    if (chromeUserDataDirEl) {
      chromeUserDataDirEl.value = String(saved.selenium_chrome_user_data_dir ?? chromeUserDataDir);
    }

    if (state.data && state.data.settings) {
      state.data.settings.selenium_compact_level = Number(saved.selenium_compact_level ?? compactLevel);
      state.data.settings.selenium_z_order_mode = String(saved.selenium_z_order_mode ?? zOrderMode);
      state.data.settings.selenium_auto_close_on_task_done = Boolean(saved.selenium_auto_close_on_task_done ?? autoClose);
      state.data.settings.selenium_chrome_user_data_dir = String(saved.selenium_chrome_user_data_dir ?? chromeUserDataDir);
    }

    if (shouldRefresh) {
      console.log(
        `Selenium 设置已保存：模式=${saved.selenium_compact_level}，层级=${saved.selenium_z_order_mode}，自动关闭=${saved.selenium_auto_close_on_task_done ? '开' : '关'}`
      );
    }
  } catch (error) {
    throw error;
  } finally {
    state.seleniumSaveInFlight = false;
    saveButtons.forEach((btn) => {
      btn.disabled = false;
    });
  }
}

async function saveInitialPromptsSettings(shouldRefresh = true) {
  if (state.initialPromptsSaveInFlight) {
    return;
  }

  state.initialPromptsSaveInFlight = true;
  const saveButtons = Array.from(document.querySelectorAll('button[data-action="save-initial-prompts"]'));
  saveButtons.forEach((btn) => {
    btn.disabled = true;
  });

  try {
    const enabled = Boolean(els.enableInitialPrompts && els.enableInitialPrompts.checked);
    await apiCall('set_setting', 'enable_initial_prompt', enabled);

    let mapVal = {};
    if (els.initialPromptsContainer) {
      const rows = Array.from(els.initialPromptsContainer.querySelectorAll('[data-prompt-row]'));
      for (const row of rows) {
        const custom = row.getAttribute('data-prompt-row') === 'custom';
        const code = custom
          ? String((row.querySelector('[data-lang-code="true"]')?.value || '')).trim()
          : String(row.getAttribute('data-lang') || '').trim();
        const val = String((row.querySelector('[data-prompt-text="true"]')?.value || '')).trim();
        if (code && val) {
          mapVal[code] = val;
        }
      }
    }
    await apiCall('set_setting', 'initial_prompts_map', mapVal);

    if (state.data && state.data.settings) {
      state.data.settings.enable_initial_prompt = enabled;
      state.data.settings.initial_prompts_map = mapVal;
    }

    if (shouldRefresh) await refreshState();
    if (shouldRefresh) console.log('按语言引导词已保存');
  } catch (error) {
    throw error;
  } finally {
    state.initialPromptsSaveInFlight = false;
    saveButtons.forEach((btn) => {
      btn.disabled = false;
    });
  }
}

async function saveImportSettings(shouldRefresh = true) {
  const backend = getSelectedImportModelEngine();
  await apiCall('set_setting', 'use_faster_whisper', backend === 'faster-whisper');

  syncToolbarMirrorValues([
    [els.exportFormatToolbar, els.exportFormat, '%Y-%m-%d %f {file}/{task-lang}'],
  ]);
  syncToolbarMirrorChecks([
    [els.autoOpenDirExportToolbar, els.autoOpenDirExport, true],
    [els.exportTxtToolbar, els.exportTxt, true],
    [els.exportSrtToolbar, els.exportSrt, true],
    [els.exportVttToolbar, els.exportVtt, true],
    [els.exportJsonToolbar, els.exportJson, true],
    [els.exportAssToolbar, els.exportAss, true],
    [els.exportCsvToolbar, els.exportCsv, false],
    [els.exportTsvToolbar, els.exportTsv, false],
    [els.exportMp4Toolbar, els.exportMp4, false],
    [els.useTempAltToolbar, els.useTempAlt, false],
    [els.keepTempToolbar, els.keepTemp, false],
    [els.fileUseOfficialWhisperToolbar, els.fileUseOfficialWhisper, false],
  ]);

  if (els.dirExport && els.dirExportFile) {
    const normalizedExportDir = String(els.dirExportFile.value || els.dirExport.value || 'auto');
    els.dirExport.value = normalizedExportDir;
    els.dirExportFile.value = normalizedExportDir;
  }
  if (els.autoOpenDirExport && els.autoOpenDirExportFile) {
    els.autoOpenDirExport.checked = Boolean(els.autoOpenDirExportFile.checked);
  }

  const sharedFileSettings = await persistSharedFileSettings();

  await persistApiPairs('set_import_setting', collectImportSpecificSettingUpdates());

  patchLocalSettings({
    use_faster_whisper: backend === 'faster-whisper',
    ...sharedFileSettings,
  });
  patchLocalImportUi({
    selected_backend: backend,
    selected_model: readStringValue(els.modelImport, ''),
    selected_model_key: readStringValue(els.modelImport, ''),
    selected_model_label: readSelectedLabel(els.modelImport, readStringValue(els.modelImport, '')),
    selected_engine: readStringValue(els.engineImport, ''),
    selected_source: readStringValue(els.sourceImport, ''),
    selected_target: readStringValue(els.targetImport, ''),
    transcribe: readBooleanValue(els.transcribeImport, true),
    translate: readBooleanValue(els.translateImport, true),
  });

  if (shouldRefresh) {
    await refreshState();
  } else {
    renderImportSettings({ import_ui: state.data?.import_ui || {} });
  }
}

async function loadRuntimeModel(modelKeyOverride = null) {
  const modelKey = modelKeyOverride || (els.modelImport ? els.modelImport.value : '');
  if (!modelKey) {
    return;
  }

  const result = await apiCall('load_runtime_model', modelKey);
  if (!result || result.ok === false) {
    throw new Error((result && result.message) || '模型加载启动失败');
  }

  await refreshTaskState();
  startRuntimeModelLoadPolling();
}

async function loadMainRuntimeModel() {
  const modelKey = els.modelMain ? els.modelMain.value : '';
  if (!modelKey) {
    return;
  }

  await saveSettings(false);
  await loadRuntimeModel(modelKey);
}

function collectRecordSettingUpdates({ includeDeviceSelection = true } = {}) {
  const recordDeviceSelection = collectRecordDeviceSelection();
  const updates = includeDeviceSelection
    ? [
        ['hostAPI', recordDeviceSelection.hostAPI],
        ['mic', recordDeviceSelection.mic],
        ['speaker', recordDeviceSelection.speaker],
      ]
    : [];
  return [
    ...updates,
    ['verbose_record', readStringValue(els.verboseRecord, 'false') === 'true'],
    ['model_device_preference', readStringValue(els.modelDevicePreference, 'auto').toLowerCase()],
    ['transcribe_rate', readNumberValue(els.transcribeRate, 300)],
    ['separate_with', readStringValue(els.separateWith, '')],
    ['use_temp', readBooleanValue(els.useTempAlt, false)],
    ['keep_temp', readBooleanValue(els.keepTemp, false)],
    ['file_use_official_whisper', readBooleanValue(els.fileUseOfficialWhisper, false)],
    ['sample_rate_mic', readNumberValue(els.micSampleRate, 16000)],
    ['chunk_size_mic', readNumberValue(els.micChunkSize, 1024)],
    ['channels_mic', readStringValue(els.micChannels, '')],
    ['auto_sample_rate_mic', readBooleanValue(els.micAutoSampleRate, false)],
    ['auto_channels_mic', readBooleanValue(els.micAutoChannels, false)],
    ['min_input_length_mic', readNumberValue(els.micMinInputLength, 0.4)],
    ['max_buffer_mic', readNumberValue(els.micMaxBuffer, 10)],
    ['max_sentences_mic', readNumberValue(els.micMaxSentences, 5)],
    ['mic_no_limit', readBooleanValue(els.micNoLimit, false)],
    ['threshold_enable_mic', readBooleanValue(els.micThresholdEnable, false)],
    ['threshold_auto_mic', readBooleanValue(els.micThresholdAuto, false)],
    ['auto_break_buffer_mic', readBooleanValue(els.micAutoBreakBuffer, false)],
    ['threshold_auto_level_mic', readNumberValue(els.micThresholdAutoLevel, 3)],
    ['threshold_auto_silero_mic', readBooleanValue(els.micThresholdAutoSilero, false)],
    ['threshold_silero_mic_min', readNumberValue(els.micThresholdSileroMin, 0.7)],
    ['threshold_db_mic', readNumberValue(els.micThresholdDb, -30.0)],
    ['sample_rate_speaker', readNumberValue(els.speakerSampleRate, 44100)],
    ['chunk_size_speaker', readNumberValue(els.speakerChunkSize, 1024)],
    ['channels_speaker', readStringValue(els.speakerChannels, '')],
    ['auto_sample_rate_speaker', readBooleanValue(els.speakerAutoSampleRate, false)],
    ['auto_channels_speaker', readBooleanValue(els.speakerAutoChannels, false)],
    ['min_input_length_speaker', readNumberValue(els.speakerMinInputLength, 0.4)],
    ['max_buffer_speaker', readNumberValue(els.speakerMaxBuffer, 10)],
    ['max_sentences_speaker', readNumberValue(els.speakerMaxSentences, 5)],
    ['speaker_no_limit', readBooleanValue(els.speakerNoLimit, false)],
    ['threshold_enable_speaker', readBooleanValue(els.speakerThresholdEnable, false)],
    ['threshold_auto_speaker', readBooleanValue(els.speakerThresholdAuto, false)],
    ['auto_break_buffer_speaker', readBooleanValue(els.speakerAutoBreakBuffer, false)],
    ['threshold_auto_level_speaker', readNumberValue(els.speakerThresholdAutoLevel, 3)],
    ['threshold_auto_silero_speaker', readBooleanValue(els.speakerThresholdAutoSilero, false)],
    ['threshold_silero_speaker_min', readNumberValue(els.speakerThresholdSileroMin, 0.7)],
    ['threshold_db_speaker', readNumberValue(els.speakerThresholdDb, -30.0)],
  ];
}

async function saveRecordSettings(shouldRefresh = true) {
  const updates = collectRecordSettingUpdates();
  await persistApiPairs('set_record_setting', updates);

  if (shouldRefresh) {
    await refreshState();
  }
}

function collectImportSpecificSettingUpdates() {
  return [
    ['model_f_import', readStringValue(els.modelImport, '')],
    ['tl_engine_f_import', readStringValue(els.engineImport, '')],
    ['source_lang_f_import', readStringValue(els.sourceImport, '')],
    ['target_lang_f_import', readStringValue(els.targetImport, '')],
    ['transcribe_f_import', readBooleanValue(els.transcribeImport, true)],
    ['translate_f_import', readBooleanValue(els.translateImport, true)],
    ['filter_file_import', readBooleanValue(els.filterFileImport, true)],
    ['filter_file_import_case_sensitive', readBooleanValue(els.filterFileImportCaseSensitive, false)],
    ['filter_file_import_strip', readBooleanValue(els.filterFileImportStrip, true)],
    ['filter_file_import_exact_match', readBooleanValue(els.filterFileImportExactMatch, false)],
    ['filter_file_import_ignore_punctuations', readStringValue(els.filterFileImportIgnorePunctuations, "\"',.?!")],
    ['filter_file_import_similarity', readNumberValue(els.filterFileImportSimilarity, 0.75)],
  ];
}

function normalizeDetachedMode(mode) {
  return mode === 'tl' ? 'tl' : 'tc';
}

function syncDetachedMirrorControls(mode) {
  const normalized = normalizeDetachedMode(mode);
  const pairs = DETACHED_WINDOW_MIRROR_PAIRS[normalized] || [];
  for (const [mirrorId, sourceId] of pairs) {
    const mirror = $(mirrorId);
    const source = $(sourceId);
    if (!mirror || !source) {
      continue;
    }
    writeInputValue(source, readInputValue(mirror, ''), '');
  }
}

function getDetachedMirrorMode(controlId) {
  if (typeof controlId !== 'string' || !controlId) {
    return null;
  }
  if (controlId.startsWith('ex_tc_') || controlId.startsWith('tb_ex_tc_')) {
    return 'tc';
  }
  if (controlId.startsWith('ex_tl_') || controlId.startsWith('tb_ex_tl_')) {
    return 'tl';
  }
  return null;
}

async function pushDetachedConfigUpdates() {
  if (typeof pywebview === 'undefined' || !pywebview.api) {
    return;
  }
  for (const mode of ['tc', 'tl']) {
    await pywebview.api.update_detached_config(mode);
  }
}

const AUTO_SAVE_BUCKETS = {
  settings: new Set([
    'log_level', 'auto_scroll_log', 'auto_refresh_log',
    'mw_size',
    'input_mode', 'backend_mw', 'model_mw', 'source_lang_mw', 'target_lang_mw', 'tl_engine_mw',
    'transcribe_mw', 'translate_mw',
    'filter_rec', 'filter_rec_case_sensitive', 'filter_rec_strip', 'filter_rec_exact_match', 'filter_rec_ignore_punctuations', 'filter_rec_similarity',
    'http_proxy_enable', 'http_proxy', 'https_proxy_enable', 'https_proxy',
    'libre_link', 'libre_api_key',
    'use_en_model', 'decoding_preset', 'temperature', 'best_of', 'beam_size', 'patience',
    'compression_ratio_threshold', 'logprob_threshold', 'no_speech_threshold', 'suppress_tokens', 'suppress_blank',
    'fp16', 'initial_prompt', 'prefix', 'max_initial_timestamp', 'whisper_args',
    'path_filter_rec',
    'rec_ask_confirmation_first', 'close_to_tray_on_close', 'supress_hidden_to_tray',
    'supress_record_warning', 'debug_realtime_record', 'debug_translate',
    'colorize_per_segment', 'colorize_per_word', 'gradient_low_conf', 'gradient_high_conf',
    'tb_mw_tc_auto_scroll', 'tb_mw_tc_limit_max', 'tb_mw_tc_limit_max_per_line', 'tb_mw_tc_max',
    'tb_mw_tc_max_per_line', 'tb_mw_tc_font', 'tb_mw_tc_font_bold', 'tb_mw_tc_font_size', 'tb_mw_tc_font_color', 'tb_mw_tc_use_conf_color',
    'tb_mw_tl_auto_scroll', 'tb_mw_tl_limit_max', 'tb_mw_tl_limit_max_per_line', 'tb_mw_tl_max',
    'tb_mw_tl_max_per_line', 'tb_mw_tl_font', 'tb_mw_tl_font_bold', 'tb_mw_tl_font_size', 'tb_mw_tl_font_color', 'tb_mw_tl_use_conf_color',
    ...DETACHED_WINDOW_PANEL_KEYS
  ]),
  import: new Set([
    'model_f_import', 'tl_engine_f_import', 'source_lang_f_import', 'target_lang_f_import',
    'transcribe_f_import', 'translate_f_import',
    'filter_file_import', 'filter_file_import_case_sensitive', 'filter_file_import_strip', 'filter_file_import_exact_match', 'filter_file_import_ignore_punctuations', 'filter_file_import_similarity',
  ]),
  fileShared: new Set([
    'dir_export', 'dir_export_file',
    'auto_open_dir_export', 'auto_open_dir_export_file',
    'auto_open_dir_translate_file', 'auto_open_dir_refinement_file', 'auto_open_dir_alignment_file',
    'export_format', 'export_format_toolbar',
    'export_txt', 'export_txt_toolbar',
    'export_srt', 'export_srt_toolbar',
    'export_vtt', 'export_vtt_toolbar',
    'export_ass', 'export_ass_toolbar',
    'export_json', 'export_json_toolbar',
    'export_csv', 'export_csv_toolbar',
    'export_tsv', 'export_tsv_toolbar',
    'export_mp4', 'export_mp4_toolbar',
    'remove_repetition_file_import', 'remove_repetition_amount',
    'segment_max_words', 'segment_max_words_toolbar',
    'segment_max_chars', 'segment_max_chars_toolbar',
    'segment_split_or_newline', 'segment_split_or_newline_toolbar',
    'segment_even_split', 'segment_even_split_toolbar',
    'segment_level', 'segment_level_toolbar',
    'word_level', 'word_level_toolbar',
    'file_slice_start', 'file_slice_end',
    'path_filter_file_import',
  ]),
  detachedMain: new Set([
    'ex_tc_geometry_main', 'ex_tc_opacity_main', 'ex_tc_always_on_top_main', 'ex_tc_no_title_bar_main', 'ex_tc_click_through_main', 'tb_ex_tc_use_conf_color_main',
    'ex_tl_geometry_main', 'ex_tl_opacity_main', 'ex_tl_always_on_top_main', 'ex_tl_no_title_bar_main', 'ex_tl_click_through_main', 'tb_ex_tl_use_conf_color_main'
  ]),
  record: new Set([
    'verbose_record', 'model_device_preference', 'transcribe_rate', 'separate_with',
    'use_temp', 'use_temp_alt', 'keep_temp', 'file_use_official_whisper',
    'sample_rate_mic', 'chunk_size_mic', 'channels_mic', 'min_input_length_mic', 'max_buffer_mic', 'max_sentences_mic',
    'auto_sample_rate_mic', 'auto_channels_mic', 'mic_no_limit', 'threshold_enable_mic', 'threshold_auto_mic',
    'threshold_auto_level_mic', 'threshold_auto_silero_mic', 'threshold_silero_mic_min', 'auto_break_buffer_mic', 'threshold_db_mic',
    'sample_rate_speaker', 'chunk_size_speaker', 'channels_speaker', 'min_input_length_speaker', 'max_buffer_speaker', 'max_sentences_speaker',
    'auto_sample_rate_speaker', 'auto_channels_speaker', 'speaker_no_limit', 'threshold_enable_speaker',
    'threshold_auto_speaker', 'threshold_auto_level_speaker', 'threshold_auto_silero_speaker', 'threshold_silero_speaker_min', 'auto_break_buffer_speaker', 'threshold_db_speaker'
  ]),
  selenium: new Set([
    'selenium_compact_level', 'selenium_z_order_mode', 'selenium_auto_close_on_task_done', 'selenium_chrome_user_data_dir'
  ])
};

function scheduleAutoSave(bucket, saveFn) {
  if (!bucket || typeof saveFn !== 'function') {
    return;
  }

  const timer = state.autoSaveTimers[bucket];
  if (timer) {
    window.clearTimeout(timer);
  }

  state.autoSaveTimers[bucket] = window.setTimeout(async () => {
    state.autoSaveTimers[bucket] = null;
    try {
      await saveFn();
    } catch (error) {
      console.debug(`Auto-save failed for ${bucket}`, error);
    }
  }, 180);
}

function resolveAutoSaveBucket(id) {
  if (!id) {
    return null;
  }

  for (const [bucket, ids] of Object.entries(AUTO_SAVE_BUCKETS)) {
    if (ids.has(id)) {
      return bucket;
    }
  }

  return null;
}

function bindAutoSaveEvents() {
  if (state.autoSaveBound) {
    return;
  }

  document.body.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const tag = target.tagName.toLowerCase();
    const type = String(target.getAttribute('type') || '').toLowerCase();
    const isInputControl = tag === 'input' && !['button', 'submit', 'reset', 'file', 'image', 'hidden'].includes(type);
    const isAutoSaveControl = tag === 'select' || tag === 'textarea' || isInputControl;
    if (!isAutoSaveControl) {
      return;
    }

    const bucket = resolveAutoSaveBucket(target.id || '');
    if (!bucket) {
      return;
    }

    if (bucket === 'settings') {
      scheduleAutoSave(bucket, () => saveSettings(false));
    } else if (bucket === 'import') {
      scheduleAutoSave(bucket, () => saveImportSettings(false));
    } else if (bucket === 'fileShared') {
      scheduleAutoSave(bucket, async () => {
        await persistSharedFileSettings();
        renderImportSettings({ import_ui: state.data?.import_ui || {} });
        renderTaskRuntimePills(state.data || {});
      });
    } else if (bucket === 'detachedMain') {
      const mode = getDetachedMirrorMode(target.id || '');
      if (mode) {
        syncDetachedMirrorControls(mode);
      }
      scheduleAutoSave('settings', () => saveSettings(false));
    } else if (bucket === 'record') {
      scheduleAutoSave(bucket, () => saveRecordSettings(false));
    } else if (bucket === 'selenium') {
      scheduleAutoSave(bucket, () => saveSeleniumSettings(false));
    }
  });

  document.body.addEventListener('input', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const bucket = resolveAutoSaveBucket(target.id || '');
    if (bucket === 'detachedMain') {
      const mode = getDetachedMirrorMode(target.id || '');
      if (mode) {
        syncDetachedMirrorControls(mode);
      }
      scheduleAutoSave('settings', () => saveSettings(false));
      return;
    }

    if (bucket === 'settings' && DETACHED_WINDOW_PANEL_KEYS.includes(target.id || '')) {
      scheduleAutoSave('settings', () => saveSettings(false));
    }
  });

  state.autoSaveBound = true;
}

async function createDetachedWindow(modeOverride = null) {
  const mode = normalizeDetachedMode(modeOverride || state.detachedModeSelected);
  try {
    state.detachedModeSelected = mode;
    const modeLabel = mode === 'tc' ? '转写' : '翻译';
    const result = await apiCall('toggle_detached_window', mode);
    if (result && result.status === 'closed') {
      state.detachedOpen[mode] = false;
      renderDetachedWindowOverview(state.data || {});
      console.log(`已关闭${modeLabel}独立窗口`);
      return;
    }

    state.detachedOpen[mode] = true;
    await apiCall('update_detached_config', mode);
    renderDetachedWindowOverview(state.data || {});
    console.log(`Created ${modeLabel} detached window:`, result);
    console.log(`已打开${modeLabel}独立窗口`);
  } catch (error) {
    console.error('创建独立窗口失败:', error);
  }
}

async function controlDetachedWindow(action, modeOverride = null) {
  const mode = normalizeDetachedMode(modeOverride || state.detachedModeSelected);
  const modeLabel = mode === 'tc' ? '转写' : '翻译';
  try {
    state.detachedModeSelected = mode;
    const actionNameMap = {
      show: 'show_detached_window',
      hide: 'hide_detached_window',
      close: 'close_detached_window',
    };
    const apiName = actionNameMap[action];
    if (!apiName) {
      throw new Error(`Unsupported detached action: ${action}`);
    }

    const result = await apiCall(apiName, mode);
    if (action === 'show') {
      state.detachedOpen[mode] = true;
    } else if (action === 'close') {
      state.detachedOpen[mode] = false;
    }
    renderDetachedWindowOverview(state.data || {});
    console.log(`${modeLabel}独立窗口操作完成:`, result);
  } catch (error) {
    console.error(`${modeLabel}独立窗口操作失败:`, error);
  }
}

async function startRecording() {
  try {
    const mainUi = state.data?.main_ui || {};
    const device = els.inputMode?.value || mainUi.selected_input || 'mic';
    const langSource = els.sourceLangMain?.value || mainUi.selected_source || 'English';
    const langTarget = els.targetLangMain?.value || mainUi.selected_target || 'Indonesian';
    const engine = els.translateEngineMain?.value || mainUi.selected_engine || 'Google Translate';
    const isTc = els.transcribeMain ? els.transcribeMain.checked : true;
    const isTl = els.translateMain ? els.translateMain.checked : true;

    if (!isTc && !isTl) {
      console.warn('请先启用“转写”或“翻译”');
      return;
    }

    // Persist current main settings before starting recording, but do not block on a full refresh.
    await saveSettings(false);
    
    const result = await apiCall('start_recording', device, langSource, langTarget, engine, isTc, isTl);
    
    if (result.ok) {
      syncRecordingButton(await apiCall('get_recording_state'));
      
      console.log('录制已开始:', result);
    } else {
      throw new Error((result && result.message) || '启动录制失败');
    }
  } catch (error) {
    console.error('启动录制出错:', error);
    try {
      syncRecordingButton(await apiCall('get_recording_state'));
      await refreshTaskState();
    } catch (_syncError) {
      // ignore follow-up sync errors
    }
    await showAlertDialog({
      title: '启动录制失败',
      message: `${error && error.message ? error.message : error}`,
      tone: 'danger',
    });
  }
}

async function stopRecording() {
  try {
    const result = await apiCall('stop_recording');
    const latestState = await apiCall('get_recording_state');
    syncRecordingButton(latestState);

    if (result.ok) {
      console.log('录制已停止:', result);
    } else {
      console.error('停止录制失败:', result.message);
    }
  } catch (error) {
    console.error('停止录制出错:', error);
  }
}

async function startImportQueue() {
  try {
    // optimistic UI: immediately show as active
    syncImportButton(true);
    await saveImportSettings();
    const res = await apiCall('start_import_queue');
    if (!res || res.ok === false) {
      // revert optimistic state on failure
      syncImportButton(false);
      throw new Error((res && res.message) || '开始处理失败');
    }
    await refreshFileProcessingState();
    console.log('File import started', res);
  } catch (err) {
    console.error('startImportQueue error', err);
  }
}

async function stopImportQueue() {
  try {
    const res = await apiCall('stop_import_queue');
    if (res && res.ok) {
      // optimistic: mark button as stopped
      syncImportButton(false);
    } else {
      console.error('stopImportQueue failed', res && res.message);
    }
    await refreshFileProcessingState();
  } catch (err) {
    console.error('stopImportQueue error', err);
  }
}


async function openDirectory(kind) {
  await apiCall('open_directory', kind);
}

async function pickDirectory(kind) {
  const result = await apiCall('select_directory', kind);
  if (!result || result.ok === false) {
    const message = result && result.message ? String(result.message) : '目录选择失败';
    if (message === 'No folder selected') {
      return;
    }
    throw new Error(message);
  }

  await refreshState();
  if (kind === 'model') {
    await refreshModelManagerState(getSelectedModelManagerEngine());
  }
}

function startTaskRefresh() {
  if (state.taskTimer !== null) {
    return;
  }
  state.taskTimer = window.setInterval(async () => {
    try {
      await refreshTaskState();
    } catch (error) {
      console.debug('Task refresh skipped', error);
    }
  }, 250);
}

function stopTaskRefresh() {
  if (state.taskTimer !== null) {
    window.clearInterval(state.taskTimer);
    state.taskTimer = null;
  }
}

function switchWorkflowTab(targetId) {
  if (!targetId) {
    return;
  }

  const tabButtons = Array.from(document.querySelectorAll('.tab-btn'));
  const tabPanels = Array.from(document.querySelectorAll('.tab-panel'));

  for (const button of tabButtons) {
    const isActive = button.dataset.tabTarget === targetId;
    button.classList.toggle('is-active', isActive);
    button.setAttribute('aria-selected', isActive ? 'true' : 'false');
  }

  for (const panel of tabPanels) {
    panel.classList.toggle('is-active', panel.id === targetId);
  }
}

async function hideToTray() {
  const suppressPrompt = Boolean(
    els.supressHiddenToTray ? els.supressHiddenToTray.checked : (state.data?.settings?.supress_hidden_to_tray ?? false)
  );
  if (!suppressPrompt) {
    const confirmed = await showConfirmDialog({
      title: '隐藏到托盘',
      message: '主窗口会隐藏到系统托盘，可通过托盘入口重新打开。',
      confirmLabel: '隐藏到托盘',
    });
    if (!confirmed) {
      return;
    }
  }

  const result = await apiCall('hide_main_window_to_tray');
  if (!result || result.ok === false) {
    throw new Error((result && result.message) || '隐藏到托盘失败');
  }
}

async function saveMainWindowGeometry() {
  await apiCall('save_main_window_geometry', true);
  await refreshState();
}

async function showMainWindow() {
  await apiCall('show_main_window');
}

async function openCurrentLogFile() {
  const about = state.data?.about || {};
  const logDir = String(about.log_dir || state.data?.settings?.dir_log || '').trim();
  const logFile = String(els.currentLog?.value || about.log_file || '').trim();
  if (!logFile) {
    throw new Error('当前日志文件不可用');
  }
  if (logDir) {
    await apiCall('open_link', `file:///${logDir.replace(/\\\\/g, '/').replace(/\\/g, '/')}/${logFile}`);
    return;
  }
  await openDirectory('log');
}

async function quitApp() {
  const confirmed = await showConfirmDialog({
    title: '退出程序',
    message: '这会关闭主窗口、独立窗口以及后台翻译进程。',
    confirmLabel: '退出程序',
    tone: 'danger',
  });
  if (!confirmed) {
    return;
  }
  await apiCall('quit_app');
}

function getSettingsPanels() {
  return Array.from(document.querySelectorAll('#settings-shell .advanced-panel'));
}

function normalizeSearchText(value) {
  return String(value || '').trim().toLowerCase();
}

function getSettingsPanelTitle(summary) {
  if (!summary) {
    return '';
  }
  const titleNode = summary.querySelector('.settings-panel-title');
  if (titleNode) {
    return String(titleNode.textContent || '').trim();
  }
  for (const node of Array.from(summary.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = String(node.textContent || '').trim();
      if (text) {
        return text;
      }
    }
  }
  return String(summary.textContent || '').trim();
}

function ensureSettingsPanelSummaryStructure(summary) {
  if (!summary) {
    return { title: '', meta: null };
  }
  const title = getSettingsPanelTitle(summary);
  let heading = summary.querySelector('.settings-panel-heading');
  let titleNode = summary.querySelector('.settings-panel-title');
  let meta = summary.querySelector('.settings-panel-meta');

  if (!heading) {
    heading = document.createElement('span');
    heading.className = 'settings-panel-heading';
  }
  if (!titleNode) {
    titleNode = document.createElement('span');
    titleNode.className = 'settings-panel-title';
  }
  titleNode.textContent = title;
  if (!heading.contains(titleNode)) {
    heading.appendChild(titleNode);
  }

  for (const node of Array.from(summary.childNodes)) {
    if (node === heading || node === meta) {
      continue;
    }
    if (node.nodeType === Node.TEXT_NODE && String(node.textContent || '').trim()) {
      summary.removeChild(node);
      continue;
    }
    if (node.nodeType === Node.ELEMENT_NODE && node.classList) {
      if (node.classList.contains('settings-panel-heading') || node.classList.contains('settings-panel-title')) {
        summary.removeChild(node);
      }
    }
  }

  summary.insertBefore(heading, summary.firstChild);
  if (meta && meta.parentElement !== summary) {
    summary.appendChild(meta);
  }

  return { title, meta };
}

function jumpToSettingsSection(sectionTitle) {
  const normalized = normalizeSearchText(sectionTitle);
  if (!normalized) {
    return false;
  }

  const panel = getSettingsPanels().find((item) => {
    const summary = item.querySelector('summary');
    return normalizeSearchText(getSettingsPanelTitle(summary)).includes(normalized);
  });
  if (!panel) {
    return false;
  }

  const resolvedTitle = getSettingsPanelTitle(panel.querySelector('summary'));
  panel.open = true;
  panel.classList.add('settings-panel-match');
  window.setTimeout(() => panel.classList.remove('settings-panel-match'), 1600);
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (els.settingsSearchMeta) {
    els.settingsSearchMeta.textContent = resolvedTitle ? `已跳转：${resolvedTitle}` : '已跳转到目标设置';
  }
  updatePageScrollIndicator();
  return resolvedTitle || true;
}

function collectSearchTerms(root, selectors) {
  if (!root) {
    return '';
  }

  const parts = [];
  for (const selector of selectors) {
    for (const node of root.querySelectorAll(selector)) {
      const fragments = [];
      const text = String(node.textContent || '').trim();
      if (text) {
        fragments.push(text);
      }
      if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {
        const value = String(node.value || '').trim();
        const placeholder = String(node.placeholder || '').trim();
        if (value) {
          fragments.push(value);
        }
        if (placeholder) {
          fragments.push(placeholder);
        }
      } else if (node instanceof HTMLSelectElement) {
        const value = String(node.value || '').trim();
        if (value) {
          fragments.push(value);
        }
        const selectedText = Array.from(node.selectedOptions || [])
          .map((option) => String(option.textContent || '').trim())
          .filter(Boolean)
          .join(' ');
        if (selectedText) {
          fragments.push(selectedText);
        }
      }
      const ariaLabel = String(node.getAttribute?.('aria-label') || '').trim();
      if (ariaLabel) {
        fragments.push(ariaLabel);
      }
      for (const fragment of fragments) {
        parts.push(fragment);
      }
    }
  }
  return normalizeSearchText(parts.join(' '));
}

function applySettingsFilter(rawQuery = '') {
  const query = normalizeSearchText(rawQuery);
  const panels = getSettingsPanels();
  const workbenchCards = Array.from(document.querySelectorAll('.settings-workbench-card'));
  let visibleCount = 0;
  let workbenchMatchCount = 0;

  for (const panel of panels) {
    const summary = panel.querySelector('summary');
    const haystack = normalizeSearchText([
      getSettingsPanelTitle(summary),
      collectSearchTerms(panel, [
        '.settings-panel-title',
        '.settings-panel-meta',
        '.settings-section-title',
        '.settings-workbench-section-title',
        '.settings-workbench-section-meta',
        'label > span',
        'label.toggle-row',
        'button',
      ]),
    ].join(' '));
    const matched = !query || haystack.includes(query);
    panel.classList.toggle('settings-panel-hidden', !matched);
    panel.classList.toggle('settings-panel-match', Boolean(query && matched));
    if (matched) {
      visibleCount += 1;
      if (query) {
        panel.open = true;
      }
    }
  }

  for (const card of workbenchCards) {
    const haystack = collectSearchTerms(card, [
      '.settings-workbench-title',
      '.settings-workbench-meta',
      '.settings-workbench-section-title',
      '.settings-workbench-section-meta',
      'label > span',
      'label.toggle-row',
    ]);
    const matched = Boolean(query && haystack.includes(query));
    card.classList.toggle('settings-workbench-card-match', matched);
    card.classList.toggle('settings-workbench-card-hidden', Boolean(query && !matched));
    if (matched) {
      workbenchMatchCount += 1;
    }
  }

  const shortcuts = Array.from(document.querySelectorAll('[data-settings-jump]'));
  for (const shortcut of shortcuts) {
    const target = normalizeSearchText(shortcut.getAttribute('data-settings-jump') || '');
    shortcut.classList.toggle('is-active', Boolean(query && target.includes(query)));
  }

  if (els.settingsSearchMeta) {
    els.settingsSearchMeta.textContent = query
      ? `筛选结果：${visibleCount} 个设置面板，${workbenchMatchCount} 个工作台卡片`
      : '显示全部设置';
  }

  updatePageScrollIndicator();
}

function switchSidebarMenu(target) {
  const menuButtons = Array.from(document.querySelectorAll('.menu-item[data-nav-target]'));
  for (const button of menuButtons) {
    button.classList.toggle('is-active', button.dataset.navTarget === target);
  }

  const showSettings = target === 'settings';
  if (els.workspaceHub) {
    els.workspaceHub.classList.toggle('is-hidden', showSettings);
    els.workspaceHub.style.display = '';
  }
  if (els.settingsShell) {
    els.settingsShell.classList.toggle('is-hidden', !showSettings);
  }

  if (els.dashboardContent) {
    els.dashboardContent.scrollTop = 0;
  }

  if (showSettings) {
    applySettingsFilter(els.settingsSearch ? els.settingsSearch.value : '');
  }

  if (!showSettings) {
    const tabMap = {
      realtime: 'tab-realtime',
      file: 'tab-file',
      model: 'tab-model',
    };
    switchWorkflowTab(tabMap[target] || 'tab-realtime');
  }
}

function bindEvents() {
  if (state.eventsBound) {
    return;
  }

  document.body.addEventListener('click', async (event) => {
    const button = event.target.closest('button');
    if (!button) {
      return;
    }

    const navTarget = button.dataset.navTarget;
    if (navTarget) {
      switchSidebarMenu(navTarget);
      return;
    }

    const tabTarget = button.dataset.tabTarget;
    if (tabTarget) {
      switchWorkflowTab(tabTarget);
      return;
    }

    const settingsJump = button.dataset.settingsJump;
    if (settingsJump) {
      if (els.settingsSearch) {
        els.settingsSearch.value = '';
      }
      switchSidebarMenu('settings');
      applySettingsFilter('');
      if (!jumpToSettingsSection(settingsJump) && els.settingsSearchMeta) {
        els.settingsSearchMeta.textContent = `未找到设置：${settingsJump}`;
      }
      return;
    }

    const action = button.dataset.action;
    const openDir = button.dataset.openDir;

    try {
      if (openDir) {
        await openDirectory(openDir);
      } else if (action === 'refresh') {
        await refreshState();
      } else if (action === 'show-main-window') {
        await showMainWindow();
      } else if (action === 'refresh-audio-devices') {
        await refreshAudioSourceOptions(els.hostAPI ? els.hostAPI.value : '', true);
      } else if (action === 'save-window-geometry') {
        await saveMainWindowGeometry();
      } else if (action === 'pick-export-dir') {
        await pickDirectory('export');
      } else if (action === 'pick-log-dir') {
        await pickDirectory('log');
      } else if (action === 'pick-model-dir') {
        await pickDirectory('model');
      } else if (action === 'pick-selenium-chrome-dir') {
        await pickDirectory('selenium_chrome');
      } else if (action === 'add-files-to-queue') {
        // Open file dialog and add selected files to backend queue
        const result = await apiCall('add_files_to_import_queue');
        if (!result || result.ok === false) {
          throw new Error((result && result.message) || '导入失败');
        }
        // update queue UI from response
        const files = result.files || [];
        state.fileImportQueue = files;
        updateFileImportListUI(files);
        await refreshState();
      } else if (action === 'clear-import-queue') {
        // Clear the backend queue
        const r = await apiCall('clear_import_queue');
        if (!r || r.ok === false) {
          throw new Error((r && r.message) || '清空队列失败');
        }
        state.fileImportQueue = [];
        updateFileImportListUI([]);
        await refreshState();
      } else if (action === 'remove-file-from-queue') {
        // Remove a specific file from queue by index
        const idxAttr = button.getAttribute('data-index');
        const idx = idxAttr ? parseInt(idxAttr, 10) : NaN;
        if (!Number.isFinite(idx)) {
          throw new Error('无效索引');
        }
        const r = await apiCall('remove_file_from_import_queue', idx);
        if (!r || r.ok === false) {
          throw new Error((r && r.message) || '删除失败');
        }
        const files2 = r.files || [];
        state.fileImportQueue = files2;
        updateFileImportListUI(files2);
        await refreshState();
      } else if (action === 'start-import-queue') {
        await startImportQueue();
        await refreshState();
      } else if (action === 'stop-import-queue') {
        await stopImportQueue();
        await refreshState();
      } else if (action === 'import-files') {
        await saveImportSettings();
        const importResult = await apiCall('import_files');
        if (!importResult || importResult.ok === false) {
          throw new Error((importResult && importResult.message) || '文件导入未启动');
        }
        await refreshState();
      } else if (action === 'hide-to-tray') {
        await hideToTray();
      } else if (action === 'quit-app') {
        await quitApp();
      } else if (action === 'open-repo') {
        await apiCall('open_link', 'https://github.com/Dadangdut33/Speech-Translate');
      } else if (action === 'open-current-log') {
        await openCurrentLogFile();
      } else if (action === 'open-filter-rec') {
        await apiCall('open_hallucination_filter', 'rec');
      } else if (action === 'open-filter-file') {
        await apiCall('open_hallucination_filter', 'file');
      } else if (action === 'refresh-log') {
        const payload = await apiCall('refresh_log');
        if (els.currentLog) els.currentLog.value = String(payload?.file || '');
        if (els.logContent) {
          els.logContent.textContent = String(payload?.content || '') || '当前日志为空。';
          if (els.autoScrollLog && els.autoScrollLog.checked) {
            els.logContent.scrollTop = els.logContent.scrollHeight;
          }
        }
      } else if (action === 'clear-log') {
        const payload = await apiCall('clear_log');
        if (els.currentLog) els.currentLog.value = String(payload?.file || '');
        if (els.logContent) {
          els.logContent.textContent = String(payload?.content || '') || '当前日志为空。';
          els.logContent.scrollTop = els.logContent.scrollHeight;
        }
      } else if (action === 'save-selenium-settings') {
        await saveSeleniumSettings();
      } else if (action === 'save-all-settings') {
        await saveAllSettings();
      } else if (action === 'save-settings') {
        await saveSettings();
      } else if (action === 'check-model-current') {
        await checkCurrentModelManagerState();
      } else if (action === 'check-all-models') {
        await checkAllModelManagerState(getSelectedModelManagerEngine());
      } else if (action === 'download-model-row') {
        const rowModel = button.dataset.model || '';
        const rowEngine = button.dataset.engine || getSelectedModelManagerEngine();
        if (!rowModel) {
          throw new Error('缺少模型名称，无法下载');
        }
        const res = await apiCall('download_model', rowModel, rowEngine);
        if (!res || res.ok === false) {
          throw new Error((res && res.message) || '模型下载启动失败');
        }
        await refreshTaskState();
        await refreshModelManagerState(rowEngine);
        startModelProgressPolling(rowEngine);
      } else if (action === 'save-record-settings') {
        await saveRecordSettings();
      } else if (action === 'create-detached-window') {
        await createDetachedWindow();
      } else if (action === 'create-detached-tc') {
        await createDetachedWindow('tc');
      } else if (action === 'create-detached-tl') {
        await createDetachedWindow('tl');
      } else if (action === 'show-detached-tc') {
        await controlDetachedWindow('show', 'tc');
      } else if (action === 'hide-detached-tc') {
        await controlDetachedWindow('hide', 'tc');
      } else if (action === 'close-detached-tc') {
        await controlDetachedWindow('close', 'tc');
      } else if (action === 'show-detached-tl') {
        await controlDetachedWindow('show', 'tl');
      } else if (action === 'hide-detached-tl') {
        await controlDetachedWindow('hide', 'tl');
      } else if (action === 'close-detached-tl') {
        await controlDetachedWindow('close', 'tl');
      } else if (action === 'open-detached-settings-tc' || action === 'open-detached-settings-tl') {
        switchSidebarMenu('settings');
        applySettingsFilter('');
        if (!jumpToSettingsSection(DETACHED_SETTINGS_SECTION_TITLE)) {
          throw new Error(`找不到设置分区：${DETACHED_SETTINGS_SECTION_TITLE}`);
        }
      } else if (action === 'save-import-settings') {
        await saveImportSettings();
      } else if (action === 'load-model') {
        await loadRuntimeModel();
      } else if (action === 'load-main-model') {
        await loadMainRuntimeModel();
      } else if (action === 'start-recording') {
        await startRecording();
        await refreshState();
      } else if (action === 'stop-recording') {
        await stopRecording();
        await refreshState();
      }
    } catch (error) {
      console.error(error);
      try {
        await refreshTaskState();
      } catch (_syncError) {
        // ignore follow-up sync errors
      }
      const node = $('model-status-card');
      if (node) {
        node.innerHTML = `<div class="state-row"><div class="state-key error">操作失败</div><div class="state-value">${escapeHtml(error.message || String(error))}</div></div>`;
      }
    }
  });

    if (els.hostAPI) {
      els.hostAPI.addEventListener('change', async () => {
        await refreshAudioSourceOptions(els.hostAPI.value, true);
      });
    }

    bindToolbarMirrorValues([
      [els.httpProxyToolbar, els.httpProxy],
      [els.httpsProxyToolbar, els.httpsProxy],
      [els.libreLinkToolbar, els.libreLink],
      [els.libreApiKeyToolbar, els.libreApiKey],
      [els.exportFormatToolbar, els.exportFormat],
      [els.segmentMaxWordsToolbar, els.segmentMaxWords],
      [els.segmentMaxCharsToolbar, els.segmentMaxChars],
      [els.segmentSplitOrNewlineToolbar, els.segmentSplitOrNewline],
      [els.transcribeRateToolbar, els.transcribeRate],
      [els.decodingPresetToolbar, els.decodingPreset],
      [els.temperatureToolbar, els.temperature],
      [els.bestOfToolbar, els.bestOf],
      [els.beamSizeToolbar, els.beamSize],
      [els.noSpeechThresholdToolbar, els.noSpeechThreshold],
      [els.logprobThresholdToolbar, els.logprobThreshold],
      [els.patienceToolbar, els.patience],
      [els.compressionRatioThresholdToolbar, els.compressionRatioThreshold],
      [els.suppressTokensToolbar, els.suppressTokens],
      [els.hostAPIToolbar, els.hostAPI],
      [els.modelDevicePreferenceToolbar, els.modelDevicePreference],
    ]);
    bindToolbarMirrorChecks([
      [els.httpProxyEnableToolbar, els.httpProxyEnable],
      [els.httpsProxyEnableToolbar, els.httpsProxyEnable],
      [els.autoOpenDirExportToolbar, els.autoOpenDirExport],
      [els.exportTxtToolbar, els.exportTxt],
      [els.exportSrtToolbar, els.exportSrt],
      [els.exportVttToolbar, els.exportVtt],
      [els.exportJsonToolbar, els.exportJson],
      [els.exportAssToolbar, els.exportAss],
      [els.exportCsvToolbar, els.exportCsv],
      [els.exportTsvToolbar, els.exportTsv],
      [els.exportMp4Toolbar, els.exportMp4],
      [els.recAskConfirmationFirstToolbar, els.recAskConfirmationFirst],
      [els.supressHiddenToTrayToolbar, els.supressHiddenToTray],
      [els.useEnModelToolbar, els.useEnModel],
      [els.suppressBlankToolbar, els.suppressBlank],
      [els.fp16Toolbar, els.fp16],
      [els.useTempAltToolbar, els.useTempAlt],
      [els.keepTempToolbar, els.keepTemp],
      [els.fileUseOfficialWhisperToolbar, els.fileUseOfficialWhisper],
      [els.supressRecordWarningToolbar, els.supressRecordWarning],
      [els.debugRealtimeRecordToolbar, els.debugRealtimeRecord],
      [els.debugTranslateToolbar, els.debugTranslate],
      [els.segmentEvenSplitToolbar, els.segmentEvenSplit],
      [els.segmentLevelToolbar, els.segmentLevel],
      [els.wordLevelToolbar, els.wordLevel],
    ]);
    bindSharedFieldMirrors();
    if (els.hostAPIToolbar) {
      els.hostAPIToolbar.addEventListener('change', async () => {
        if (els.hostAPI) {
          els.hostAPI.value = els.hostAPIToolbar.value;
        }
        await refreshAudioSourceOptions(els.hostAPIToolbar.value, true);
      });
    }

  bindSelectSettingPersistence(els.mic, 'set_record_setting', 'mic');
  bindSelectSettingPersistence(els.speaker, 'set_record_setting', 'speaker');

  if (els.modelManagerEngineBar) {
    els.modelManagerEngineBar.addEventListener('click', async (event) => {
      const tab = event.target && event.target.closest ? event.target.closest('.model-engine-tab') : null;
      if (!tab) {
        return;
      }
      const engine = tab.getAttribute('data-engine-option') || 'whisper';
      setSelectedModelManagerEngine(engine);
      await checkAllModelManagerState(engine);
    });
  }

  if (els.modelImportEngineBar) {
    els.modelImportEngineBar.addEventListener('click', async (event) => {
      const tab = event.target && event.target.closest ? event.target.closest('.model-engine-tab') : null;
      if (!tab) {
        return;
      }
      const engine = tab.getAttribute('data-import-engine-option') || 'whisper';
      const previous = getSelectedImportModelEngine();
      if (engine === previous) {
        return;
      }
      setSelectedImportModelEngine(engine);
      await apiCall('set_setting', 'use_faster_whisper', engine === 'faster-whisper');
      await refreshState();
    });
  }

  bindSelectSettingPersistence(els.modelImport, 'set_import_setting', 'model_f_import', {
    onError(error) {
      console.error('保存模型选择失败', error);
    },
  });

  // 加载按钮由页面的 data-action 统一事件处理器处理（action='load-model'），无需额外绑定。

  state.eventsBound = true;
}

async function init() {
  if (state.initialized) {
    return state.initInFlight || Promise.resolve();
  }
  if (state.initInFlight) {
    return state.initInFlight;
  }

  state.initInFlight = (async () => {
    els.dirExport = $('dir_export');
    els.dirExportFile = $('dir_export_file');
    els.dirModel = $('dir_model');
    els.dirLog = $('dir_log');
    els.currentLog = $('current_log');
    els.logContent = $('log_content');
    els.logLevel = $('log_level');
    els.mainWindowSize = $('mw_size');
    els.autoScrollLog = $('auto_scroll_log');
    els.autoRefreshLog = $('auto_refresh_log');
    els.httpProxyEnable = $('http_proxy_enable');
    els.httpProxy = $('http_proxy');
    els.httpsProxyEnable = $('https_proxy_enable');
    els.httpsProxy = $('https_proxy');
    els.libreLink = $('libre_link');
    els.libreApiKey = $('libre_api_key');
    els.httpProxyEnableToolbar = $('http_proxy_enable_toolbar');
    els.httpProxyToolbar = $('http_proxy_toolbar');
    els.httpsProxyEnableToolbar = $('https_proxy_enable_toolbar');
    els.httpsProxyToolbar = $('https_proxy_toolbar');
    els.libreLinkToolbar = $('libre_link_toolbar');
    els.libreApiKeyToolbar = $('libre_api_key_toolbar');
    els.autoOpenDirExport = $('auto_open_dir_export');
    els.autoOpenDirExportFile = $('auto_open_dir_export_file');
    els.exportFormat = $('export_format');
    els.autoOpenDirExportToolbar = $('auto_open_dir_export_toolbar');
    els.exportFormatToolbar = $('export_format_toolbar');
    els.removeRepetitionFileImport = $('remove_repetition_file_import');
    els.removeRepetitionAmount = $('remove_repetition_amount');
    els.segmentMaxWords = $('segment_max_words');
    els.segmentMaxWordsToolbar = $('segment_max_words_toolbar');
    els.segmentMaxChars = $('segment_max_chars');
    els.segmentMaxCharsToolbar = $('segment_max_chars_toolbar');
    els.segmentSplitOrNewline = $('segment_split_or_newline');
    els.segmentSplitOrNewlineToolbar = $('segment_split_or_newline_toolbar');
    els.segmentEvenSplit = $('segment_even_split');
    els.segmentEvenSplitToolbar = $('segment_even_split_toolbar');
    els.segmentLevel = $('segment_level');
    els.segmentLevelToolbar = $('segment_level_toolbar');
    els.wordLevel = $('word_level');
    els.wordLevelToolbar = $('word_level_toolbar');
    els.useEnModel = $('use_en_model');
    els.useEnModelToolbar = $('use_en_model_toolbar');
    els.decodingPreset = $('decoding_preset');
    els.decodingPresetToolbar = $('decoding_preset_toolbar');
    els.temperature = $('temperature');
    els.temperatureToolbar = $('temperature_toolbar');
    els.bestOfToolbar = $('best_of_toolbar');
    els.beamSizeToolbar = $('beam_size_toolbar');
    els.noSpeechThresholdToolbar = $('no_speech_threshold_toolbar');
    els.logprobThresholdToolbar = $('logprob_threshold_toolbar');
    els.patienceToolbar = $('patience_toolbar');
    els.compressionRatioThresholdToolbar = $('compression_ratio_threshold_toolbar');
    els.suppressTokensToolbar = $('suppress_tokens_toolbar');
    els.bestOf = $('best_of');
    els.beamSize = $('beam_size');
    els.patience = $('patience');
    els.compressionRatioThreshold = $('compression_ratio_threshold');
    els.logprobThreshold = $('logprob_threshold');
    els.noSpeechThreshold = $('no_speech_threshold');
    els.suppressTokens = $('suppress_tokens');
    els.suppressBlank = $('suppress_blank');
    els.suppressBlankToolbar = $('suppress_blank_toolbar');
    els.fp16 = $('fp16');
    els.fp16Toolbar = $('fp16_toolbar');
    els.initialPrompt = $('initial_prompt');
    els.prefix = $('prefix');
    els.maxInitialTimestamp = $('max_initial_timestamp');
    els.whisperArgs = $('whisper_args');
    els.fileSliceStart = $('file_slice_start');
    els.fileSliceEnd = $('file_slice_end');
    els.autoOpenDirTranslate = $('auto_open_dir_translate');
    els.autoOpenDirTranslateFile = $('auto_open_dir_translate_file');
    els.autoOpenDirRefinement = $('auto_open_dir_refinement');
    els.autoOpenDirRefinementFile = $('auto_open_dir_refinement_file');
    els.autoOpenDirAlignment = $('auto_open_dir_alignment');
    els.autoOpenDirAlignmentFile = $('auto_open_dir_alignment_file');
    els.recAskConfirmationFirst = $('rec_ask_confirmation_first');
    els.recAskConfirmationFirstToolbar = $('rec_ask_confirmation_first_toolbar');
    els.closeToTrayOnClose = $('close_to_tray_on_close');
    els.supressHiddenToTray = $('supress_hidden_to_tray');
    els.supressHiddenToTrayToolbar = $('supress_hidden_to_tray_toolbar');
    els.supressRecordWarning = $('supress_record_warning');
    els.debugRealtimeRecord = $('debug_realtime_record');
    els.debugTranslate = $('debug_translate');
    els.pathFilterRec = $('path_filter_rec');
    els.pathFilterFileImport = $('path_filter_file_import');
    els.colorizePerSegment = $('colorize_per_segment');
    els.colorizePerWord = $('colorize_per_word');
    els.gradientLowConf = $('gradient_low_conf');
    els.gradientHighConf = $('gradient_high_conf');
    els.tbMwTcAutoScroll = $('tb_mw_tc_auto_scroll');
    els.tbMwTcLimitMax = $('tb_mw_tc_limit_max');
    els.tbMwTcLimitMaxPerLine = $('tb_mw_tc_limit_max_per_line');
    els.tbMwTcMax = $('tb_mw_tc_max');
    els.tbMwTcMaxPerLine = $('tb_mw_tc_max_per_line');
    els.tbMwTcFont = $('tb_mw_tc_font');
    els.tbMwTcFontBold = $('tb_mw_tc_font_bold');
    els.tbMwTcFontSize = $('tb_mw_tc_font_size');
    els.tbMwTcFontColor = $('tb_mw_tc_font_color');
    els.tbMwTcUseConfColor = $('tb_mw_tc_use_conf_color');
    els.tbMwTlAutoScroll = $('tb_mw_tl_auto_scroll');
    els.tbMwTlLimitMax = $('tb_mw_tl_limit_max');
    els.tbMwTlLimitMaxPerLine = $('tb_mw_tl_limit_max_per_line');
    els.tbMwTlMax = $('tb_mw_tl_max');
    els.tbMwTlMaxPerLine = $('tb_mw_tl_max_per_line');
    els.tbMwTlFont = $('tb_mw_tl_font');
    els.tbMwTlFontBold = $('tb_mw_tl_font_bold');
    els.tbMwTlFontSize = $('tb_mw_tl_font_size');
    els.tbMwTlFontColor = $('tb_mw_tl_font_color');
    els.tbMwTlUseConfColor = $('tb_mw_tl_use_conf_color');
    els.seleniumCompactLevel = $('selenium_compact_level');
    els.seleniumZOrderMode = $('selenium_z_order_mode');
    els.seleniumAutoCloseOnTaskDone = $('selenium_auto_close_on_task_done');
    els.seleniumChromeUserDataDir = $('selenium_chrome_user_data_dir');
    els.exportTxt = $('export_txt');
    els.exportSrt = $('export_srt');
    els.exportVtt = $('export_vtt');
    els.exportAss = $('export_ass');
    els.exportJson = $('export_json');
    els.exportCsv = $('export_csv');
    els.exportTsv = $('export_tsv');
    els.exportMp4 = $('export_mp4');
    els.exportTxtToolbar = $('export_txt_toolbar');
    els.exportSrtToolbar = $('export_srt_toolbar');
    els.exportVttToolbar = $('export_vtt_toolbar');
    els.exportJsonToolbar = $('export_json_toolbar');
    els.exportAssToolbar = $('export_ass_toolbar');
    els.exportCsvToolbar = $('export_csv_toolbar');
    els.exportTsvToolbar = $('export_tsv_toolbar');
    els.exportMp4Toolbar = $('export_mp4_toolbar');
    els.inputMode = $('input_mode');
    els.backendMain = $('backend_mw');
    els.modelMain = $('model_mw');
    els.sourceLangMain = $('source_lang_mw');
    els.targetLangMain = $('target_lang_mw');
    els.translateEngineMain = $('tl_engine_mw');
    els.transcribeMain = $('transcribe_mw');
    els.translateMain = $('translate_mw');
    els.mainInputPill = $('main-input-pill');
    els.mainModelPill = $('main-model-pill');
    els.mainLangPill = $('main-lang-pill');
    els.mainEnginePill = $('main-engine-pill');
    els.btnLoadMainModel = document.querySelector('button[data-action="load-main-model"]');
    els.hostAPI = $('hostAPI');
    els.hostAPIToolbar = $('hostAPI_toolbar');
    els.mic = $('mic');
    els.speaker = $('speaker');
    els.verboseRecord = $('verbose_record');
    els.modelDevicePreference = $('model_device_preference');
    els.modelDevicePreferenceToolbar = $('model_device_preference_toolbar');
    els.transcribeRate = $('transcribe_rate');
    els.transcribeRateToolbar = $('transcribe_rate_toolbar');
    els.separateWith = $('separate_with');
    els.useTemp = $('use_temp');
    els.useTempAlt = $('use_temp_alt');
    els.useTempAltToolbar = $('use_temp_alt_toolbar');
    els.keepTemp = $('keep_temp');
    els.keepTempToolbar = $('keep_temp_toolbar');
    els.fileUseOfficialWhisper = $('file_use_official_whisper');
    els.fileUseOfficialWhisperToolbar = $('file_use_official_whisper_toolbar');
    els.supressRecordWarningToolbar = $('supress_record_warning_toolbar');
    els.debugRealtimeRecordToolbar = $('debug_realtime_record_toolbar');
    els.debugTranslateToolbar = $('debug_translate_toolbar');
    els.recordInputPill = $('record-input-pill');
    els.recordModePill = $('record-mode-pill');
    els.recordVisualizerCard = $('record_visualizer_card');
    els.recordVisualizerLabel = $('record_visualizer_label');
    els.recordVisualizerFill = $('record_visualizer_fill');
    els.recordVisualizerThreshold = $('record_visualizer_threshold');
    els.recordVisualizerDb = $('record_visualizer_db');
    els.recordVisualizerThresholdText = $('record_visualizer_threshold_text');
    els.micSampleRate = $('sample_rate_mic');
    els.micChunkSize = $('chunk_size_mic');
    els.micChannels = $('channels_mic');
    els.micAutoSampleRate = $('auto_sample_rate_mic');
    els.micAutoChannels = $('auto_channels_mic');
    els.micMinInputLength = $('min_input_length_mic');
    els.micMaxBuffer = $('max_buffer_mic');
    els.micMaxSentences = $('max_sentences_mic');
    els.micNoLimit = $('mic_no_limit');
    els.micThresholdEnable = $('threshold_enable_mic');
    els.micThresholdAuto = $('threshold_auto_mic');
    els.micAutoBreakBuffer = $('auto_break_buffer_mic');
    els.micThresholdAutoLevel = $('threshold_auto_level_mic');
    els.micThresholdAutoSilero = $('threshold_auto_silero_mic');
    els.micThresholdSileroMin = $('threshold_silero_mic_min');
    els.micThresholdDb = $('threshold_db_mic');
    els.micThresholdDbValue = $('threshold_db_mic_value');
    els.speakerSampleRate = $('sample_rate_speaker');
    els.speakerChunkSize = $('chunk_size_speaker');
    els.speakerChannels = $('channels_speaker');
    els.speakerAutoSampleRate = $('auto_sample_rate_speaker');
    els.speakerAutoChannels = $('auto_channels_speaker');
    els.speakerMinInputLength = $('min_input_length_speaker');
    els.speakerMaxBuffer = $('max_buffer_speaker');
    els.speakerMaxSentences = $('max_sentences_speaker');
    els.speakerNoLimit = $('speaker_no_limit');
    els.speakerThresholdEnable = $('threshold_enable_speaker');
    els.speakerThresholdAuto = $('threshold_auto_speaker');
    els.speakerAutoBreakBuffer = $('auto_break_buffer_speaker');
    els.speakerThresholdAutoLevel = $('threshold_auto_level_speaker');
    els.speakerThresholdAutoSilero = $('threshold_auto_silero_speaker');
    els.speakerThresholdSileroMin = $('threshold_silero_speaker_min');
    els.speakerThresholdDb = $('threshold_db_speaker');
    els.speakerThresholdDbValue = $('threshold_db_speaker_value');
    els.modelImport = $('model_f_import');
    els.modelImportEngineBar = $('model-import-engine-bar');
    els.btnLoadModel = $('btn-load-model');
    els.engineImport = $('tl_engine_f_import');
    els.sourceImport = $('source_lang_f_import');
    els.targetImport = $('target_lang_f_import');
    els.transcribeImport = $('transcribe_f_import');
    els.translateImport = $('translate_f_import');
    els.importModelPill = $('import-model-pill');
    els.importEnginePill = $('import-engine-pill');
    els.modelManagerEngineBar = $('model-manager-engine-bar');
    els.modelManagerDirPill = $('model-manager-dir-pill');
    els.modelManagerSelectionPill = $('model-manager-selection-pill');
    els.modelManagerCachePill = $('model-manager-cache-pill');
    els.modelManagerDownloadPill = $('model-manager-download-pill');
    els.fileImportList = $('file_import_list');
    els.btnImportStart = $('btn-import-start');
    els.modelManagerHint = $('model-manager-hint');
    els.modelStatusCard = $('model-status-card');
    els.globalModelState = $('global-model-state');
    els.globalModelMeta = $('global-model-meta');
    els.globalTaskState = $('global-task-state');
    els.globalTaskMessage = $('global-task-message');
    els.globalTaskProgressText = $('global-task-progress-text');
    els.globalTaskProgressFill = $('global-task-progress-fill');
    els.globalTaskProgressWrap = $('global-task-progress-wrap');
    els.realtimeModelState = $('realtime-model-state');
    els.realtimeModelMeta = $('realtime-model-meta');
    els.realtimeTaskState = $('realtime-task-state');
    els.realtimeTaskMessage = $('realtime-task-message');
    els.realtimeRecordingTimer = $('realtime-recording-timer');
    els.realtimeRecordingBuffer = $('realtime-recording-buffer');
    els.realtimeRecordingSentences = $('realtime-recording-sentences');
    els.realtimeRecordingDevice = $('realtime-recording-device');
    els.modelSelectionRuntime = $('model-selection-runtime');
    els.modelSelectionRuntimeMeta = $('model-selection-runtime-meta');
    els.fileImportQueueCount = $('file-import-queue-count');
    els.fileImportProcessingState = $('file-import-processing-state');
    els.fileImportLanguageState = $('file-import-language-state');
    els.fileImportExportDir = $('file-import-export-dir');
    els.fileImportExportMeta = $('file-import-export-meta');
    els.fileImportExportFormat = $('file-import-export-format');
    els.fileImportExportFormatMeta = $('file-import-export-format-meta');
    els.fileImportSliceRange = $('file-import-slice-range');
    els.fileImportSliceMeta = $('file-import-slice-meta');
    els.fileImportFilterState = $('file-import-filter-state');
    els.fileImportFilterMeta = $('file-import-filter-meta');
    els.mainTranscribedOutput = $('main-transcribed-output');
    els.mainTranslatedOutput = $('main-translated-output');
    els.mainTranscribedLabel = $('main-transcribed-label');
    els.mainTranslatedLabel = $('main-translated-label');
    els.detachedTcState = $('detached-tc-state');
    els.detachedTcGeometry = $('detached-tc-geometry');
    els.detachedTlState = $('detached-tl-state');
    els.detachedTlGeometry = $('detached-tl-geometry');
    for (const key of DETACHED_WINDOW_PANEL_KEYS) {
      els[key] = $(key);
    }
    els.btnRecordingToggle = $('btn-recording-toggle');
    els.workspaceHub = $('workspace-hub');
    els.settingsShell = $('settings-shell');
    els.settingsToolbar = $('settings_toolbar');
    els.settingsSearch = $('settings_search');
    els.settingsSearchClear = $('settings_search_clear');
    els.settingsSearchMeta = $('settings_search_meta');
    els.decodePresetKpi = $('decode_preset_kpi');
    els.decodeTemperatureKpi = $('decode_temperature_kpi');
    els.decodeOutputKpi = $('decode_output_kpi');
    els.seleniumModeKpi = $('selenium_mode_kpi');
    els.seleniumZorderKpi = $('selenium_zorder_kpi');
    els.seleniumAutoCloseKpi = $('selenium_auto_close_kpi');
    els.appModalBackdrop = $('app_modal_backdrop');
    els.appModalCard = $('app_modal_card');
    els.appModalKicker = $('app_modal_kicker');
    els.appModalTitle = $('app_modal_title');
    els.appModalMessage = $('app_modal_message');
    els.appModalClose = $('app_modal_close');
    els.appModalCancel = $('app_modal_cancel');
    els.appModalConfirm = $('app_modal_confirm');
    els.taskCard = $('task-card');
    els.taskBadge = $('task-badge');
    els.taskTitle = $('task-title');
    els.taskMessage = $('task-message');
    els.taskRuntimeModelPill = $('task-runtime-model-pill');
    els.taskRuntimeExportPill = $('task-runtime-export-pill');
    els.taskRuntimeLogPill = $('task-runtime-log-pill');
    els.taskProgressText = $('task-progress-text');
    els.taskProgressFill = $('task-progress-fill');
    els.aboutCard = $('about-card');

    // Hallucination filters
    els.filterRec = $('filter_rec');
    els.filterRecCaseSensitive = $('filter_rec_case_sensitive');
    els.filterRecStrip = $('filter_rec_strip');
    els.filterRecExactMatch = $('filter_rec_exact_match');
    els.filterRecIgnorePunctuations = $('filter_rec_ignore_punctuations');
    els.filterRecSimilarity = $('filter_rec_similarity');
    els.filterFileImport = $('filter_file_import');
    els.filterFileImportCaseSensitive = $('filter_file_import_case_sensitive');
    els.filterFileImportStrip = $('filter_file_import_strip');
    els.filterFileImportExactMatch = $('filter_file_import_exact_match');
    els.filterFileImportIgnorePunctuations = $('filter_file_import_ignore_punctuations');
    els.filterFileImportSimilarity = $('filter_file_import_similarity');

    // Per-language initial prompts UI
    els.enableInitialPrompts = $('enable_initial_prompt');
    els.conditionOnPreviousText = $('condition_on_previous_text');
    els.initialPromptsContainer = $('initial_prompts_container');
    els.btnSaveInitialPrompts = document.querySelector('button[data-action="save-initial-prompts"]');

    // Live value displays for range sliders
    if (els.micThresholdDb) {
      els.micThresholdDb.addEventListener('input', (e) => {
        if (els.micThresholdDbValue) els.micThresholdDbValue.textContent = `${Number(e.target.value).toFixed(1)} dB`;
      });
    }
    if (els.speakerThresholdDb) {
      els.speakerThresholdDb.addEventListener('input', (e) => {
        if (els.speakerThresholdDbValue) els.speakerThresholdDbValue.textContent = `${Number(e.target.value).toFixed(1)} dB`;
      });
    }
    if (els.autoRefreshLog) {
      els.autoRefreshLog.addEventListener('change', () => {
        syncLogAutoRefresh();
      });
    }
    els.globalStatusbar = $('global-statusbar');
    els.pageScrollIndicator = $('page-scroll-indicator');
    els.pageScrollThumb = $('page-scroll-thumb');
    els.dashboardContent = document.querySelector('.dashboard-content');

    // bind per-language prompts buttons
    try {
      if (els.settingsSearch) {
        els.settingsSearch.addEventListener('input', () => {
          applySettingsFilter(els.settingsSearch.value);
        });
      }

      if (els.settingsSearchClear) {
        els.settingsSearchClear.addEventListener('click', () => {
          if (els.settingsSearch) {
            els.settingsSearch.value = '';
          }
          applySettingsFilter('');
        });
      }

      const saveBtns = Array.from(document.querySelectorAll('button[data-action="save-initial-prompts"]'));
      saveBtns.forEach((btn) => {
        btn.addEventListener('click', async () => {
          try {
            await saveInitialPromptsSettings(true);
          } catch (e) {
            console.error(e);
            await showAlertDialog({
              title: '保存引导词失败',
              message: String(e && e.message ? e.message : e),
              tone: 'danger',
            });
          }
        });
      });

      const resetBtn = document.getElementById('reset_initial_prompts');
      if (resetBtn) {
        resetBtn.addEventListener('click', async () => {
          if (!els.initialPromptsContainer) return;
          const inputs = Array.from(els.initialPromptsContainer.querySelectorAll('[data-prompt-text="true"]'));
          inputs.forEach((el) => {
            el.value = '';
          });
          try {
            await saveInitialPromptsSettings(true);
          } catch (e) {
            console.error(e);
            await showAlertDialog({
              title: '重置引导词失败',
              message: String(e && e.message ? e.message : e),
              tone: 'danger',
            });
          }
        });
      }

      const clearBtn = document.getElementById('clear_initial_prompts');
      if (clearBtn) {
        clearBtn.addEventListener('click', async () => {
          if (!els.initialPromptsContainer) return;
          buildInitialPromptsUi(els.initialPromptsContainer, {});
          try {
            await saveInitialPromptsSettings(true);
          } catch (e) {
            console.error(e);
            await showAlertDialog({
              title: '清空引导词失败',
              message: String(e && e.message ? e.message : e),
              tone: 'danger',
            });
          }
        });
      }

      const addCustomBtn = document.getElementById('add_custom_initial_prompt');
      if (addCustomBtn) {
        addCustomBtn.addEventListener('click', () => {
          const row = addCustomInitialPromptRow('', '');
          const codeInput = row ? row.querySelector('[data-lang-code="true"]') : null;
          if (codeInput) {
            codeInput.focus();
          }
        });
      }
    } catch (e) {
      console.debug('Initial prompts bindings skipped', e);
    }

    bindEvents();
    bindAutoSaveEvents();
    bindUiEvents();
    bindPageScrollIndicator();
    switchSidebarMenu('realtime');
    applySettingsFilter('');

    const bridgeReady = await waitForBridge();
    if (!bridgeReady) {
      throw new Error('连接 Python 桥接失败（pywebview API 未就绪）');
    }
    await startupMark('bridge_ready');
    await startupMark('before_first_paint');
    await nextUiTurn();
    await nextUiTurn();
    await startupMark('before_show_main_window');
    try {
      await apiCall('show_main_window');
    } catch (error) {
      console.debug('Show main window skipped', error);
    }
    await startupMark('after_show_main_window');

    await startupMark('before_refresh_state');
    await refreshState();
    await startupMark('after_refresh_state');

    updatePageScrollIndicator();
    state.initialized = true;
    await nextUiTurn();
    await nextUiTurn();
    await startupMark('init_complete');
  })();

  try {
    await state.initInFlight;
  } finally {
    state.initInFlight = null;
  }
}

function initWithErrorRender() {
  init().catch((error) => {
    console.error(error);
    const node = $('model-status-card');
    if (node) {
      node.innerHTML = `<div class="state-row"><div class="state-key error">启动失败</div><div class="state-value">${escapeHtml(error.message || String(error))}</div></div>`;
    }
  });
}

window.addEventListener('pywebviewready', initWithErrorRender);
document.addEventListener('pywebviewready', initWithErrorRender);
document.addEventListener('DOMContentLoaded', initWithErrorRender);

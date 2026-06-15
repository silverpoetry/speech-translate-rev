const state = {
  data: null,
  taskTimer: null,
  uiRefreshTimer: null,
  initialized: false,
  bridgeReady: false,
  eventsBound: false,
  uiEventsBound: false,
  pageScrollBound: false,
  modelPollTimer: null,
  modelCheckedOnce: false,
  seleniumSaveInFlight: false,
  detachedModeSelected: 'tc',
  detachedOpen: { tc: false, tl: false },
  initInFlight: null,
  autoSaveBound: false,
  autoSaveTimers: {},
  fileImportQueue: [],
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

function buildInitialPromptsUi(container, map) {
  try {
    container.innerHTML = '';
    const next = map || {};
    for (const code of DEFAULT_PROMPT_LANGS) {
      const label = document.createElement('label');
      label.className = 'field-span-2';

      const span = document.createElement('span');
      span.textContent = `${PROMPT_LANG_NAMES[code] || code} (${code})`;

      const ta = document.createElement('textarea');
      ta.setAttribute('data-lang', code);
      ta.className = 'input card-input';
      ta.placeholder = '留空表示使用内置默认引导词';
      ta.value = String(next[code] || '');
      // 自动保存：内容变更即保存
      ta.addEventListener('change', async () => {
        try {
          await saveInitialPromptsSettings(false);
        } catch (err) {
          console.error('保存引导词内容失败', err);
        }
      });

      label.appendChild(span);
      label.appendChild(ta);
      container.appendChild(label);
    }

    // Also include any custom language keys present in user map
    for (const k of Object.keys(next)) {
      if (!DEFAULT_PROMPT_LANGS.includes(k)) {
        const label = document.createElement('label');
        label.className = 'field-span-2';
        const span = document.createElement('span');
        span.textContent = `${k}`;
        const ta = document.createElement('textarea');
        ta.setAttribute('data-lang', k);
        ta.className = 'input card-input';
        ta.placeholder = '自定义语言代码';
        ta.value = String(next[k] || '');
        ta.addEventListener('change', async () => {
          try {
            await saveInitialPromptsSettings(false);
          } catch (err) {
            console.error('保存引导词内容失败', err);
          }
        });
        label.appendChild(span);
        label.appendChild(ta);
        container.appendChild(label);
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

function bindUiEvents() {
  if (state.uiEventsBound) {
    return;
  }

  window.addEventListener('speechtranslate-ui-update', (event) => {
    const detail = event && event.detail ? event.detail : {};
    const sections = Array.isArray(detail.sections) ? detail.sections : ['task'];
    scheduleUiRefresh(sections);
    try {
      if (sections.includes('import')) {
        refreshFileProcessingState().catch(() => {});
      }
    } catch (e) {
      console.debug('import-refresh skipped', e);
    }
  });

  state.uiEventsBound = true;
}

function populateSelect(selectEl, options, selectedValue, keepMissingSelection = true) {
  const normalizedOptions = Array.isArray(options) ? options : [];
  const currentValue = selectedValue ?? '';
  selectEl.innerHTML = normalizedOptions
    .map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`)
    .join('');

  if (keepMissingSelection && currentValue && !normalizedOptions.includes(currentValue)) {
    const option = document.createElement('option');
    option.value = currentValue;
    option.textContent = currentValue;
    selectEl.appendChild(option);
  }

  selectEl.value = currentValue;
}

function renderSettings(data) {
  const settings = data.settings || {};
  if (els.dirExport) {
    els.dirExport.value = settings.dir_export ?? 'auto';
  }
  if (els.fileExportDirPill) {
    els.fileExportDirPill.textContent = `输出目录：${settings.dir_export ?? 'auto'}`;
  }
  const exportTo = settings.export_to || ['txt', 'srt', 'vtt', 'json', 'ass'];
  if (els.exportTxt) els.exportTxt.checked = exportTo.includes('txt');
  if (els.exportSrt) els.exportSrt.checked = exportTo.includes('srt');
  if (els.exportVtt) els.exportVtt.checked = exportTo.includes('vtt');
  if (els.exportAss) els.exportAss.checked = exportTo.includes('ass');
  if (els.exportJson) els.exportJson.checked = exportTo.includes('json');
  if (els.exportCsv) els.exportCsv.checked = exportTo.includes('csv');
  if (els.exportMp4) els.exportMp4.checked = exportTo.includes('mp4');
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
}

function renderMainControls(data) {
  const mainUi = data.main_ui || {};
  const recordUi = data.record_ui || {};
  populateSelect(els.inputMode, mainUi.input_options || [], mainUi.selected_input || '');
  populateSelect(els.hostAPI, recordUi.host_api_options || [], recordUi.selected_host_api || recordUi.host_api || '');
  populateSelect(els.mic, recordUi.mic_options || [], recordUi.selected_mic || recordUi.mic || '');
  populateSelect(els.speaker, recordUi.speaker_options || [], recordUi.selected_speaker || recordUi.speaker || '');
  populateSelect(els.sourceLangMain, mainUi.source_options || [], mainUi.selected_source || '');
  populateSelect(els.targetLangMain, mainUi.target_options || [], mainUi.selected_target || '');
  populateSelect(els.translateEngineMain, mainUi.engine_options || [], mainUi.selected_engine || '');

  if (els.transcribeMain) els.transcribeMain.checked = Boolean(mainUi.transcribe ?? true);
  if (els.translateMain) els.translateMain.checked = Boolean(mainUi.translate ?? true);
  els.mainInputPill.textContent = `输入：${mainUi.selected_input || '未知'}`;
  els.mainLangPill.textContent = `语言：${mainUi.selected_source || '未知'} → ${mainUi.selected_target || '未知'}`;
  els.mainEnginePill.textContent = `引擎：${mainUi.selected_engine || '未知'}`;
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
    await apiCall('set_record_setting', 'hostAPI', els.hostAPI ? els.hostAPI.value : nextHostApi);
    await apiCall('set_record_setting', 'mic', els.mic ? els.mic.value : nextMic);
    await apiCall('set_record_setting', 'speaker', els.speaker ? els.speaker.value : nextSpeaker);
  }
}

function renderImportSettings(data) {
  const importUi = data.import_ui || {};
  setSelectedImportModelEngine(importUi.selected_backend || 'whisper');
  populateSelect(els.modelImport, importUi.model_options || [], importUi.selected_model || '', false);
  populateSelect(els.engineImport, importUi.engine_options || [], importUi.selected_engine || '');
  populateSelect(els.sourceImport, importUi.source_options || [], importUi.selected_source || '');
  populateSelect(els.targetImport, importUi.target_options || [], importUi.selected_target || '');

  els.transcribeImport.checked = Boolean(importUi.transcribe);
  els.translateImport.checked = Boolean(importUi.translate);
  els.importModelPill.textContent = `模型：${importUi.selected_model_key || importUi.selected_model || '未下载'}`;
  els.importEnginePill.textContent = `引擎：${importUi.selected_engine || '未知'}`;
  els.importLangPill.textContent = `语言：${importUi.selected_source || '自动'} → ${importUi.selected_target || '自动'}`;

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
    els.fileImportList.innerHTML = '<li class="file-queue-empty" style="color:var(--muted); padding:8px;">队列为空</li>';
    return;
  }

  // 辅助函数：根据状态文字生成对应的 HTML（包含进度条动画）
  const renderStatusCell = (statusText) => {
    const s = String(statusText || '').trim();
    if (!s) return '<span class="status-badge muted">无</span>';

    const sLower = s.toLowerCase();

    // 如果包含失败、错误字眼
    if (sLower.includes('fail') || sLower.includes('error') || sLower.includes('parse')) {
      return `<span class="status-badge error" style="color:var(--danger)">${escapeHtml(s)}</span>`;
    }

    // 如果已经完成
    if (sLower.includes('transcribed') || sLower.includes('translated') || sLower.includes('refined') || sLower.includes('aligned')) {
      return `<span class="status-badge success" style="color:var(--success)">${escapeHtml(s)}</span>`;
    }

    // 如果正在处理中 (显示微型进度条/动画效果)
    if (sLower.includes('please wait') || sLower.includes('processing') || sLower.includes('re-transcribing')) {
      return `
        <div style="display:flex; align-items:center; gap:6px;">
          <div class="mini-spinner" style="width:12px; height:12px; border:2px solid var(--accent); border-top-color:transparent; border-radius:50%; animation: spin 1s linear infinite;"></div>
          <span class="status-badge active" style="color:var(--accent); font-size:0.9em;">处理中...</span>
        </div>
      `;
    }

    // 默认情况 (Waiting)
    return `<span class="status-badge muted" style="color:var(--muted)">${escapeHtml(s)}</span>`;
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
      <li class="file-queue-item" data-index="${idx}" style="display:flex; padding:6px 8px; border-bottom:1px solid var(--surface-2); align-items:center;">
        <span class="file-queue-name" style="flex:2; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; padding-right:8px;" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
        <span class="file-queue-status-tc" style="flex:1; overflow:hidden;">${renderStatusCell(tcStatus)}</span>
        <span class="file-queue-status-tl" style="flex:1; overflow:hidden;">${renderStatusCell(tlStatus)}</span>
        <div class="file-queue-actions" style="width:32px; text-align:right;">
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
  } catch (err) {
    console.debug('Failed to refresh file processing state', err);
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

function renderLiveOutputs(data) {
  const live = data.live_ui || {};
  const setOutput = (el, htmlValue, textFallback) => {
    if (!el) {
      return;
    }
    const html = htmlValue || '';
    const plain = textFallback || '';
    if (html.trim()) {
      el.innerHTML = html;
      return;
    }
    el.textContent = plain || textFallback || '等待更新...';
  };

  setOutput(els.mainTranscribedOutput, live.main_transcribed_html, live.main_transcribed_text || '转写内容将显示在这里。');
  setOutput(els.mainTranslatedOutput, live.main_translated_html, live.main_translated_text || '翻译内容将显示在这里。');
  
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
  if (els.showAudioVisualizerInSetting) {
    els.showAudioVisualizerInSetting.checked = Boolean(recordUi.show_audio_visualizer_in_setting);
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
  if (els.recordVisualPill) {
    els.recordVisualPill.textContent = `可视化：${recordUi.show_audio_visualizer_in_setting ? '开' : '关'}`;
  }
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
    ['激活', active],
    ['标题', task.title],
    ['消息', task.message],
    ['进度', `${progress.toFixed(2)}%`],
    ['完成', task.finished],
    ['错误', task.error || ''],
  ];

  const infoHtml = summaryRows
    .map(([label, value]) => `
      <div class="state-row">
        <div class="state-key">${escapeHtml(label)}</div>
        <div class="state-value">${previewValue(value)}</div>
      </div>
    `)
    .join('');

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
  const modelKey = runtime.key || data?.import_ui?.selected_model_key || data?.import_ui?.selected_model || '未知';
  const loading = Boolean(runtime.loading);
  const loaded = Boolean(runtime.loaded);
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
        `计时 ${recordingState?.timer || '--:--:--'}`,
        `缓冲 ${recordingState?.buffer || '0/0 sec'}`,
        `句子 ${recordingState?.sentences || '0'}`,
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
}

function syncRecordingButton(recordingState) {
  if (!els.btnRecordingToggle) {
    return;
  }

  const active = Boolean(recordingState?.active);

  els.btnRecordingToggle.textContent = active ? '停止录制' : '开始录制';
  els.btnRecordingToggle.dataset.action = active ? 'stop-recording' : 'start-recording';
  els.btnRecordingToggle.classList.toggle('is-stop', active);
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
  const selectedEngine = modelUi.selected_engine || 'whisper';
  const rows = Array.isArray(modelUi.rows) ? modelUi.rows : [];
  setSelectedModelManagerEngine(selectedEngine);

  if (els.modelManagerDirPill) {
    els.modelManagerDirPill.textContent = `模型目录：${modelUi.model_dir || 'auto'}`;
  }
  if (els.modelManagerEnginePill) {
    els.modelManagerEnginePill.textContent = `引擎：${selectedEngine}`;
  }
  if (els.modelManagerDownloadPill) {
    els.modelManagerDownloadPill.textContent = `下载：${modelUi.download_running ? '进行中' : '空闲'}`;
  }
  if (els.modelManagerHint) {
    els.modelManagerHint.textContent = `说明：当前展示 ${selectedEngine} 的全部模型。缺失项可点击下载按钮，下载进度会同步到卡片和底部状态栏。`;
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
              <div class="model-download-progress-fill" style="width: ${rowProgress.toFixed(1)}%"></div>
            </div>
            <div class="model-download-meta">${rowProgress.toFixed(0)}%${rowSpeed ? ` | ${escapeHtml(rowSpeed)}` : ''}</div>
          `
          : '';

        return `
          <div class="model-status-item">
            <div class="model-status-head">
              <span class="model-status-name">${escapeHtml(rowModel)}</span>
              <span class="pill pill-muted">${escapeHtml(row.engine || '-')}</span>
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
      : '<div class="state-row"><div class="state-key">状态</div><div class="state-value">暂无模型状态</div></div>';
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
  renderLiveOutputs(data);
  updatePageScrollIndicator();

  const runHeavyRefresh = async () => {
    await refreshTaskState();
    await refreshImportUiDetails();
    if (!state.modelCheckedOnce) {
      await checkAllModelManagerState(getSelectedModelManagerEngine());
      state.modelCheckedOnce = true;
    } else {
      await refreshModelManagerState(getSelectedModelManagerEngine());
    }
    await loadDetachedConfig(getSelectedDetachedMode());
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
  try {
    const live = await apiCall('get_live_state');
    renderLiveOutputs({ live_ui: live });
  } catch (error) {
    console.debug('Live state refresh skipped', error);
  }
  updatePageScrollIndicator();
}

async function saveSettings(shouldRefresh = true) {
  const valueOf = (node, fallback = '') => (node && typeof node.value !== 'undefined' ? node.value : fallback);
  const checkedOf = (node, fallback = false) => (node && typeof node.checked !== 'undefined' ? Boolean(node.checked) : fallback);
  const currentSetting = (key, fallback = '') => {
    const settings = state.data && state.data.settings ? state.data.settings : null;
    const value = settings ? settings[key] : undefined;
    return value === undefined ? fallback : value;
  };

  const exportTo = [];
  if (els.exportTxt && checkedOf(els.exportTxt)) exportTo.push('txt');
  if (els.exportSrt && checkedOf(els.exportSrt)) exportTo.push('srt');
  if (els.exportVtt && checkedOf(els.exportVtt)) exportTo.push('vtt');
  if (els.exportAss && checkedOf(els.exportAss)) exportTo.push('ass');
  if (els.exportJson && checkedOf(els.exportJson)) exportTo.push('json');
  if (els.exportCsv && checkedOf(els.exportCsv)) exportTo.push('csv');
  if (els.exportMp4 && checkedOf(els.exportMp4)) exportTo.push('mp4');

  const updates = [
    ['dir_export', els.dirExport ? valueOf(els.dirExport, 'auto') : currentSetting('dir_export', 'auto')],
    ['export_to', exportTo],
    ['input', valueOf(els.inputMode, 'mic')],
    ['source_lang_mw', valueOf(els.sourceLangMain, 'English')],
    ['target_lang_mw', valueOf(els.targetLangMain, 'Indonesian')],
    ['tl_engine_mw', valueOf(els.translateEngineMain, 'Google Translate')],
    ['transcribe_mw', checkedOf(els.transcribeMain, true)],
    ['translate_mw', checkedOf(els.translateMain, true)],
    ['filter_rec', checkedOf(els.filterRec, true)],
    ['filter_rec_case_sensitive', checkedOf(els.filterRecCaseSensitive, false)],
    ['filter_rec_strip', checkedOf(els.filterRecStrip, true)],
    ['filter_rec_exact_match', checkedOf(els.filterRecExactMatch, false)],
    ['filter_rec_ignore_punctuations', valueOf(els.filterRecIgnorePunctuations, "\"',.?!")],
    ['filter_rec_similarity', Number(valueOf(els.filterRecSimilarity, 0.75))],
  ];

  for (const [key, value] of updates) {
    await apiCall('set_setting', key, value);
  }

  await apiCall('set_record_setting', 'hostAPI', valueOf(els.hostAPI, ''));
  await apiCall('set_record_setting', 'mic', valueOf(els.mic, ''));
  await apiCall('set_record_setting', 'speaker', valueOf(els.speaker, ''));

  if (shouldRefresh) {
    await refreshState();
  }
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
      const inputs = Array.from(els.initialPromptsContainer.querySelectorAll('[data-lang]'));
      for (const el of inputs) {
        const code = el.getAttribute('data-lang');
        el.onchange = async () => {
          try {
            await saveInitialPromptsSettings(false);
          } catch (err) {
            console.error('保存引导词内容失败', err);
          }
        };
        const val = (el.value || '').trim();
        if (val) {
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

  const exportTo = [];
  if (els.exportTxt && els.exportTxt.checked) exportTo.push('txt');
  if (els.exportSrt && els.exportSrt.checked) exportTo.push('srt');
  if (els.exportVtt && els.exportVtt.checked) exportTo.push('vtt');
  if (els.exportAss && els.exportAss.checked) exportTo.push('ass');
  if (els.exportJson && els.exportJson.checked) exportTo.push('json');
  if (els.exportCsv && els.exportCsv.checked) exportTo.push('csv');
  if (els.exportMp4 && els.exportMp4.checked) exportTo.push('mp4');

  const exportDir = els.dirExport
    ? els.dirExport.value
    : ((state.data && state.data.settings && state.data.settings.dir_export) || 'auto');
  await apiCall('set_setting', 'dir_export', exportDir);
  await apiCall('set_setting', 'export_to', exportTo);

  const updates = [
    ['model_f_import', els.modelImport.value],
    ['tl_engine_f_import', els.engineImport.value],
    ['source_lang_f_import', els.sourceImport.value],
    ['target_lang_f_import', els.targetImport.value],
    ['transcribe_f_import', els.transcribeImport.checked],
    ['translate_f_import', els.translateImport.checked],
    ['filter_file_import', els.filterFileImport ? els.filterFileImport.checked : true],
    ['filter_file_import_case_sensitive', els.filterFileImportCaseSensitive ? els.filterFileImportCaseSensitive.checked : false],
    ['filter_file_import_strip', els.filterFileImportStrip ? els.filterFileImportStrip.checked : true],
    ['filter_file_import_exact_match', els.filterFileImportExactMatch ? els.filterFileImportExactMatch.checked : false],
    ['filter_file_import_ignore_punctuations', els.filterFileImportIgnorePunctuations ? els.filterFileImportIgnorePunctuations.value : "\"',.?!"],
    ['filter_file_import_similarity', els.filterFileImportSimilarity ? Number(els.filterFileImportSimilarity.value) : 0.75],
  ];

  for (const [key, value] of updates) {
    await apiCall('set_import_setting', key, value);
  }

  if (shouldRefresh) {
    await refreshState();
  }
}

async function loadRuntimeModel() {
  const modelKey = els.modelImport ? els.modelImport.value : '';
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

async function saveRecordSettings(shouldRefresh = true) {
  const numberOr = (value, fallback) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const checked = (node) => Boolean(node && node.checked);

  const updates = [
    ['hostAPI', els.hostAPI.value],
    ['mic', els.mic.value],
    ['speaker', els.speaker.value],
    ['verbose_record', els.verboseRecord.value === 'true'],
    ['model_device_preference', String(els.modelDevicePreference ? els.modelDevicePreference.value : 'auto').toLowerCase()],
    ['transcribe_rate', numberOr(els.transcribeRate.value, 300)],
    ['separate_with', els.separateWith.value],
    ['use_temp', checked(els.useTempAlt)],
    ['keep_temp', checked(els.keepTemp)],
    ['file_use_official_whisper', checked(els.fileUseOfficialWhisper)],
    ['show_audio_visualizer_in_setting', checked(els.showAudioVisualizerInSetting)],
    ['sample_rate_mic', numberOr(els.micSampleRate.value, 16000)],
    ['chunk_size_mic', numberOr(els.micChunkSize.value, 1024)],
    ['channels_mic', els.micChannels.value],
    ['auto_sample_rate_mic', checked(els.micAutoSampleRate)],
    ['auto_channels_mic', checked(els.micAutoChannels)],
    ['min_input_length_mic', numberOr(els.micMinInputLength.value, 0.4)],
    ['max_buffer_mic', numberOr(els.micMaxBuffer.value, 10)],
    ['max_sentences_mic', numberOr(els.micMaxSentences.value, 5)],
    ['mic_no_limit', checked(els.micNoLimit)],
    ['threshold_enable_mic', checked(els.micThresholdEnable)],
    ['threshold_auto_mic', checked(els.micThresholdAuto)],
    ['auto_break_buffer_mic', checked(els.micAutoBreakBuffer)],
    ['threshold_auto_level_mic', numberOr(els.micThresholdAutoLevel.value, 3)],
    ['threshold_auto_silero_mic', checked(els.micThresholdAutoSilero)],
    ['threshold_silero_mic_min', numberOr(els.micThresholdSileroMin.value, 0.7)],
    ['threshold_db_mic', numberOr(els.micThresholdDb.value, -30.0)],
    ['sample_rate_speaker', numberOr(els.speakerSampleRate.value, 44100)],
    ['chunk_size_speaker', numberOr(els.speakerChunkSize.value, 1024)],
    ['channels_speaker', els.speakerChannels.value],
    ['auto_sample_rate_speaker', checked(els.speakerAutoSampleRate)],
    ['auto_channels_speaker', checked(els.speakerAutoChannels)],
    ['min_input_length_speaker', numberOr(els.speakerMinInputLength.value, 0.4)],
    ['max_buffer_speaker', numberOr(els.speakerMaxBuffer.value, 10)],
    ['max_sentences_speaker', numberOr(els.speakerMaxSentences.value, 5)],
    ['speaker_no_limit', checked(els.speakerNoLimit)],
    ['threshold_enable_speaker', checked(els.speakerThresholdEnable)],
    ['threshold_auto_speaker', checked(els.speakerThresholdAuto)],
    ['auto_break_buffer_speaker', checked(els.speakerAutoBreakBuffer)],
    ['threshold_auto_level_speaker', numberOr(els.speakerThresholdAutoLevel.value, 3)],
    ['threshold_auto_silero_speaker', checked(els.speakerThresholdAutoSilero)],
    ['threshold_silero_speaker_min', numberOr(els.speakerThresholdSileroMin.value, 0.7)],
    ['threshold_db_speaker', numberOr(els.speakerThresholdDb.value, -30.0)],
  ];

  for (const [key, value] of updates) {
    await apiCall('set_record_setting', key, value);
  }

  if (shouldRefresh) {
    await refreshState();
  }
}

function normalizeDetachedMode(mode) {
  return mode === 'tl' ? 'tl' : 'tc';
}

function getSelectedDetachedMode() {
  return normalizeDetachedMode(state.detachedModeSelected);
}

async function setDetachedMode(mode, shouldLoad = true) {
  const normalizedMode = normalizeDetachedMode(mode);
  state.detachedModeSelected = normalizedMode;

  if (els.detachedModeTcBtn) {
    els.detachedModeTcBtn.classList.toggle('is-active', normalizedMode === 'tc');
  }
  if (els.detachedModeTlBtn) {
    els.detachedModeTlBtn.classList.toggle('is-active', normalizedMode === 'tl');
  }

  if (shouldLoad) {
    await loadDetachedConfig(normalizedMode);
  }
}

async function loadDetachedConfig(mode) {
  const normalizedMode = mode === 'tc' ? 'tc' : 'tl';
  const config = await apiCall('get_detached_config', normalizedMode);
  if (config) {
    if (els.detachedFont) els.detachedFont.value = config.font || 'Arial';
    if (els.detachedFontSize) els.detachedFontSize.value = config.font_size || 13;
    if (els.detachedFontColor) els.detachedFontColor.value = config.font_color || '#FFFFFF';
    if (els.detachedBgColor) els.detachedBgColor.value = config.bg_color || '#000000';
    if (els.detachedOpacity) els.detachedOpacity.value = config.opacity || 1.0;
    if (els.detachedAlwaysOnTop) els.detachedAlwaysOnTop.checked = Boolean(config.always_on_top);
    if (els.detachedNoTitleBar) els.detachedNoTitleBar.checked = Boolean(config.no_title_bar);
    if (els.detachedClickThrough) els.detachedClickThrough.checked = Boolean(config.click_through);
  }
}

async function saveDetachedSettings(shouldRefresh = true) {
  const mode = getSelectedDetachedMode();
  const updates = [
    ['font', els.detachedFont ? els.detachedFont.value : 'Arial'],
    ['font_size', Number(els.detachedFontSize ? els.detachedFontSize.value : 13)],
    ['font_color', els.detachedFontColor ? els.detachedFontColor.value : '#FFFFFF'],
    ['bg_color', els.detachedBgColor ? els.detachedBgColor.value : '#000000'],
    ['always_on_top', Boolean(els.detachedAlwaysOnTop && els.detachedAlwaysOnTop.checked)],
    ['no_title_bar', Boolean(els.detachedNoTitleBar && els.detachedNoTitleBar.checked)],
    ['opacity', Number(els.detachedOpacity ? els.detachedOpacity.value : 1.0)],
    ['click_through', Boolean(els.detachedClickThrough && els.detachedClickThrough.checked)],
  ];

  for (const [key, value] of updates) {
    await apiCall('set_detached_config', mode, key, value);
  }

  // Refresh state to get updated config
  if (shouldRefresh) {
    await refreshState();
  }
  
  // Apply updated config to the detached window if it's open
  if (typeof pywebview !== 'undefined' && pywebview.api) {
    try {
      await pywebview.api.update_detached_config(mode);
      console.log(`Applied settings to ${mode} detached window`);
    } catch (error) {
      console.log(`Detached window ${mode} not open yet or config application failed:`, error);
    }
  }
}

const AUTO_SAVE_BUCKETS = {
  settings: new Set([
    'input_mode', 'source_lang_mw', 'target_lang_mw', 'tl_engine_mw',
    'transcribe_mw', 'translate_mw',
    'filter_rec', 'filter_rec_case_sensitive', 'filter_rec_strip', 'filter_rec_exact_match', 'filter_rec_ignore_punctuations', 'filter_rec_similarity'
  ]),
  import: new Set([
    'model_f_import', 'tl_engine_f_import', 'source_lang_f_import', 'target_lang_f_import',
    'transcribe_f_import', 'translate_f_import',
    'export_txt', 'export_srt', 'export_vtt', 'export_ass', 'export_json', 'export_csv', 'export_mp4',
    'filter_file_import', 'filter_file_import_case_sensitive', 'filter_file_import_strip', 'filter_file_import_exact_match', 'filter_file_import_ignore_punctuations', 'filter_file_import_similarity'
  ]),
  record: new Set([
    'verbose_record', 'use_temp', 'use_temp_alt', 'keep_temp', 'file_use_official_whisper',
    'show_audio_visualizer_in_setting',
    'auto_sample_rate_mic', 'auto_channels_mic', 'mic_no_limit', 'threshold_enable_mic', 'threshold_auto_mic',
    'threshold_auto_silero_mic', 'auto_break_buffer_mic', 'threshold_db_mic',
    'auto_sample_rate_speaker', 'auto_channels_speaker', 'speaker_no_limit', 'threshold_enable_speaker',
    'threshold_auto_speaker', 'threshold_auto_silero_speaker', 'auto_break_buffer_speaker', 'threshold_db_speaker'
  ]),
  detached: new Set([
    'detached_opacity', 'detached_always_on_top', 'detached_no_title_bar', 'detached_click_through'
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
    const isAutoSaveControl = tag === 'select' || (tag === 'input' && ['checkbox', 'radio', 'range'].includes(type));
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
    } else if (bucket === 'record') {
      scheduleAutoSave(bucket, () => saveRecordSettings(false));
    } else if (bucket === 'detached') {
      scheduleAutoSave(bucket, () => saveDetachedSettings(false));
    } else if (bucket === 'selenium') {
      scheduleAutoSave(bucket, () => saveSeleniumSettings(false));
    }
  });

  state.autoSaveBound = true;
}

async function createDetachedWindow(modeOverride = null) {
  const mode = normalizeDetachedMode(modeOverride || getSelectedDetachedMode());
  try {
    await setDetachedMode(mode, false);
    const modeLabel = mode === 'tc' ? '转写' : '翻译';
    const result = await apiCall('toggle_detached_window', mode);
    if (result && result.status === 'closed') {
      state.detachedOpen[mode] = false;
      console.log(`已关闭${modeLabel}独立窗口`);
      return;
    }

    state.detachedOpen[mode] = true;
    await apiCall('update_detached_config', mode);
    console.log(`Created ${modeLabel} detached window:`, result);
    console.log(`已打开${modeLabel}独立窗口`);
  } catch (error) {
    console.error('创建独立窗口失败:', error);
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
      console.error('启动录制失败:', result.message);
    }
  } catch (error) {
    console.error('启动录制出错:', error);
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
  stopTaskRefresh();
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

function switchSidebarMenu(target) {
  const menuButtons = Array.from(document.querySelectorAll('.menu-item[data-nav-target]'));
  for (const button of menuButtons) {
    button.classList.toggle('is-active', button.dataset.navTarget === target);
  }

  const showSettings = target === 'settings';
  if (els.workspaceHub) {
    els.workspaceHub.style.display = showSettings ? 'none' : 'grid';
  }
  if (els.settingsShell) {
    els.settingsShell.style.display = showSettings ? 'block' : 'none';
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

    const action = button.dataset.action;
    const openDir = button.dataset.openDir;

    try {
      if (openDir) {
        await openDirectory(openDir);
      } else if (action === 'refresh') {
        await refreshState();
      } else if (action === 'pick-export-dir') {
        await pickDirectory('export');
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
      } else if (action === 'open-repo') {
        await apiCall('open_link', 'https://github.com/Dadangdut33/Speech-Translate');
      } else if (action === 'open-filter-rec') {
        await apiCall('open_hallucination_filter', 'rec');
      } else if (action === 'open-filter-file') {
        await apiCall('open_hallucination_filter', 'file');
      } else if (action === 'save-selenium-settings') {
        await saveSeleniumSettings();
      } else if (action === 'save-settings') {
        await saveSettings();
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
      } else if (action === 'save-detached-settings') {
        await saveDetachedSettings();
      } else if (action === 'save-import-settings') {
        await saveImportSettings();
      } else if (action === 'load-model') {
        await loadRuntimeModel();
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

  if (els.detachedModeTcBtn) {
    els.detachedModeTcBtn.addEventListener('click', async () => {
      await setDetachedMode('tc', true);
    });
  }

  if (els.detachedModeTlBtn) {
    els.detachedModeTlBtn.addEventListener('click', async () => {
      await setDetachedMode('tl', true);
    });
  }

  if (els.hostAPI) {
    els.hostAPI.addEventListener('change', async () => {
      await refreshAudioSourceOptions(els.hostAPI.value, true);
    });
  }

  if (els.mic) {
    els.mic.addEventListener('change', async () => {
      await apiCall('set_record_setting', 'mic', els.mic.value);
    });
  }

  if (els.speaker) {
    els.speaker.addEventListener('change', async () => {
      await apiCall('set_record_setting', 'speaker', els.speaker.value);
    });
  }

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

  if (els.modelImport) {
    els.modelImport.addEventListener('change', async () => {
      try {
        // 只保存选择；不要自动触发模型加载。
        await apiCall('set_import_setting', 'model_f_import', els.modelImport.value);
      } catch (error) {
        console.error('保存模型选择失败', error);
      }
    });
  }

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
    els.exportMp4 = $('export_mp4');
    els.inputMode = $('input_mode');
    els.sourceLangMain = $('source_lang_mw');
    els.targetLangMain = $('target_lang_mw');
    els.translateEngineMain = $('tl_engine_mw');
    els.transcribeMain = $('transcribe_mw');
    els.translateMain = $('translate_mw');
    els.mainInputPill = $('main-input-pill');
    els.mainLangPill = $('main-lang-pill');
    els.mainEnginePill = $('main-engine-pill');
    els.hostAPI = $('hostAPI');
    els.mic = $('mic');
    els.speaker = $('speaker');
    els.verboseRecord = $('verbose_record');
    els.modelDevicePreference = $('model_device_preference');
    els.transcribeRate = $('transcribe_rate');
    els.separateWith = $('separate_with');
    els.useTemp = $('use_temp');
    els.useTempAlt = $('use_temp_alt');
    els.keepTemp = $('keep_temp');
    els.fileUseOfficialWhisper = $('file_use_official_whisper');
    els.showAudioVisualizerInSetting = $('show_audio_visualizer_in_setting');
    els.recordInputPill = $('record-input-pill');
    els.recordModePill = $('record-mode-pill');
    els.recordVisualPill = $('record-visual-pill');
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
    els.importLangPill = $('import-lang-pill');
    els.modelManagerEngineBar = $('model-manager-engine-bar');
    els.modelManagerDirPill = $('model-manager-dir-pill');
    els.modelManagerEnginePill = $('model-manager-engine-pill');
    els.modelManagerDownloadPill = $('model-manager-download-pill');
    els.fileExportDirPill = $('file-export-dir-pill');
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
    els.mainTranscribedOutput = $('main-transcribed-output');
    els.mainTranslatedOutput = $('main-translated-output');
    els.detachedModeTitlebar = $('detached_mode_titlebar');
    els.detachedModeTcBtn = $('detached_mode_tc_btn');
    els.detachedModeTlBtn = $('detached_mode_tl_btn');
    els.detachedFont = $('detached_font');
    els.detachedFontSize = $('detached_font_size');
    els.detachedFontColor = $('detached_font_color');
    els.detachedBgColor = $('detached_bg_color');
    els.detachedOpacity = $('detached_opacity');
    els.detachedAlwaysOnTop = $('detached_always_on_top');
    els.detachedNoTitleBar = $('detached_no_title_bar');
    els.detachedClickThrough = $('detached_click_through');
    els.btnRecordingToggle = $('btn-recording-toggle');
    els.workspaceHub = $('workspace-hub');
    els.settingsShell = $('settings-shell');
    els.taskCard = $('task-card');

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
    els.globalStatusbar = $('global-statusbar');
    els.pageScrollIndicator = $('page-scroll-indicator');
    els.pageScrollThumb = $('page-scroll-thumb');
    els.dashboardContent = document.querySelector('.dashboard-content');

    // bind per-language prompts buttons
    try {
      const saveBtns = Array.from(document.querySelectorAll('button[data-action="save-initial-prompts"]'));
      saveBtns.forEach((btn) => {
        btn.addEventListener('click', async () => {
          try {
            await saveInitialPromptsSettings(true);
          } catch (e) {
            console.error(e);
            window.alert('保存按语言引导词失败：' + (e && e.message ? e.message : e));
          }
        });
      });

      const resetBtn = document.getElementById('reset_initial_prompts');
      if (resetBtn) {
        resetBtn.addEventListener('click', async () => {
          if (!els.initialPromptsContainer) return;
          const inputs = Array.from(els.initialPromptsContainer.querySelectorAll('[data-lang]'));
          inputs.forEach((el) => {
            el.value = '';
          });
          try {
            await saveInitialPromptsSettings(true);
          } catch (e) {
            console.error(e);
            window.alert('重置失败：' + (e && e.message ? e.message : e));
          }
        });
      }

      const clearBtn = document.getElementById('clear_initial_prompts');
      if (clearBtn) {
        clearBtn.addEventListener('click', async () => {
          if (!els.initialPromptsContainer) return;
          const inputs = Array.from(els.initialPromptsContainer.querySelectorAll('[data-lang]'));
          inputs.forEach((el) => {
            el.value = '';
          });
          try {
            await saveInitialPromptsSettings(true);
          } catch (e) {
            console.error(e);
            window.alert('清空失败：' + (e && e.message ? e.message : e));
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

    const bridgeReady = await waitForBridge();
    if (!bridgeReady) {
      throw new Error('连接 Python 桥接失败（pywebview API 未就绪）');
    }
    await startupMark('bridge_ready');

    await startupMark('before_refresh_state');
    await refreshState();
    await startupMark('after_refresh_state');

    await startupMark('before_show_main_window');
    try {
      await apiCall('show_main_window');
    } catch (error) {
      console.debug('Show main window skipped', error);
    }
    await startupMark('after_show_main_window');

    await startupMark('before_set_detached_mode');
    await setDetachedMode(state.detachedModeSelected, false);
    await startupMark('after_set_detached_mode');

    updatePageScrollIndicator();
    await startupMark('init_complete');
    state.initialized = true;
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

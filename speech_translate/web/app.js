const state = {
  data: null,
  autoRefresh: true,
  logTimer: null,
  taskTimer: null,
  uiRefreshTimer: null,
  initialized: false,
  bridgeReady: false,
  eventsBound: false,
  uiEventsBound: false,
  pageScrollBound: false,
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

async function apiCall(name, ...args) {
  if (!window.pywebview || !window.pywebview.api) {
    throw new Error('pywebview API 尚未就绪');
  }
  return window.pywebview.api[name](...args);
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
  });

  state.uiEventsBound = true;
}

function renderPills(data) {
  els.version.textContent = `${data.app_name} ${data.version}`;
  els.os.textContent = `${data.os_name} ${data.os_release}`;
  els.logfile.textContent = data.current_log;
}

function populateSelect(selectEl, options, selectedValue) {
  const normalizedOptions = Array.isArray(options) ? options : [];
  const currentValue = selectedValue ?? '';
  selectEl.innerHTML = normalizedOptions
    .map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`)
    .join('');

  if (currentValue && !normalizedOptions.includes(currentValue)) {
    const option = document.createElement('option');
    option.value = currentValue;
    option.textContent = currentValue;
    selectEl.appendChild(option);
  }

  selectEl.value = currentValue;
}

function renderSettings(data) {
  const settings = data.settings || {};
  els.theme.value = settings.theme ?? '';
  els.logLevel.value = settings.log_level ?? data.log_level ?? 'DEBUG';
  els.dirExport.value = settings.dir_export ?? 'auto';
  els.dirModel.value = settings.dir_model ?? 'auto';
  els.autoRefresh.textContent = `自动刷新：${settings.auto_refresh_log ? '开' : '关'}`;
  state.autoRefresh = Boolean(settings.auto_refresh_log);
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

  els.autoScrollLog.checked = Boolean(mainUi.auto_scroll_log);
  els.autoRefreshLog.checked = Boolean(mainUi.auto_refresh_log);
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
  populateSelect(els.modelImport, importUi.model_options || [], importUi.selected_model || '');
  populateSelect(els.engineImport, importUi.engine_options || [], importUi.selected_engine || '');
  populateSelect(els.sourceImport, importUi.source_options || [], importUi.selected_source || '');
  populateSelect(els.targetImport, importUi.target_options || [], importUi.selected_target || '');

  els.transcribeImport.checked = Boolean(importUi.transcribe);
  els.translateImport.checked = Boolean(importUi.translate);
  els.importModelPill.textContent = `模型：${importUi.selected_model_key || importUi.selected_model || '未知'}`;
  els.importEnginePill.textContent = `引擎：${importUi.selected_engine || '未知'}`;
  els.importLangPill.textContent = `语言：${importUi.selected_source || '自动'} → ${importUi.selected_target || '自动'}`;
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

function renderState(data) {
  const settings = data.settings || {};
  const keys = [
    ['系统', `${data.os_name} ${data.os_release} (${data.os_version})`],
    ['CPU', data.cpu],
    ['主题', settings.theme],
    ['日志级别', settings.log_level],
    ['导出目录', settings.dir_export],
    ['模型目录', settings.dir_model],
    ['源语言', settings.source_lang_mw],
    ['目标语言', settings.target_lang_mw],
    ['输入', settings.input],
    ['翻译引擎', settings.tl_engine_mw],
    ['自动滚动日志', settings.auto_scroll_log],
    ['自动刷新日志', settings.auto_refresh_log],
  ];

  els.stateCard.innerHTML = keys
    .map(([label, value]) => `
      <div class="state-row">
        <div class="state-key">${escapeHtml(label)}</div>
        <div class="state-value">${previewValue(value)}</div>
      </div>
    `)
    .join('');
}

function renderAbout(data) {
  const about = data.about || {};
  const lines = [
    ['应用', about.name],
    ['版本', about.version],
    ['系统', about.os],
    ['CPU', about.cpu],
    ['日志文件', about.log_file],
    ['模型目录', about.model_dir],
    ['导出目录', about.export_dir],
  ];

  if (els.aboutCard) {
    els.aboutCard.innerHTML = lines
      .map(([label, value]) => `
        <div class="state-row">
          <div class="state-key">${escapeHtml(label)}</div>
          <div class="state-value">${previewValue(value)}</div>
        </div>
      `)
      .join('');
  }
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
    
    if (tcHtml) {
      pywebview.api.update_detached_content('tc', tcHtml).catch(() => {
        // Window might not be created yet
      });
    }
    
    if (tlHtml) {
      pywebview.api.update_detached_content('tl', tlHtml).catch(() => {
        // Window might not be created yet
      });
    }
  }
}

function renderRecordSettings(data) {
  const recordUi = data.record_ui || {};
  const mic = recordUi.mic_device || {};
  const speaker = recordUi.speaker_device || {};

  if (els.verboseRecord) els.verboseRecord.value = String(Boolean(recordUi.verbose_record));
  if (els.transcribeRate) els.transcribeRate.value = recordUi.transcribe_rate ?? 300;
  if (els.separateWith) els.separateWith.value = recordUi.separate_with ?? '\n';
  if (els.useTemp) els.useTemp.checked = !Boolean(recordUi.use_temp);
  if (els.useTempAlt) els.useTempAlt.checked = Boolean(recordUi.use_temp);
  if (els.keepTemp) els.keepTemp.checked = Boolean(recordUi.keep_temp);
  if (els.showAudioVisualizerInSetting) {
    els.showAudioVisualizerInSetting.checked = Boolean(recordUi.show_audio_visualizer_in_setting);
  }

  const fillDevice = (prefix, device) => {
    const setValue = (suffix, value) => {
      const node = els[`${prefix}${suffix}`];
      if (node) {
        node.value = value ?? '';
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
        ? (runtimeMsg || '正在准备模型缓存')
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

function renderModelManagerState(data) {
  const modelUi = data || {};
  const engines = modelUi.engine_options || ['whisper', 'faster-whisper'];
  const models = modelUi.model_options || [];
  const selectedEngine = modelUi.selected_engine || 'whisper';
  const selectedModel = modelUi.selected_model || '';
  const rows = Array.isArray(modelUi.rows) ? modelUi.rows : [];

  if (els.modelManagerEngine) populateSelect(els.modelManagerEngine, engines, selectedEngine);
  if (els.modelManagerModel) populateSelect(els.modelManagerModel, models, selectedModel);

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
    const scope = modelUi.view_scope === 'both' ? '双引擎' : `当前引擎（${selectedEngine}）`;
    els.modelManagerHint.textContent = `提示：刷新会读取缓存状态，检查操作会对${scope}执行真实检查。`;
  }

  if (els.modelStatusCard) {
    const renderedRows = rows
      .map((row) => {
        const status = row.downloading
          ? '下载中'
          : row.downloaded === true
            ? '已下载'
            : row.downloaded === false
                ? '缺失'
                : '未知';
              const note = row.error ? `错误：${row.error}` : '';
        return `
          <div class="model-status-item">
            <div class="model-status-head">
              <span class="model-status-name">${escapeHtml(row.model || '-')}</span>
              <span class="pill pill-muted">${escapeHtml(row.engine || '-')}</span>
            </div>
            <div class="model-status-value">${escapeHtml(status)}</div>
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
}

function renderLog(content) {
  els.logOutput.textContent = content || '';
  const settings = state.data?.settings || {};
  if (settings.auto_scroll_log !== false) {
    els.logOutput.scrollTop = els.logOutput.scrollHeight;
  }
}

async function refreshState() {
  if (!state.bridgeReady) {
    const ready = await waitForBridge();
    if (!ready) {
      throw new Error('桥接初始化超时：pywebview API 不可用');
    }
  }

  const data = await apiCall('get_state');
  state.data = data;
  renderPills(data);
  renderSettings(data);
  renderMainControls(data);
  renderRecordSettings(data);
  renderImportSettings(data);
  renderLiveOutputs(data);
  renderState(data);
  renderAbout(data);
  renderLog(data.log_content);
  const task = await apiCall('get_task_state');
  const recordingState = await apiCall('get_recording_state');
  state.data.recording_state = recordingState;
  renderTaskState(task);
  renderGlobalStatusBar(task, data, recordingState);
  syncRecordingButton(recordingState);
  updatePageScrollIndicator();
  await refreshModelManagerState();
  await loadDetachedConfig('tc');
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

async function refreshLog() {
  const data = await apiCall('refresh_log');
  els.logOutput.textContent = data.content || '';
  const settings = state.data?.settings || {};
  if (settings.auto_scroll_log !== false) {
    els.logOutput.scrollTop = els.logOutput.scrollHeight;
  }
}

async function saveSettings(shouldRefresh = true) {
  const updates = [
    ['theme', els.theme.value],
    ['log_level', els.logLevel.value],
    ['dir_export', els.dirExport.value],
    ['dir_model', els.dirModel.value],
    ['input', els.inputMode.value],
    ['source_lang_mw', els.sourceLangMain.value],
    ['target_lang_mw', els.targetLangMain.value],
    ['tl_engine_mw', els.translateEngineMain.value],
    ['transcribe_mw', els.transcribeMain ? els.transcribeMain.checked : true],
    ['translate_mw', els.translateMain ? els.translateMain.checked : true],
    ['auto_scroll_log', els.autoScrollLog.checked],
    ['auto_refresh_log', els.autoRefreshLog.checked],
  ];

  for (const [key, value] of updates) {
    await apiCall('set_setting', key, value);
  }

  await apiCall('set_record_setting', 'hostAPI', els.hostAPI ? els.hostAPI.value : '');
  await apiCall('set_record_setting', 'mic', els.mic ? els.mic.value : '');
  await apiCall('set_record_setting', 'speaker', els.speaker ? els.speaker.value : '');

  if (shouldRefresh) {
    await refreshState();
    startAutoRefresh();
  }
}

async function saveImportSettings() {
  const updates = [
    ['model_f_import', els.modelImport.value],
    ['tl_engine_f_import', els.engineImport.value],
    ['source_lang_f_import', els.sourceImport.value],
    ['target_lang_f_import', els.targetImport.value],
    ['transcribe_f_import', els.transcribeImport.checked],
    ['translate_f_import', els.translateImport.checked],
  ];

  for (const [key, value] of updates) {
    await apiCall('set_import_setting', key, value);
  }

  await refreshState();
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
}

async function saveRecordSettings() {
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
    ['transcribe_rate', numberOr(els.transcribeRate.value, 300)],
    ['separate_with', els.separateWith.value],
    ['use_temp', checked(els.useTempAlt)],
    ['keep_temp', checked(els.keepTemp)],
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

  await refreshState();
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

async function saveDetachedSettings() {
  const mode = (els.detachedMode && els.detachedMode.value === 'tc') ? 'tc' : 'tl';
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
  await refreshState();
  
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

async function createDetachedWindow(modeOverride = null) {
  const mode = modeOverride || (els.detachedMode && els.detachedMode.value === 'tc' ? 'tc' : 'tl');
  try {
    const modeLabel = mode === 'tc' ? '转写' : '翻译';
    const result = await apiCall('create_detached_window', mode, 100, 100);
    await apiCall('update_detached_config', mode);
    console.log(`Created ${modeLabel} detached window:`, result);
    
    // Show success message
    if (typeof pywebview !== 'undefined' && pywebview.api) {
      await apiCall('notify', '语音翻译', `已打开${modeLabel}独立窗口`);
    }
  } catch (error) {
    console.error('创建独立窗口失败:', error);
    if (typeof pywebview !== 'undefined' && pywebview.api) {
      await apiCall('notify', '语音翻译', '打开窗口失败，请查看控制台');
    }
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
      await apiCall('notify', '语音翻译', '请先启用“转写”或“翻译”');
      return;
    }

    // Persist current main settings before starting recording, but do not block on a full refresh.
    await saveSettings(false);
    
    const result = await apiCall('start_recording', device, langSource, langTarget, engine, isTc, isTl);
    
    if (result.ok) {
      syncRecordingButton(await apiCall('get_recording_state'));
      
      console.log('录制已开始:', result);
      if (typeof pywebview !== 'undefined' && pywebview.api) {
        await apiCall('notify', '语音翻译', '录制已开始');
      }
    } else {
      console.error('启动录制失败:', result.message);
      if (typeof pywebview !== 'undefined' && pywebview.api) {
        await apiCall('notify', '语音翻译', `录制失败：${result.message}`);
      }
    }
  } catch (error) {
    console.error('启动录制出错:', error);
    if (typeof pywebview !== 'undefined' && pywebview.api) {
      await apiCall('notify', '语音翻译', `录制错误：${error.message || String(error)}`);
    }
  }
}

async function stopRecording() {
  try {
    const result = await apiCall('stop_recording');
    const latestState = await apiCall('get_recording_state');
    syncRecordingButton(latestState);

    if (result.ok) {
      console.log('录制已停止:', result);
      if (typeof pywebview !== 'undefined' && pywebview.api) {
        await apiCall('notify', '语音翻译', '录制已停止');
      }
    } else {
      console.error('停止录制失败:', result.message);
      if (typeof pywebview !== 'undefined' && pywebview.api) {
        await apiCall('notify', '语音翻译', `停止失败：${result.message}`);
      }
    }
  } catch (error) {
    console.error('停止录制出错:', error);
    if (typeof pywebview !== 'undefined' && pywebview.api) {
      await apiCall('notify', '语音翻译', `停止错误：${error.message || String(error)}`);
    }
  }
}


async function openDirectory(kind) {
  await apiCall('open_directory', kind);
}

async function clearLog() {
  const data = await apiCall('clear_log');
  els.logOutput.textContent = data.content || '';
}

async function sendTestNotification() {
  await apiCall('notify', '语音翻译', 'pywebview 桥接已连接');
}

function startAutoRefresh() {
  stopAutoRefresh();
  if (!state.autoRefresh) {
    return;
  }
  state.logTimer = window.setInterval(() => {
    refreshLog().catch((error) => console.error(error));
  }, 2000);
}

function startTaskRefresh() {
  stopTaskRefresh();
}

function stopAutoRefresh() {
  if (state.logTimer !== null) {
    window.clearInterval(state.logTimer);
    state.logTimer = null;
  }
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
      } else if (action === 'import-files') {
        await saveImportSettings();
        const importResult = await apiCall('import_files');
        if (!importResult || importResult.ok === false) {
          throw new Error((importResult && importResult.message) || '文件导入未启动');
        }
        await refreshState();
      } else if (action === 'clear-log') {
        await clearLog();
      } else if (action === 'open-repo') {
        await apiCall('open_link', 'https://github.com/Dadangdut33/Speech-Translate');
      } else if (action === 'save-settings') {
        await saveSettings();
      } else if (action === 'refresh-model-manager') {
        await refreshModelManagerState(els.modelManagerEngine ? els.modelManagerEngine.value : 'whisper');
      } else if (action === 'check-model') {
        const response = await apiCall(
          'check_model',
          els.modelManagerModel ? els.modelManagerModel.value : 'small',
          els.modelManagerEngine ? els.modelManagerEngine.value : 'whisper',
        );
        renderModelManagerState(response);
      } else if (action === 'check-model-all') {
        const response = await apiCall(
          'check_all_models',
          els.modelManagerEngine ? els.modelManagerEngine.value : 'whisper',
        );
        renderModelManagerState(response);
      } else if (action === 'check-model-all-both') {
        const response = await apiCall('check_all_models', 'both');
        renderModelManagerState(response);
      } else if (action === 'download-model') {
        const res = await apiCall(
          'download_model',
          els.modelManagerModel ? els.modelManagerModel.value : 'small',
          els.modelManagerEngine ? els.modelManagerEngine.value : 'whisper',
        );
        if (!res || res.ok === false) {
          throw new Error((res && res.message) || '模型下载启动失败');
        }
        await refreshModelManagerState(els.modelManagerEngine ? els.modelManagerEngine.value : 'whisper');
        await refreshTaskState();
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
      } else if (action === 'notify') {
        await sendTestNotification();
      } else if (action === 'refresh-log') {
        await refreshLog();
      } else if (action === 'auto-refresh-toggle') {
        state.autoRefresh = !state.autoRefresh;
        els.autoRefresh.textContent = `自动刷新：${state.autoRefresh ? '开' : '关'}`;
        if (state.data?.settings) {
          state.data.settings.auto_refresh_log = state.autoRefresh;
        }
        startAutoRefresh();
      } else if (action === 'open-github') {
        await apiCall('open_link', 'https://github.com/Dadangdut33/Speech-Translate');
      } else if (action === 'open-wiki') {
        await apiCall('open_link', 'https://github.com/Dadangdut33/Speech-Translate/wiki');
      } else if (action === 'quit') {
        await apiCall('quit_app');
      }
    } catch (error) {
      console.error(error);
      try {
        await refreshTaskState();
      } catch (_syncError) {
        // ignore follow-up sync errors
      }
      const node = $('task-card');
      if (node) {
        node.innerHTML = `<div class="state-row"><div class="state-key error">操作失败</div><div class="state-value">${escapeHtml(error.message || String(error))}</div></div>`;
      }
    }
  });

  if (els.detachedMode) {
    els.detachedMode.addEventListener('change', async () => {
      await loadDetachedConfig(els.detachedMode.value || 'tl');
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

  if (els.modelManagerEngine) {
    els.modelManagerEngine.addEventListener('change', async () => {
      await refreshModelManagerState(els.modelManagerEngine.value);
    });
  }

  if (els.modelImport) {
    els.modelImport.addEventListener('change', async () => {
      try {
        await loadRuntimeModel();
      } catch (error) {
        console.error(error);
      }
    });
  }

  state.eventsBound = true;
}

async function init() {
  if (state.initialized) {
    return;
  }

  els.version = $('pill-version');
  els.os = $('pill-os');
  els.logfile = $('pill-logfile');
  els.theme = $('theme');
  els.logLevel = $('log_level');
  els.dirExport = $('dir_export');
  els.dirModel = $('dir_model');
  els.inputMode = $('input_mode');
  els.sourceLangMain = $('source_lang_mw');
  els.targetLangMain = $('target_lang_mw');
  els.translateEngineMain = $('tl_engine_mw');
  els.autoScrollLog = $('auto_scroll_log');
  els.autoRefreshLog = $('auto_refresh_log');
  els.transcribeMain = $('transcribe_mw');
  els.translateMain = $('translate_mw');
  els.mainInputPill = $('main-input-pill');
  els.mainLangPill = $('main-lang-pill');
  els.mainEnginePill = $('main-engine-pill');
  els.hostAPI = $('hostAPI');
  els.mic = $('mic');
  els.speaker = $('speaker');
  els.verboseRecord = $('verbose_record');
  els.transcribeRate = $('transcribe_rate');
  els.separateWith = $('separate_with');
  els.useTemp = $('use_temp');
  els.useTempAlt = $('use_temp_alt');
  els.keepTemp = $('keep_temp');
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
  els.modelImport = $('model_f_import');
  els.engineImport = $('tl_engine_f_import');
  els.sourceImport = $('source_lang_f_import');
  els.targetImport = $('target_lang_f_import');
  els.transcribeImport = $('transcribe_f_import');
  els.translateImport = $('translate_f_import');
  els.importModelPill = $('import-model-pill');
  els.importEnginePill = $('import-engine-pill');
  els.importLangPill = $('import-lang-pill');
  els.modelManagerEngine = $('model_manager_engine');
  els.modelManagerModel = $('model_manager_model');
  els.modelManagerDirPill = $('model-manager-dir-pill');
  els.modelManagerEnginePill = $('model-manager-engine-pill');
  els.modelManagerDownloadPill = $('model-manager-download-pill');
  els.modelManagerHint = $('model-manager-hint');
  els.modelStatusCard = $('model-status-card');
  els.taskBadge = $('task-badge');
  els.taskTitle = $('task-title');
  els.taskMessage = $('task-message');
  els.taskProgressText = $('task-progress-text');
  els.taskProgressFill = $('task-progress-fill');
  els.globalModelState = $('global-model-state');
  els.globalModelMeta = $('global-model-meta');
  els.globalTaskState = $('global-task-state');
  els.globalTaskMessage = $('global-task-message');
  els.globalTaskProgressText = $('global-task-progress-text');
  els.globalTaskProgressFill = $('global-task-progress-fill');
  els.globalTaskProgressWrap = $('global-task-progress-wrap');
  els.logOutput = $('log-output');
  els.stateCard = $('state-card');
  els.aboutCard = $('about-card');
  els.mainTranscribedOutput = $('main-transcribed-output');
  els.mainTranslatedOutput = $('main-translated-output');
  els.detachedMode = $('detached_mode');
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
  els.autoRefresh = $('auto-refresh-toggle');
  els.globalStatusbar = $('global-statusbar');
  els.pageScrollIndicator = $('page-scroll-indicator');
  els.pageScrollThumb = $('page-scroll-thumb');
  els.dashboardContent = document.querySelector('.dashboard-content');

  bindEvents();
  bindUiEvents();
  bindPageScrollIndicator();
  switchSidebarMenu('realtime');
  const bridgeReady = await waitForBridge();
  if (!bridgeReady) {
    throw new Error('连接 Python 桥接失败（pywebview API 未就绪）');
  }

  await refreshState();
  updatePageScrollIndicator();
  startAutoRefresh();
  state.initialized = true;
}

function initWithErrorRender() {
  init().catch((error) => {
    console.error(error);
    const node = $('state-card');
    if (node) {
      node.innerHTML = `<div class="state-row"><div class="state-key error">启动失败</div><div class="state-value">${escapeHtml(error.message || String(error))}</div></div>`;
    }
  });
}

window.addEventListener('pywebviewready', initWithErrorRender);
document.addEventListener('pywebviewready', initWithErrorRender);
document.addEventListener('DOMContentLoaded', initWithErrorRender);

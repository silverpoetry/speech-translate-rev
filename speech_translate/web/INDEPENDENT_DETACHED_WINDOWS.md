# 独立 Detached 窗口实现方案 (pywebview 多窗口)

## 概述

用户的 detached subtitle 窗口现在实现为真正的 OS-level 独立窗口，而不是主窗口内嵌的 HTML divs。这意味着它们可以：

✅ **独立显示** - 即使主窗口最小化也能看到
✅ **独立管理** - 可以独立最小化、最大化、关闭
✅ **始终在前** - 通过 OS window manager 管理 z-order
✅ **现代 Web UI** - 使用纯 HTML/CSS/JavaScript 绘制界面
✅ **双进程通信** - 主窗口和 detached 窗口通过 postMessage 通信

## 架构

### 后端 (Python - webview_app.py)

#### 1. DetachedWindowManager 类
```python
class DetachedWindowManager:
    """管理 detached subtitle 窗口的生命周期"""
    
    - create_window(mode, x, y, width, height) - 创建新窗口
    - show_window(mode) - 显示窗口
    - hide_window(mode) - 隐藏窗口
    - close_window(mode) - 关闭窗口
    - update_window_content(mode, html) - 更新窗口内容
    - update_window_config(mode, config) - 更新窗口配置
    - close_all() - 关闭所有窗口
```

#### 2. WebBridge 新方法
```python
- create_detached_window(mode, x, y) - 前端调用创建窗口
- show_detached_window(mode) - 显示窗口
- hide_detached_window(mode) - 隐藏窗口
- close_detached_window(mode) - 关闭窗口
- update_detached_content(mode, html) - 更新内容
- update_detached_config(mode, config) - 更新配置
```

#### 3. 生命周期管理
- **启动时**: 根据用户设置可选自动创建 detached 窗口
- **运行中**: 通过 `update_detached_content()` 发送实时内容
- **关闭时**: `quit_app()` 自动关闭所有 detached 窗口

### 前端 (Web - app.js)

#### 1. 窗口创建
```javascript
async function createDetachedWindow() {
  const mode = 'tc' | 'tl'; // 选择 Transcribed 或 Translated
  const result = await apiCall('create_detached_window', mode, 100, 100);
  console.log('Window created:', result);
}
```

#### 2. 内容更新
```javascript
// 在 renderLiveOutputs() 中
if (tcHtml) {
  pywebview.api.update_detached_content('tc', tcHtml);
}
```

#### 3. 双窗口通信
**主窗口 → Detached 窗口:**
```javascript
window.evaluate_js(`window.postMessage({
  type: 'update-content',
  html: '...'
}, '*')`)
```

**Detached 窗口接收:**
```javascript
window.addEventListener('message', (e) => {
  if (e.data.type === 'update-content') {
    document.getElementById('window-content').innerHTML = e.data.html;
  }
});
```

## 文件列表

### 新增文件
- `speech_translate/web/detached_window.html` - Detached 窗口的 HTML 模板

### 修改文件
- `speech_translate/webview_app.py` - 添加 DetachedWindowManager 和 WebBridge 方法
- `speech_translate/web/app.js` - 添加 createDetachedWindow() 和内容更新逻辑
- `speech_translate/web/index.html` - 添加"Open Detached Window"按钮

### 保留文件（向后兼容）
- 主窗口内嵌的 detached-tc-window 和 detached-tl-window divs 仍存在
- 用于备用或用户偏好内嵌式的场景

## 工作流程

### 用户流程

1. **打开 Detached 窗口**
   - 在 "Detached Window Settings" 部分选择 Transcribed 或 Translated
   - 点击 "Open Detached Window" 按钮
   - 一个独立的浮动窗口出现在屏幕上

2. **配置窗口**
   - 在设置面板中调整字体、颜色、透明度等
   - 点击 "Save Detached Settings"
   - 设置应用到已打开的窗口

3. **内容更新**
   - 当开始录音/转录时，内容自动发送到 detached 窗口
   - 实时显示转录和翻译结果
   - 可独立最小化窗口以节省空间

4. **窗口管理**
   - 可独立最小化、最大化、移动 detached 窗口
   - 点击窗口的关闭按钮关闭它
   - 可随时重新打开新的 detached 窗口

### 数据流

```
主窗口录音 → web_backend.py (更新 live_state) 
  ↓
app.js: renderLiveOutputs(data)
  ↓
if (detached_tc/tl_html) → pywebview.api.update_detached_content()
  ↓
webview_app.py: DetachedWindowManager.update_window_content()
  ↓
detached_window.js 接收 postMessage
  ↓
更新 #window-content 显示新文本
```

## 技术细节

### Window 模式
- **tc** (Transcribed) - 显示原始转录文本
- **tl** (Translated) - 显示翻译后的文本

### 通信协议 (postMessage)

**update-content**
```json
{
  "type": "update-content",
  "html": "<p>Content here</p>"
}
```

**update-config**
```json
{
  "type": "update-config",
  "config": {
    "font": "Arial",
    "opacity": 0.9,
    "color": "#00FF00"
  }
}
```

### CSS 特性
- `-webkit-app-region: drag` - 使 titlebar 可拖动
- `user-select: none` - 防止文本选择
- `border-radius: 8px` - 圆角边框
- `opacity` - 支持透明度

## 限制和注意事项

### pywebview 限制
1. **无原生 topmost** - pywebview 不支持 OS-level "always on top" 标志
   - Workaround: 依赖 Window Manager 的 z-order（用户可用窗口装饰器）
   
2. **进程通信** - 每个窗口后面是单独的 WebView 进程
   - postMessage 用于跨窗口通信
   - 不建议传输大量数据（大于 100MB）

3. **平台差异** - 不同操作系统的窗口行为略有不同
   - Windows: 支持最小化、最大化、移动
   - Linux: 取决于 Window Manager
   - macOS: 支持全屏（需启用）

### 性能考虑
- 实时内容更新可能产生频繁的 postMessage 调用
- 建议内容更新频率 < 100ms
- 如果 HTML 内容较大（> 10KB），考虑限制渲染频率

## 测试检查清单

- [ ] 点击 "Open Detached Window" 按钮 - 新窗口出现
- [ ] Transcribed/Translated 模式切换 - 打开对应窗口
- [ ] 拖动窗口标题栏 - 窗口跟随鼠标移动
- [ ] 点击最小化按钮 - 窗口最小化（内容隐藏但窗口保持显示）
- [ ] 点击关闭按钮 - 窗口关闭
- [ ] 主窗口最小化 - 观察 detached 窗口行为（应保持可见）
- [ ] 启动转录 - 内容在 detached 窗口实时更新
- [ ] 修改设置并保存 - 样式变化应在窗口中生效
- [ ] 重新打开关闭的 detached 窗口 - 新窗口创建成功
- [ ] 关闭应用 - detached 窗口自动关闭，无错误消息

## 未来改进

1. **多窗口布局记忆** - 记住每个窗口的位置和大小
2. **窗口透明度点击穿透** - 允许点击穿过透明区域（Windows 只有）
3. **快捷键绑定** - Alt+T 快速打开/关闭 Transcribed 窗口
4. **窗口主题** - 支持深色/浅色主题切换
5. **持久化窗口状态** - 下次启动时恢复窗口位置和内容

## 调试

启用调试模式查看 detached 窗口的 postMessage 通信：

```javascript
// 在 detached_window.html 中
window.addEventListener('message', (e) => {
  console.log('Message received:', e.data);
  // ... handle message
});
```

查看错误：
```
# 检查 Web 开发者工具 (F12)
# 检查 Python 日志文件 (speech_translate/log/)
# 运行时添加 --debug-webview 标志
```

## 参考资源

- pywebview 文档: https://pywebview.kivy.org/
- HTML postMessage: https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage
- CSS 拖動: https://developer.mozilla.org/en-US/docs/Web/CSS/-webkit-app-region

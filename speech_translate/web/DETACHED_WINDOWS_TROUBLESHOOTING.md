# Detached Windows - Troubleshooting Guide

## Problem 1: Drag Not Working

### Quick Test
1. Open `speech_translate/web/test_drag.html` in a web browser
2. Try dragging the blue test windows at bottom
3. If drag works in test page → Issue is specific to main app styling
4. If drag doesn't work → Issue is JavaScript implementation

### Debugging Steps

#### Step 1: Check Browser Console
1. Open main app
2. Press **F12** to open Developer Tools
3. Go to **Console** tab
4. Perform these tests:
   - Move mouse over detached window title bar
   - Click and hold on title bar → Should see: `"Drag started on titlebar"`
   - Move mouse while holding → Should see: `"Dragging to: X, Y"` (repeated)
   - Release mouse → Should see: `"Drag ended"`

**If you don't see these logs**: Mouse events aren't reaching the title bar

#### Step 2: Verify CSS Pointer Events
In Developer Tools (F12):
1. Inspect the blue title bar element (right-click → Inspect)
2. Check Styles tab - look for:
   - `pointer-events: auto;` (should be present)
   - `user-select: none;` (should be present)
   - `cursor: grab;` (should be present)
3. If `pointer-events: none;` appears → That's blocking clicks!
4. Check for `pointer-events: hidden;` in parent `.detached-float-window`

#### Step 3: Check Z-Index
In Developer Tools:
1. Inspect title bar element
2. In Styles, find `z-index: 10000;` or higher in `.detached-float-window`
3. If z-index is too low (< 1000) → Other elements might be on top blocking clicks
4. Make sure parent window has `pointer-events: auto;` set

#### Step 4: Test Simple Click
1. In browser console, run:
```javascript
document.querySelector('.detached-titlebar').click()
```
2. If you get error → Element doesn't exist on page
3. If no error → Element exists and is clickable

### Common Issues & Fixes

**Issue**: "Drag started on titlebar" log appears but "Dragging to: X, Y" doesn't
- **Cause**: `mousemove` handler not firing during drag
- **Fix**: Try clearing browser cache (Ctrl+Shift+Delete) and refresh

**Issue**: Window jumps to wrong position when dragging
- **Cause**: Offset calculation using old position values
- **Fix**: Check console for errors, try refreshing page

**Issue**: Can drag element but it snaps back immediately
- **Cause**: `left`/`right` CSS properties conflicting (having both `left` and `right` set)
- **Fix**: Check CSS - code should be setting `bottom: auto; right: auto;` when dragging

---

## Problem 2: Detached Windows Disappear When Main Window Minimized

### This is a pywebview Architecture Limitation
- pywebview is browser-based, not a native OS window
- When main window minimizes, all nested HTML divs get hidden
- **Workaround**: Use keyboard shortcuts or window focus to restore

### How to Show Windows Again

#### Option 1: Keyboard Shortcuts
- **Alt+T** - Show/toggle transcribed subtitle window
- **Alt+Y** - Show/toggle translated subtitle window

#### Option 2: Automatic Restore (if enabled)
1. Minimize and restore main window with taskbar click
2. Detached windows should automatically reappear (if "always on top" enabled in settings)

#### Option 3: Detached Window Settings
1. Open "Detached Window Settings" section
2. Check current mode (tc or tl)
3. Enable "Always On Top" checkbox
4. Click "Save Settings"
5. Next time main window minimizes/restores, windows will auto-restore

### Advanced: Show All Windows Button (Planned)
Future update will add a button to immediately show all hidden detached windows.

---

## Problem 3: Buttons Not Responding (Minimize/Close)

### Test
1. In browser console (F12), run:
```javascript
document.querySelector('.detached-close').click()
```
2. Window should disappear
3. If nothing happens → Button not wired to JavaScript

### Fix
1. Check that `app.js` has code for:
   - `minimizeBtn.addEventListener('click', ...)`
   - `closeBtn.addEventListener('click', ...)`
2. If missing, buttons weren't initialized properly
3. Try refreshing page (F5)

---

## Requesting Help

If drag still doesn't work after these steps, provide:
1. **Browser type and version** (Chrome, Firefox, Edge, etc.)
2. **Output from console** when trying to drag
3. **Screenshot** of Developer Tools Styles for title bar element
4. **Operating system** (Windows, macOS, Linux)

This information will help debug the issue faster.

---

## Keyboard Shortcuts Reference

| Shortcut | Action |
|----------|--------|
| **Alt+T** | Toggle transcribed subtitle window |
| **Alt+Y** | Toggle translated subtitle window |
| **F12** | Open browser Developer Tools (for debugging) |
| **Ctrl+Shift+Delete** | Clear browser cache (helps with CSS/JS reloads) |

---

## Files to Check in Development

If modifications needed:
- `speech_translate/web/app.js` - Drag event handlers, keyboard shortcuts
- `speech_translate/web/styles.css` - Pointer events, z-index, cursor styles
- `speech_translate/web/test_drag.html` - Test page for drag mechanics
- `speech_translate/webview_app.py` - Settings storage/retrieval

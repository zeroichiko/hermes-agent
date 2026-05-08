# ChatGPT CDP 表單提交 — 正確方法（2026-05-07 實測驗證）

## 問題背景
ChatGPT 使用 React，直接設定 `textarea.value` 後 dispatch `input` 事件 **不會**自動送出訊息。
Send 按鈕必須透過 `form.requestSubmit()` 觸發表單提交。

## 正確流程（已驗證可工作）

```python
# 1. 設定 textarea 值（React 會偵測）
ta = document.querySelector('textarea')
ns = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
ns.call(ta, '你好')
ta.dispatchEvent(new Event('input', {bubbles: true}))
ta.dispatchEvent(new Event('change', {bubbles: true}))

# 2. 提交表單
form = document.querySelector('form')
form.requestSubmit()  # action: https://chatgpt.com/new, method: GET
```

## 表單資訊
| 屬性 | 值 |
|------|-----|
| form action | `https://chatgpt.com/new` |
| form method | `get` |
| textarea name | `prompt-textarea` |

## ❌ 無效方法（本次 session 已驗證）

1. **`Input.dispatchKeyEvent` 逐字輸入** — 只輸入第一個字，React 不追蹤
2. **`Input.dispatchMouseEvent` 點 send 按鈕** — 按鈕無反應
3. **`Input.dispatchKeyEvent` 按 Enter** — 無效果
4. **`textarea.value = 'text'` 不加 prototype setter** — React 不更新

## 關鍵差異
- JS setter + `form.requestSubmit()` ✅ 成功
- `Input.dispatchKeyEvent` 逐字 + Enter ❌ 失敗
- `Input.dispatchMouseEvent` 點按鈕 ❌ 失敗

## 驗證步驟
1. 設定 textarea 值
2. 用 `document.querySelector('textarea').value` 確認值已設定
3. 執行 `form.requestSubmit()`
4. 等待 8-10 秒
5. 截圖或用 `document.body.innerText` 確認回覆
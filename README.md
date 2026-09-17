# LINE申請フォーム サンプル

## 2つの案

### 案A：Flex Message（LIFF不要・すぐ使える）
- LINEのリッチメッセージ（Flex Message）で入力を受け付ける
- 日付・時刻・テキストをQuickReplyで段階入力
- メリット：LIFF登録不要、すぐ実装可能
- デメリット：入力は複数回に分かれる、画像/ドロップダウン不可

### 案B：LIFFアプリ（本格Webフォーム）
- LINE内でブラウザが開き、HTMLフォームで入力
- 日付ピッカー、テーブル、自動計算など本格UI
- メリット：1画面で完結、リッチなUI
- デメリット：LIFF登録が必要（無料）

---

## 案Bの使い方（LIFFフォーム）

### 1. LIFFアプリを作成
1. [LINE Developers Console](https://developers.line.biz/) にログイン
2. チャネルを選択
3. 「LIFF」タブ → 「追加」をクリック
4. 以下の設定：
   - LIFF ID: `liff-application`
   - エンドポイントURL: `https://<your-domain>/liff_form_sample/index.html`
   - スコープ: `profile`, `chat_message.write`（オプション）
5. LIFF ID をコピー

### 2. HTMLファイルを更新
`index.html` の `YOUR_LIFF_ID` を実際のLIFF IDに置き換え：
```javascript
let liffId = '1234567890-abcdefgh'; // ← 実際のLIFF ID
```

### 3. Webhook URLを設定（オプション）
フォーム送信先のエンドポイントを用意：
- Google Apps Script（Web App）
- 外部サーバー（Flask/Node.js）
- または直接adapter.pyに処理を追加

### 4. LINEでフォームを開く
adapter.pyに以下を追加：
```python
from line.models.message import messages
from line.models.message.flex import FlexContainer, FlexBubble, FlexMessage

def send_liff_form(self, chat_id: str, form_type: str = "overtime"):
    """Send LIFF app URL as a flex message."""
    liff_id = "YOUR_LIFF_ID"
    liff_url = f"https://liff.line.me/{liff_id}?type={form_type}"
    
    bubble = FlexBubble(
        hero=None,
        body=FlexContainer(
            type="box",
            layout="vertical",
            contents=[
                {"type": "text", "text": "申請フォーム", "size": "xl", "weight": "bold", "color": "#667eea"},
                {"type": "text", "text": "タップしてフォームを開いてください", "size": "sm", "color": "#888888", "margin": "md"},
            ]
        ),
        footer=FlexContainer(
            type="box",
            layout="vertical",
            contents=[
                {
                    "type": "button",
                    "action": {"type": "uri", "label": "フォームを開く", "uri": liff_url},
                    "style": "primary",
                    "color": "#667eea",
                }
            ]
        ),
    )
    flex_msg = FlexMessage(
        alt_text="申請フォーム",
        contents=bubble,
    )
    self._client.send_message(chat_id, messages=[flex_msg])
```

---

## 承認フローの例

### 3者協議パターン（ユーザー指定）
1. **申請者**がフォーム送信
2. **上長**に「承認しますか？」のQuickReplyを送信
3. **上長**が「承認」をタップ
4. **管理者**に最終確認を送信
5. **管理者**が「承認」→ 申請者に完了通知

### 簡易パターン（1人承認）
1. 申請者がフォーム送信
2. 上長に承認依頼
3. 上長が承認/却下
4. 申請者に結果通知

---

## 必要な作業

1. **LIFF ID取得**（案Bの場合）：LINE Developers Consoleで5分
2. **Webhook URL用意**：Google Apps Script / Flask / Node.js
3. **承認ルート設定**：chat_idを複数登録
4. **保存先選択**：Google Sheets / PostgreSQL / Airtable / Notion

## 相談ポイント（上司との確認事項）
- [ ] LIFFを使うか、Flex Message+QuickReplyで進めるか
- [ ] 承認ルート（1人承認 or 3者協議）
- [ ] 保存先（既存のシステムがあるか）
- [ ] セキュリティ要件（申請内容の暗号化など）
# liff-drawing-search

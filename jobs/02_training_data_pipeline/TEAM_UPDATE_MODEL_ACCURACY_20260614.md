# 團隊更新：Issue Category 模型（2026-06-14）

## 1) 過去兩天已完成的優化

- 統一標籤政策，降低訓練資料中的標籤噪音。
- 維持高準確率的 production 模型路徑，同時加入執行期分類口徑整併行為。
- 新增針對高風險混淆對的重點人工複核迴圈。
- 新增 dual-label 複核處理，避免對可接受替代答案過度懲罰。
- 新增更安全的改標套用檢查（更新來源資料前先檢查 unmatched/ambiguous）。

## 2) 目前 Production 狀態

- 目前上線模型基準仍維持 round4b 路線，accuracy 約 0.8933。
- 執行期輸出已將 ICPS 與 Killer 視為同一個 canonical 類別：ICPS/Killer。
- 已提供合併口徑的團隊報表視圖，方便分享與儀表板對齊。

參考檔案：
- [models/issue_category_model.joblib](models/issue_category_model.joblib)
- [models/issue_category_model_metrics.json](models/issue_category_model_metrics.json)
- [models/issue_category_model_metrics_merged_label_view.json](models/issue_category_model_metrics_merged_label_view.json)

## 3) 每週 Human Category 新填寫規範（重要）

填寫每週標註檔時，請一律遵守以下 canonical 標籤規則。

### 3.1 必填規則

1. 除非另有要求，僅填寫 human_category 欄位。
2. 請勿刪除資料列，也不要調整欄位順序。
3. 只能使用 canonical 類別名稱。

### 3.2 Canonical 標籤規則（更新版）

1. ICPS/Killer 為單一合併類別。
   - 不要填 ICPS。
   - 不要填 Killer。
2. 超出範圍或無法判定的案例，請填 Need-Triage。
   - 不要填 Unknown。
   - 不要填 Not-Wireless。
   - 不要填 Needs-Triage。
3. Miracast/Wi-Fi Direct 案例請填 P2P。
   - 不要再建立 Miracast 獨立標籤。
4. 以下標籤已移除，請勿使用：
   - WAPI
   - Power on sequence

### 3.3 快速填寫範例

- 若屬於軟體側 ICPS 或 Killer 行為：填 ICPS/Killer。
- 若看起來與無線議題無關：填 Need-Triage。
- 若為無線投影或 Wi-Fi Direct 配對/投影問題：填 P2P。

## 4) 每週標註 Do/Don’t

Do：
- 標籤命名請與 canonical 清單一致。
- 情境模糊時請補上註記。
- 無法確定時請升級確認，不要自行創建新類別。

Don’t：
- 不要使用舊別名。
- 不要臨時新增類別名稱。
- 不要留下會破壞 CSV 結構的不完整編輯。

## 5) 每週檔案建議通知訊息（可直接貼）

各位團隊夥伴您好，

請於本週標註檔中填寫 human_category，並僅使用 canonical 標籤。

本週關鍵政策更新如下：
- ICPS 與 Killer 已合併為單一類別：ICPS/Killer。
- Unknown / Not-Wireless / Needs-Triage 一律改填 Need-Triage。
- Miracast / Wi-Fi Direct 一律標為 P2P。
- 請勿使用 WAPI 或 Power on sequence。

請保持原始列與欄位結構不變，並於週五截止前完成。

謝謝。

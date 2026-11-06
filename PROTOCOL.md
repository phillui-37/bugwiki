# PROTOCOL.md — 記憶庫協議 v1

對象：`~/.agents/wiki` 內容根（現有 9 頁 solution、LESSONS.md、README index）
程式：`~/poc/agent-wiki`（`bin/wiki`）
狀態：**schema 已實作並通過 `verify --migration`（9 頁零改動）**。內容根未安裝：要先 `git init` 才有可逆性。

---

## 0. 前提（已定）

| 決定 | 內容 |
| --- | --- |
| 單一 store | dream 係手段，產出**最終落 wiki**。冇第二個 store，所以 canonical 衝突問題自動消失 |
| 自動維護 | 機械嘅自動做；**可逆嘅唔需要批准**（見 §3）。撤回 v0 嘅提案制 |
| 多 handler | 唔止一個 handler；但見 §5，要分「做事的人」同「擁有隊列的人」 |
| Migration | 純加法。唔搬檔、唔改名、唔改 `related:` 路徑 |

---

## 1. Node schema

```yaml
---
slug: hkma-sorted-view-caches-sorted-rows      # 必須 = 檔名 stem
kind: solution                                  # solution | fact | raw
maturity: distilled                             # distilled | promoted
status: verified                                # verified | open | obsolete | conflict
scope: repo:hkma-service                        # 這條事實對邊個範圍成立
source: [thread:mw2vqil15g59a0yfjq9, doc:docs/cache-branch-review.md]
valid_from: 2026-11-05                          # 事實何時開始為真
valid_to: null                                  # 何時不再為真（null = 仍有效）
recorded_at: 2026-11-05                         # 幾時寫入記憶
verified_at: 2026-11-05                         # 最後一次用證據核對
expires: 2026-11-19                             # 幾時要重新驗（null = 唔會過期）
supersedes: [hkma-cache-branch-pending]
contradicts: []
related: [../hkma/springboot4-port-points.md]
recurrence: 1                                   # 獨立日期上嘅發生次數（保留）
last_seen: 2026-11-05
---
```

**硬限制（`bin/wiki` 的 `field()` 係 line-oriented）**

- 只准 flat scalar 同**單行 inline list**：`supersedes: [a, b]`
- block list（`- a` 逐行）一定解析唔到 → 禁止
- 值裡面唔准有 `#`（會被當註解截斷）
- 日期一律 `YYYY-MM-DD`，時間唔入 frontmatter

**`kind` 缺席時由路徑推導**（現有頁零改動即可讀）

```
solutions/**    → kind: solution
facts/**        → kind: fact
.raw/**         → kind: raw（唔入 find 範圍）
```

---

## 2. 讀取協議

**觸發點**：要用「記住嘅操作性狀態」去做下一步（branch 狀態、config 值、版本、某人偏好、之前嘅決定）之前。

```
1. wiki find "<exact term>"          # 用錯誤原文／identifier／檔名，唔用抽象詞
2. 最多開 2 個節點
3. 順 supersedes 鏈行到鏈尾，取最新一環
4. status: obsolete / conflict / expires 已過 → 當「唔知」，唔可以當答案用
5. 命中並用咗 → bump recurrence + last_seen（現有規則，唔另設）
6. 60 秒內找不到 → 直接做，做完如有價值就寫 proposal；唔准無限瀏覽
```

第 4 步係協議嘅核心。舊記憶錯唔係因為冇記錄，係因為**過期嘅記錄同有效嘅記錄長得一樣**。所以判準唔可以係「有冇寫」，只可以係 `status` + `expires` + `valid_to`。

**輸出格式**（`wiki show` 要答得出）：

```
hkma-cache-branch-pending   solution  obsolete  valid 2026-10-28→2026-11-05
  superseded by hkma-cache-branch-merged-on-origin (2026-11-05)
```

行尾一定要有「呢條仲有冇效、邊條取代佢」。冇呢行，reader 就會用 obsolete 嘅內容。

---

## 3. 寫入協議（自動寫入 + 可逆）

**v0 嘅提案制已撤回**（2026-11-06）。理由見下：提案制把一件可以機械化嘅事（「有事實來源就可以寫」）變成一件要人重複決定嘅事，同現有 wiki「機械自動做」嘅設計相反。

**規則**

```
.raw/                          transcript，只由 source: 指過去，唔入 find 範圍
facts/<slug>.md                dream 直接寫
solutions/<domain>/<slug>.md   人手寫，或 dream 提出後由人開頁
AGENTS.md                      永遠唔可以由自動流程寫
maturity: promoted             永遠唔可以由自動流程寫
```

Dream 每次 run：

1. 逐 transcript 抽事實，寫 `facts/<slug>.md`
2. `status` 由**證據類別**決定，唔由自信程度決定：
   - `verified` —— 事實可以追到 transcript 內一個 **tool result**（指令輸出、檔案內容、API 回應）
   - `open` —— 只追到 agent 嘅敘述或推論
3. 舊事實被推翻 → 唔刪，舊節點 `valid_to` 補上、`status: obsolete`、新節點 `supersedes: [old]`
4. 每次 run **一個 git commit**：`dream 2026-11-06: 3 new, 1 superseded, 0 conflict`
5. 報告只寫一行，**append 到 `reports/<date>.md`**（housekeep 已經用呢個檔）—— 唔開新表面

**冇 proposal queue、冇 accept 命令、冇 7 日過期。**

指令對應：

```bash
wiki dream export [--since D] [--thread ID] [--dry-run]   # threads -> .raw/<date>/*.txt
wiki new fact <slug>                                      # 開一條 fact（分析步驟由 agent 做）
wiki dream commit [-m msg]                                # 一個 commit + 一行報告
```

報告寫 `reports/<date>.dream.md`，**唔寫** `reports/<date>.md` —— housekeep 每晚會整份覆寫後者，寫入去會被沖走（實測）。

**煞停機制係可逆，唔係批准**

| 風險 | 機制 |
| --- | --- |
| 寫錯一條事實 | `git revert` 一次；或 `status: open` 令讀取協議唔當它係答案 |
| 事實過時 | 易變 fact 強制 `expires`；過期即當「唔知」 |
| 壞 run 大量寫入 | commit message 有計數；`git log --oneline` 一眼睇到異常那晚 |
| 污染 transcript 想寫指令 | 自動流程寫唔到 `maturity: promoted` 同 `AGENTS.md` —— 呢兩樣才會入每個 prompt |

**真邊界係「reach」，唔係「canonical」**：自動流程可以寫 canonical facts，因為 git 令佢可逆；唔可以寫會進入每個 session prompt 嘅嘢（promoted / AGENTS.md）。舊稿講「automation 唔准寫 canonical」係錯嘅煞停點。

**Git 係前置條件，唔係 nice-to-have**：`~/.agents/wiki` 同 `~/.agents` 今日都**唔係 git repo**（已驗）。冇 git 嘅自動寫入係不可逆，所以 `git init` 必須在開 dream 之前做，並且每次 run 一個 commit。

## 4. 門檻（per kind，唔再用單一 180 日）

| kind | 幾時算 stale | 動作 |
| --- | --- | --- |
| `fact` 易變（repo/branch/config/版本） | `expires` 必須明寫，預設 14 日 | 過期即當「唔知」，排入 re-verify 隊列 |
| `fact` 穩定（機制、因果） | 90 日 | 排入 re-verify 隊列，唔阻使用 |
| `solution` | 180 日 | 維持現狀（`WIKI_STALE_DAYS`） |
| `promoted` | 90 日 | 驗唔到就降返 `maturity: distilled` |

現有 `WIKI_STALE_DAYS=180` 只管 solution；新 kind 用新閾值，env 可改（`WIKI_FACT_STALE_DAYS` / `WIKI_VOLATILE_DAYS`）。

**易變 fact 一定要 `expires`**：呢個係防止 11-05 branch 個案重演嘅唯一機制 —— 當日錯唔係寫錯，係寫啱之後**冇人知佢會變**。

---

## 5. Handler 模型：verifier 可以多，sweeper 只可以一個

| 角色 | 數目 | 做咩 |
| --- | --- | --- |
| verifier | 不限 | 任何 session 撞到 stale 節點，都可以用新證據 `verified_at` 更新、或者寫 proposal |
| sweeper | **1** | 擁有 stale 隊列：每晚列出邊啲要驗、發 proposal、7 日後清過期提案 |
| writer | dream（自動）＋ 人手 | 兩者都經 git commit；冇 accept 關卡 |

「多 handler」成立嘅部分係 verifier —— 呢個係好設計，因為驗證嘅成本散落喺本來就要做嘅工作入面。但**隊列一定要有一個 owner**，否則 `expires` 過期嘅節點會永遠掛住 `verified`，同今日 `MEMORY.md` 一樣。

---

## 6. Migration：純加法，5 條規則

1. **唔搬檔、唔改名**：`solutions/<domain>/<slug>.md` 位置不變
2. **`kind` 缺席 = 由路徑推導**：現有 8 頁一行都唔改都讀得到
3. **`related:` 保留相對路徑**：已驗 9 條全部解得通；轉 stem slug 會丟 domain 資訊兼有撞名風險
4. **新欄位全部 optional**：`valid_from`/`valid_to`/`expires`/`supersedes` 缺席時，行為同今日一樣
5. **`README.md` index table 保留**：graph edge 係補上去，唔係取代 index

**驗證（apply 前後都要 0 error）**

```sh
wiki lint                 # 現有檢查 + 每條 edge 解得到
wiki verify --migration   # 逐頁讀：frontmatter 解得通、kind 推導到、日期格式對
```

未 apply 之前，`wiki verify --migration` 應該對現有 8 頁全部 pass。任何一頁 fail = 唔 apply。

---

## 7. 已知取捨（寫明，唔裝作冇）

- **自動寫入靠 git 兜底**。冇 commit 就冇得 revert，所以 git init 係前置。出事時嘅 audit 係 `git log -p`，唔係每晚讀報告 —— review on demand，唔係 review on schedule。
- **`rg` 起手靠 lexical 命中**。抽象查詢（「上次個 race condition」）找唔到就係找唔到，graph 幫唔到。緩解：`source:` 同 `slug` 要寫得可 grep（identifier 優先）。
- **`.raw/` 只係體積邊界，唔係安全邊界**。fact 由 raw 衍生，污染一樣傳得出。真邊界係「自動流程改唔到 canonical」。
- **冇 bi-temporal 就冇得答「當時係咪真」**。有 `valid_from/valid_to` 之後答得到，但只限有人肯填。

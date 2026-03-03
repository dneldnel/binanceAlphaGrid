# BSC 主网分阶段放量 Runbook

最近更新时间：`2026-03-04`

## 目的

这份 runbook 用来执行并验收以下四个阶段：

1. 测试网写链验证
2. 主网只读验证
3. 主网最小仓位 live
4. 主网扩容放量

它和 `doc/bsc_live_rollout_progress.md` 的分工是：

1. 进度文档记录“做到了什么”和“还缺什么”。
2. runbook 记录“下一阶段具体怎么跑”和“什么算通过”。

## 当前确认顺序

`2026-03-04` 已确认：

1. 本轮不再把“测试网写链验证”和“主网只读验证”作为上线前门禁。
2. 当前执行顺序调整为：`主网最小仓位 live -> 主网扩容放量`。
3. 阶段 0 和阶段 1 保留为回退路径；如果主网最小仓位 preflight 或首笔成交不稳定，回退去补跑。

## 通用前提

进入任一阶段前，都先确认：

1. `config/live.toml` 中所有 `0x000...` 占位地址已经替换完成。
2. RPC、router、wallet、token decimals、route path 都已经过手工复核。
3. 每个环境使用独立的 SQLite 文件，避免测试网、主网只读和主网 live 共用状态。
4. 先执行 `python main.py --config config/live.toml --preflight`，确保没有新的配置级失败项。
5. `risk.kill_switch_file` 可写、可删，并已手工验证文件存在时会停止新单。
6. `max_daily_realized_loss_usd`、`max_daily_gas_usd`、`max_consecutive_failed_tx` 都设置为非零值。
7. 主网最小 live 前，`universe.max_symbols = 1`，并只保留一个真实 symbol。

## 阶段 0：测试网写链验证（当前跳过，保留为回退路径）

### 目标

验证 live 写链链路本身，而不是验证收益。

### 配置要求

1. `chain.chain_id` 指向测试网，不是 `56`。
2. `runtime.mode = "live"`。
3. `runtime.allow_live = true`。
4. `runtime.allow_mainnet = false`。
5. 使用测试网钱包、测试网 router、测试网 token。

### 检查项

1. 启动时 RPC chain id 与配置一致。
2. `preview_buy / preview_sell` 能稳定返回。
3. approve、swap、receipt 都能跑通至少一轮。
4. 重启后能恢复 `prepared / submitted / retryable / cancelling` 的 pending tx。
5. 人工制造低 gas 或延迟场景时，replacement bump 和 cancel 路径能被触发。

### 通过条件

1. 至少完成 3 笔成功 receipt 的真实测试网成交。
2. 至少验证 1 次 pending tx 恢复。
3. 至少验证 1 次 replacement 或 cancel 路径。
4. 没有残留无法解释的 `orphaned` 单据。

## 阶段 1：主网只读验证（当前跳过，保留为回退路径）

### 目标

验证真实主网 quote、余额同步、风控信号和状态恢复，但不写链。

### 配置要求

1. `chain.chain_id = 56`。
2. `runtime.mode = "paper"`。
3. `runtime.allow_live = false`。
4. `runtime.allow_mainnet = false`。
5. `risk.mainnet_buy_enabled = false`。
6. `risk.mainnet_sell_enabled = false`。

### 检查项

1. 连续运行至少 30 分钟，无未处理异常退出。
2. quote 年龄始终满足 `market.stale_quote_sec` 约束。
3. 链上 base / quote 余额同步结果与钱包实际余额一致。
4. symbol 级异常隔离正常，单个 symbol 异常不会打断整轮。
5. `pause_on_route_failure / pause_on_liquidity_drop / pause_on_honeypot_signal` 至少完成一次人工演练或日志核对。

### 通过条件

1. 连续运行窗口内没有误触发写链路径。
2. 没有持续累积的 `ERROR` / `PAUSED` 状态且原因可解释。
3. `pending_txs`、`execution_attempts` 不会因为只读模式产生脏数据。

## 阶段 2：主网最小仓位 live（当前起始阶段）

### 目标

在单币、单侧、极小金额前提下，验证主网 live 的最小闭环。

### 当前执行说明

1. `2026-03-04` 起，本轮直接从这一阶段开始执行。
2. 如果本阶段出现无法快速解释的 quote、approve、swap、receipt、pending recovery 问题，优先停止放量，而不是硬顶着继续。
3. 必要时回退去补跑阶段 1 或阶段 0。
4. 当前建议先用 `python main.py --config config/live.toml --preflight` 跑到只剩“真实钱包 / 真实 symbol route 缺失”这一类阻塞，再做首笔主网成交。

### 配置要求

1. `chain.chain_id = 56`。
2. `runtime.mode = "live"`。
3. `runtime.allow_live = true`。
4. `runtime.allow_mainnet = true`。
5. `universe.max_symbols = 1`，且只保留一个真实 symbol。
6. 只保留单层网格，避免多层同时开单。
7. 只打开一个方向：
   - 要么 `risk.mainnet_buy_enabled = true` 且 `risk.mainnet_sell_enabled = false`
   - 要么 `risk.mainnet_buy_enabled = false` 且 `risk.mainnet_sell_enabled = true`
8. `risk.mainnet_max_notional_per_order` 维持极小值，初始建议不高于 `5.0`。
9. `risk.mainnet_max_position_per_symbol_usd` 维持极小值，初始建议不高于 `25.0`。

### 检查项

1. 首单前先确认钱包真实余额、allowance 和当前 nonce。
2. 首单后确认 SQLite 中同步写入：
   - `pending_txs`
   - `execution_attempts`
   - `fills`
   - `positions`
3. 确认 receipt/event 回写的成交数量与链上浏览器一致。
4. 人工触发 `kill_switch_file`，确认会停止新单。
5. 若出现 stuck tx，确认 replacement / cancel / recovery 行为与预期一致。

### 通过条件

1. 至少完成 3 笔主网真实成交。
2. 没有无法解释的 open pending tx 长时间滞留。
3. 链上余额变化、SQLite fill 和 PnL 结果可对账。
4. 没有触发日内 gas / 亏损 / 连续失败熔断。

### 额外说明

1. `risk.mainnet_*` 只限制常规网格单。
2. `hard_stop_from_cost_bps` 触发的 emergency sell 仍应保留，不能因为单侧开关被堵死。

## 阶段 3：主网扩容放量

### 目标

在最小 live 稳定后，逐项放宽限制，而不是一次性切到多币满仓。

### 放量顺序

1. 先增加成交次数，再增加 notional。
2. 先放宽 `mainnet_max_position_per_symbol_usd`，再考虑增加 symbol 数量。
3. 先验证双侧，再考虑多币。

### 每次放量前都要确认

1. 上一阶段至少连续运行 24 小时，无未解释异常。
2. 所有 `pending_txs` 都已收敛，没有遗留 `orphaned / cancelling`。
3. 当前 RPC 对 txpool / pending-block 的支持情况已记录，知道恢复逻辑走的是哪条降级路径。

### 每次放量后都要确认

1. 日内 gas、失败笔数、realized pnl 没有明显劣化。
2. quote / preview / receipt 的日志噪声没有显著上升。
3. 若新增 symbol，共享 quote 余额分配结果与预期一致。

## 暂不放量的情形

出现以下任一情况，停止推进下一阶段：

1. 外部替换交易识别出现无法解释的误判或漏判。
2. honeypot / liquidity / route pause 连续给出无法核实的异常信号。
3. receipt/event 与链上浏览器对账出现不可解释偏差。
4. `kill_switch`、cancel tx 或 pending tx 恢复任一关键链路未通过演练。

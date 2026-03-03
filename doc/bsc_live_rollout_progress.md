# BSC 链上实盘接入需求与进度

## 文档目的

这份文档是当前仓库接入 `BSC 链上实盘` 的唯一进度基线。

这里的 `实盘` 明确定义为：

1. 接 `BSC/BNB Chain` 链上报价与交易执行。
2. 通过链上 Router / 聚合器报价、授权、签名、广播、回执完成交易。
3. 不接 `Binance Spot API`。

后续每次出现以下任一情况，都要同步更新本文件：

1. 收到新的开发任务。
2. 确认新的开发计划或实施顺序。
3. 完成一个阶段性改动。

每次更新至少要做这三件事：

1. 修改“最近更新时间”。
2. 更新“状态矩阵”或“当前确认的实施计划”中的相关内容。
3. 在“更新日志”追加一条记录。

最近更新时间：`2026-03-03`

## 目标范围

按既定顺序推进，不直接上主网开干：

1. 先把模拟执行替换成真实链上读写。
2. 先接可控的单一路由源，不先做复杂聚合。
3. 补齐实盘必需的配置、风控和 kill switch。
4. 按 `测试网 -> 主网只读 -> 主网最小仓位 -> 多币扩展` 推进。

## 已确认的开发要求

### 四步接入路径

#### 第一步：真实链上读写替换模拟执行

保持策略核不动，优先替换两个模块：

1. `src/modules/quote.py`
   输出 `exec_buy_price / exec_sell_price / spread_bps`，但数据来源改成真实链上询价。
2. `src/modules/execution.py`
   负责：
   - allowance 检查
   - 必要时 approve
   - 构造 swap 交易
   - 签名
   - 发送 raw tx
   - 等 receipt
   - 回写成交结果

#### 第二步：先接单一路由源

当前阶段目标不是最优成交，而是先稳定跑通：

1. quote
2. approve
3. swap
4. receipt
5. 失败重试
6. nonce 管理

#### 第三步：补齐实盘配置与风控

实盘前至少需要这些字段：

1. 钱包与账户
   - `wallet_address`
   - `private_key_env`
2. 实盘开关
   - `live_enabled = false`
   - `allow_mainnet = false`
3. 路由参数
   - `router_address`
   - `router_abi_path`
   - `base_token_address`
   - `quote_token_address`
4. 交易保护
   - `slippage_bps`
   - `deadline_sec`
   - `max_gas_gwei`
   - `max_gas_usd_per_tx`
   - `max_price_impact_bps`
5. 风控
   - `kill_switch_file`
   - `max_daily_loss_usd`
   - `max_consecutive_failed_tx`
   - `max_notional_per_order`
   - `max_position_per_symbol_usd`

核心原则：

1. `live_enabled` 默认为 `false`。
2. 没有显式打开前，程序只能 dry-run。

#### 第四步：按环境分阶段放量

1. 测试网先验证 RPC、签名、nonce、发送、receipt。
2. 主网只读模式接真实 RPC 和真实 quote，但不发交易。
3. 主网最小实盘先跑单币、极小金额、单层网格、单侧交易。
4. 单币稳定后再扩展多币和动态轮动。

### 下一版应落地的六项开发任务

1. 在 `config` 里新增 `live.toml` 模板，补钱包、路由、风控字段。
2. 给 `ExecutionEngine` 定义统一接口：
   - `preview_buy`
   - `preview_sell`
   - `execute_buy`
   - `execute_sell`
3. 给 `QuoteEngine` 改成“真实 quote / 模拟 quote”双实现。
4. 加 SQLite 持久化：
   - fills
   - positions
   - pending tx
   - realized pnl
5. 加 `--mode dry-run|paper|live`。
6. 加 kill switch：
   - 检测某个文件存在就立即停止新单。

## 当前进度审计

### 总体判断

当前仓库处于“执行层已接上入口、但风控与持久化仍未补齐”的阶段，**不能视为可上主网**。

原因不是策略核本身，而是执行层接入还没有完整闭环：

1. live / paper / dry-run 模式已经接到入口层。
2. 默认 dry-run 基线已经恢复可运行。
3. nonce-aware 的 pending tx 恢复、replacement gas bump 第一版、README/依赖声明对齐、`max_daily_gas_usd` 的 receipt 实耗统计，以及 live 配置模板收口都已补齐，但 cancel/txpool 能力和主网推进清单仍未完成。

### 状态矩阵

| 事项 | 状态 | 当前情况 | 主要缺口 |
| --- | --- | --- | --- |
| 真实 quote / 模拟 quote 双实现 | 已完成 | `src/modules/quote.py` 已通过 builder 接入；`paper/live` 使用真实 quote，`dry-run` 使用模拟 quote | live quote 仍只有基础 `getAmountsOut`，后续还要补独立预览接口与更细致的价格保护 |
| 真实执行层 approve/swap/sign/send/receipt | 部分完成 | `src/modules/execution.py` 和 `src/evm.py` 已实现 allowance、approve、swap、签名、广播、wait receipt，并已拆成 preview / execute 两阶段接口；live fill 现已优先按 receipt 中的 ERC20 `Transfer` 日志回写，余额差分作为 fallback；approve/swap nonce 与 gas price 现已随 pending tx 持久化，恢复阶段会按 wallet `latest/pending nonce` 判断“继续等待 / 可安全重试 / 需人工处理”，同 nonce 重试会按上一笔 gas 做 replacement bump | 还没有 cancel tx 策略，也没有 txpool / 外部替换交易识别能力；复杂路由/聚合器下的成交核对边界还没有专门处理 |
| 单一路由源接入 | 部分完成 | `src/evm.py` 已按通用 `UniswapV2 Router` 方式接了单一路由器；router ABI 现已支持通过 `router_abi_path` 从文件加载；preview 阶段已新增价格冲击与 gas 成本估算，用于实盘前风控拦截；route/liquidity/honeypot 一阶 pause signal 已接入 | 仍缺真实流动性/税费数据源，以及更稳健的失败分类 |
| live 配置模板 | 已完成 | `config/live.toml` 已补齐安全默认值、`router_abi_path`、`max_notional_per_order`、`max_position_per_symbol_usd`，并附带 `config/abi/uniswap_v2_router.json` 模板；常规买卖单现已真正受这些字段约束 | 主网推进清单仍是单独未完成项，不属于模板缺口 |
| CLI 模式切换 `dry-run/paper/live` | 已完成 | `src/cli.py` 已支持 `--mode dry-run|paper|live` 覆盖配置模式 | `paper` 仍依赖一份可用的链上配置才能实际跑通 |
| ExecutionEngine 统一接口 | 已完成 | `ExecutionEngine` 已拆成 `preview_buy / preview_sell / execute_buy / execute_sell`，`app` 已切到新的调用路径，preview 结果也已可写入 `pending_txs`；receipt/event 核对、nonce 落库、运行中恢复扫描、in-flight 限流和 replacement gas bump 都已接进统一执行层 | 还没有 cancel tx 策略与更细的风险元数据持久化 |
| SQLite 持久化 | 部分完成 | 已新增 SQLite `StateStore`，并落地 `fills / positions / pending_txs / realized_pnl / execution_failures / execution_attempts` 表；主循环会同步 positions / realized pnl，fill 完成后会写 fills，live preview 会写 pending_txs，执行失败会写 `execution_failures`，实际发单前会写 `execution_attempts`；启动时会从 SQLite 恢复 position；pending tx 已扩展到 `prepared / submitted / retryable / confirmed / failed / orphaned`，并新增启动恢复、有限重试、nonce/gas price 持久化、运行时恢复扫描和 receipt/event 成交回写；`execution_attempts` 现在会同时保存 estimated/actual gas，并按 receipt 实耗汇总日内 gas | 失败分类和人工处置面板还不够细；无 receipt 的 in-flight 单据在确认前不会计入实际 gas |
| kill switch | 已完成 | `risk.kill_switch_file` 已接入配置；文件存在时主循环会停止新单并把 symbol 状态置为 `HALTED` | 当前架构仍是同步执行，没有异步 inflight tx 需要额外保护 |
| live 安全开关 | 部分完成 | `src/evm.py` 已将只读 quote 与可写执行分离；写模式仍校验 `allow_live`、`allow_mainnet`；`config/live.toml` 已提供这两个开关 | 还没有更细的 live 保护开关，例如主网最小仓位/单侧限制开关 |
| 主网推进流程文档化 | 未完成 | 代码中没有测试网/主网只读/最小仓位分阶段流程 | 缺少执行清单和验收标准 |

### 代码级审计结论

#### 1. 已经存在的链上接入基础

以下内容已经落地，说明仓库不再是纯空壳：

1. `src/modules/quote.py`
   - 已有 `LiveQuoteEngine`
   - 使用 `EvmRouterClient.get_amounts_out(...)` 生成可执行买卖价
2. `src/modules/execution.py`
   - 已有 `LiveExecutionEngine`
   - 已调用 allowance、approve、swap
3. `src/evm.py`
   - 已实现：
     - ERC20 allowance / approve
     - Router `getAmountsOut`
     - swap 构造
     - 本地签名
     - `send_raw_transaction`
     - `wait_for_transaction_receipt`
4. `src/core/config.py` 与 `src/core/models.py`
   - 已有：
     - `chain`
     - `router`
     - `allow_live`
     - `allow_mainnet`
     - gas / slippage / retry 相关配置结构

#### 2. 当前最关键的阻塞项

这些问题决定了当前版本还不能作为“可控实盘基线”：

1. `pending_txs` 虽已补到 replacement gas bump 第一版，但还没有 cancel tx 策略与 txpool 视角的更强恢复能力。
2. 主网推进清单仍未接入，测试网 / 主网只读 / 最小仓位的验收标准还没落盘。
3. honeypot / liquidity / route pause 仍主要依赖启发式信号，不是完整的数据面风控。

#### 3. 即使修好入口，离“可控实盘”仍差的部分

1. `pending tx` 已有 nonce-aware 的恢复、in-flight 限流和 replacement gas bump 第一版，但仍没有 cancel tx 管理器，也没有 txpool 数据面来识别外部替换交易。
2. receipt/event 成交核对已经接入，但复杂路由、聚合器或特殊税费 token 下的边界还没有专门处理。
3. 没有主网最小仓位、单币、单侧的显式保护开关。
4. `paper` 模式虽然已打通入口，但仍需要一份完整的链上配置才能实际验链。
5. `live/paper` 启动虽然已增加链上余额同步和 symbol 级异常隔离，但运行时余额校准、共享 quote 资金分配策略和后续生命周期管理还没有继续下沉。
6. `max_daily_gas_usd` 现在按 receipt 实耗统计；但没有 receipt 的 in-flight 交易会在确认前暂不计入。

## 六项任务的当前判定

### 1. `config/live.toml` 模板

状态：`已完成`

说明：

1. 已新增 `config/live.toml`，默认以 `paper` 模式启动，避免直接进入可写主网模式。
2. 模板已补齐当前代码已支持的链上字段：
   - `[router]`
   - `router_abi_path`
   - `allow_live`
   - `allow_mainnet`
   - `wallet_address`
   - `private_key_env`
   - `[risk].max_notional_per_order`
   - `[risk].max_position_per_symbol_usd`
   - symbol route 的 `buy_path / sell_path`
3. 已新增 `config/abi/uniswap_v2_router.json`，`src/evm.py` 会优先按 `router_abi_path` 加载 ABI；同时兼容 ABI 数组和带 `abi` 字段的 JSON。
4. 常规买卖单现已真实受 `max_notional_per_order` 与 `max_position_per_symbol_usd` 约束；未显式配置 `max_position_per_symbol_usd` 时，会 fallback 到旧字段 `inventory.max_base_exposure_usd`。
5. `kill_switch_file` 已接入代码。

### 2. `ExecutionEngine` 统一接口

状态：`已完成`

说明：

1. `ExecutionEngine` 已拆成：
   - `preview_buy`
   - `preview_sell`
   - `execute_buy`
   - `execute_sell`
2. `src/app.py` 已切到“先 preview，再 execute”的调用路径。
3. `LiveExecutionEngine` 的 preview 现在会返回路由执行所需的 raw amount / path / min out。

### 3. `QuoteEngine` 双实现

状态：`已完成`

说明：

1. 代码里已有 `SimulatedQuoteEngine` 和 `LiveQuoteEngine`。
2. 入口层已统一通过 builder 接线。
3. `paper` 模式已定义为“真实 quote + dry-run execution”。

### 4. SQLite 持久化

状态：`部分完成`

说明：

1. 已新增 `src/modules/state_store.py`，并初始化 SQLite schema。
2. 已落地：
   - fills
   - positions
   - pending tx
   - realized pnl
3. `src/app.py` 已在运行时同步写入 positions / realized pnl。
4. fill 成功后会写入 `fills`，live preview 会创建 `pending_txs`。
5. 启动时已支持从 SQLite 恢复 symbol position。
6. 当前已补齐：
   - `approve_nonce / swap_nonce` 持久化
   - 基于 wallet `latest/pending nonce` 的 pending tx 恢复判断
   - 运行中循环恢复扫描与单 symbol / 全局 in-flight 限流
   - 基于上一笔 gas price 的 replacement bump
7. 当前还没有：
   - 更细的失败分类与人工处置辅助
   - 面向 in-flight 未确认交易的单独 gas 预算前瞻

### 5. `--mode dry-run|paper|live`

状态：`已完成`

说明：

1. `src/cli.py` 已支持 `--mode dry-run|paper|live`。
2. `paper` 语义已接通为“真实 quote、不开实单”。
3. 实际使用 `paper` 时仍需要完整 router/RPC 配置。

### 6. kill switch

状态：`已完成`

说明：

1. `risk.kill_switch_file` 已接入配置解析。
2. 文件存在时，`src/app.py` 会停止新单并将 symbol 状态标记为 `HALTED`。
3. 当前执行路径是同步式的，因此“只停新单、不影响后台待确认交易”的异步状态管理仍是后续增强项。

## 当前确认的实施计划

`2026-03-03` 已确认：

1. 上一轮补充审计中的三项工作没有硬依赖，可以按 `严格审查 -> pending tx 设计 -> 实施表落盘` 的顺序处理。
2. 从现在开始，这份文档既记录“已完成”，也记录“已确认但尚未开工”的计划顺序。
3. 下一阶段先补执行与状态一致性，不先改策略核参数。

### 当前优先级顺序

1. 补更强的 pending tx 管理能力：
   - cancel tx 策略
   - txpool 或等价数据源支持
   - 外部替换交易识别
2. 细化风险统计与信号来源：
   - `pause_on_liquidity_drop` 引入真实 liquidity 数据源
   - `pause_on_honeypot_signal` 引入更稳健的税费 / honeypot 信号
   - 评估是否需要单独的 in-flight gas 预算前瞻
3. 收口主网推进文档与剩余 live 保护开关：
   - 测试网 / 主网只读 / 最小仓位验收清单
   - 主网最小仓位 / 单侧限制开关

### 已完成的本轮计划项

1. 已完成：启动时引入链上余额同步，替代 `live/paper` 路径下的模拟仓位启动。
2. 已完成：给报价与主循环加 symbol 级异常隔离，单个 symbol 的 quote / route 异常不会再直接打断整轮运行。
3. 已完成：扩展 `pending_txs` schema 与状态机，并把 `submitted / retryable / confirmed / failed` 接入当前执行路径。
4. 已完成：实现启动恢复与有限重试，启动时会扫描 `prepared / submitted / retryable`。
5. 已完成：增加 receipt / event 级成交核对，live fill 与恢复确认优先按 receipt 中的 ERC20 `Transfer` 日志回写。
6. 已完成：接线关键 live 风控字段，包括价格冲击、预期收益、预计 gas、连续失败熔断、单币失败上限、UTC 日内/小时窗口统计、`hard_stop_from_cost_bps`、`max_daily_gas_usd` 和 `pause_on_*` 第一版信号。
7. 已完成：补齐 nonce-aware 的 pending tx 恢复与 in-flight 管理第一版。
8. 已完成：更新 `README.md` 与 `pyproject.toml`，使仓库说明、Python 版本要求和 live 依赖声明与当前实现对齐。
9. 已完成：把 `max_daily_gas_usd` 从预计 gas 汇总切换到 receipt 实耗汇总。
10. 已完成：补齐 replacement gas bump 第一版，同 nonce 重试会按上一笔 gas price 自动提价。
11. 已完成：收口 live 配置模板，接线 `router_abi_path`、`max_notional_per_order`、`max_position_per_symbol_usd`，并新增 ABI 模板文件。
12. 当前实现口径：
   - `paper/live` 启动时优先读取链上 base / quote 余额
   - base 余额直接按 symbol 对应 token 回写
   - 多个 symbol 共用同一 quote token 时，按各自 `max_quote_per_symbol_usd` 比例分配共享 quote 余额
   - 单个 symbol 的非执行异常会被标记为 `ERROR`、写入 recent event，并继续处理其他 symbol
   - `pending_txs` 现在会在执行前标记为 `submitted`
   - 执行超时等可重试错误会标为 `retryable`
   - 成功成交会回写 `approve_tx_hash / swap_tx_hash / approve_nonce / swap_nonce / receipt_status / fill_*`
   - live 启动时和运行中每轮都会扫描 `prepared / submitted / retryable`
   - `retryable` 单据会在重试预算内继续执行，并尊重 `next_retry_at`
   - 带 `swap_nonce` 的单据会先查 receipt，再结合 wallet `latest/pending nonce` 判断 `confirmed / inflight / retryable / orphaned`
   - 仅有 approve 阶段的单据也会结合 allowance 与 nonce 判断“继续等待 / 可重试 / 需人工处理”
   - 同 nonce replacement 重试现在会读取上一次 `approve/swap_gas_price_wei`，并按 `execution.replacement_gas_bump_bps` 计算最小提价
   - `single_symbol_single_inflight` 与 `max_inflight_txs` 现在按 SQLite 未决单真实生效
   - live fill 与恢复确认会优先解析 receipt 中的 ERC20 `Transfer` 日志，余额差分作为 fallback
   - preview 结果已新增 `price_impact_bps / expected_profit_usd / estimated_gas_usd`
   - 实盘 preview 会在执行前检查价格冲击、预计 gas 与预期收益，超限则直接阻断新单
   - 全局已接 `max_consecutive_failed_tx`，单币已接 `max_failed_tx_per_symbol`
   - `daily_trade_count` 现在按 UTC 日内 `fills` 实时刷新，启动恢复和跨日运行都会自动归零
   - `max_trades_per_symbol_per_hour` 现在按最近 1 小时 `fills` 真实计数生效
   - `max_daily_realized_loss_usd` 现在按 UTC 日内 `fills.realized_pnl` 聚合生效
   - `max_failed_tx_per_symbol` 现在按 UTC 日内 `execution_failures` 计数生效
   - `max_daily_gas_usd` 现在按 UTC 日内 `execution_attempts.actual_gas_usd` 聚合生效，实耗会在成功、失败 receipt 和恢复确认时回写
   - `[router].router_abi_path` 现在按配置文件相对路径解析，并支持直接加载 ABI 数组或带 `abi` 字段的 JSON
   - 常规买卖单现已同时受 `risk.max_notional_per_order` 限制
   - 常规买入单现已按 preview 后的 projected position 校验 `risk.max_position_per_symbol_usd`
   - 未显式配置 `risk.max_position_per_symbol_usd` 时，会自动回退到旧字段 `inventory.max_base_exposure_usd`
   - `hard_stop_from_cost_bps` 现在会在 `exec_sell_price` 相对 `avg_cost_price` 跌破阈值时触发紧急卖出
   - `allow_emergency_sell_reserve = true` 时，hard stop 可卖出 reserve base；否则仅卖出可用仓位
   - `pause_on_route_failure` 与 `pause_on_liquidity_drop` 已按可分类的 quote / preview 失败接线
   - `pause_on_honeypot_signal` 已按执行错误关键字接成 sticky pause
   - `dry-run/paper` 在没有 router-backed preview 时不会因为 gas 不可估而误拦单

### 本轮补充审计得到的高优先级风险

1. 还没有 cancel tx 策略，遇到长时间卡住的 pending tx 时仍缺少主动撤单能力。
2. honeypot / liquidity / route pause 当前仍主要靠启发式信号，不是完整的数据面风控。
3. 失败事件当前只记录执行失败，不区分更细的错误类别，后续要和恢复状态机统一。
4. 没有 receipt 的 in-flight 交易在确认前不会计入实际 gas，是否需要单独的前瞻预算仍未定。

## 更新日志

### 2026-03-03 步骤 22：live 配置模板收口与仓位上限接线

本次完成：

1. `src/core/models.py` 与 `src/core/config.py` 已新增并接线：
   - `router.router_abi_path`
   - `risk.max_notional_per_order`
   - `risk.max_position_per_symbol_usd`
2. `src/core/config.py` 现在会把 `router_abi_path` 按配置文件目录解析为绝对路径。
3. `src/evm.py` 已支持从 ABI 文件加载 router 合约接口：
   - 兼容“纯 ABI 数组”
   - 兼容“带 `abi` 字段”的 JSON
4. 已新增 `config/abi/uniswap_v2_router.json`，作为 `UniswapV2 Router` 的最小可用 ABI 模板。
5. `src/modules/risk.py` 与 `src/app.py` 现在已把以下限制真正接到常规买卖单：
   - `max_notional_per_order`
   - `max_position_per_symbol_usd`
6. `max_position_per_symbol_usd` 未显式配置时，会回退到旧字段 `inventory.max_base_exposure_usd`，避免旧配置直接失效。
7. `config/live.toml` 已更新为当前可运行模板，不再把这三个字段保留为注释占位。
8. `README.md` 已同步去掉与当前实现不符的已关闭缺口描述。

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/risk.py src/core/models.py src/core/config.py`
   - 结果：成功
2. 执行 `env PYTHONPATH=src python3.11 -c "from pathlib import Path; from core.config import load_config; cfg = load_config(Path('config/live.toml')); print(cfg.router.router_abi_path); print(cfg.risk.max_notional_per_order); print(cfg.risk.max_position_per_symbol_usd)"`
   - 结果：成功，能正确解析出 ABI 绝对路径与风险上限值
3. 执行 `python3.11 -c "import json; from pathlib import Path; data = json.loads(Path('config/abi/uniswap_v2_router.json').read_text()); print(type(data).__name__, len(data))"`
   - 结果：成功，ABI 模板可被解析为长度为 `4` 的 JSON 数组

本次剩余缺口：

1. 还没有 cancel tx 策略。
2. 还没有 txpool 或等价数据源来识别外部替换交易。
3. 主网测试网/只读/最小仓位的推进清单仍待文档化。

### 2026-03-03 步骤 21：replacement gas bump 第一版

本次完成：

1. `src/core/models.py` 与 `src/core/config.py` 已新增 `execution.replacement_gas_bump_bps`，默认值为 `1250.0`。
2. `config/live.toml` 已新增：
   - `replacement_gas_bump_bps = 1250.0`
3. `src/evm.py` 已扩展发单结果：
   - `SubmittedTransaction` 现在会带回 `gas_price_wei`
   - `TransactionSendError` 现在也会带回失败交易对应的 `gas_price_wei`
   - `send_transaction(...)` 已支持 `min_gas_price_wei`
4. `src/modules/state_store.py` 已把 `approve/swap gas price` 接入持久化：
   - `pending_txs.approve_gas_price_wei / swap_gas_price_wei`
   - `execution_attempts.approve_gas_price_wei / swap_gas_price_wei`
5. `src/modules/execution.py` 的 pending tx 重试现在会：
   - 读取上一笔 `approve/swap_gas_price_wei`
   - 按 `replacement_gas_bump_bps` 计算最小提价
   - 用同一 nonce 做 replacement 重发，而不是平价重发
6. `src/app.py` 的恢复逻辑现在会区分：
   - pending 且到达重试窗口、仍有预算：允许进入 replacement 重试
   - pending 但预算耗尽：保持 inflight，不会误标成可重试

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/execution.py src/modules/state_store.py src/core/models.py src/core/config.py`
   - 结果：成功
2. 执行本地 stub 脚本验证：
   - `execution_attempts` 的 gas price / actual gas 列可正常写入
   - 上一笔 gas price 为 `100 / 200 wei` 时，replacement 最小 gas 会被提到 `113 / 225 wei`
   - pending 且达到重试窗口时，会放行进入 replacement；预算耗尽但仍 pending 时，会继续保持 inflight

本次剩余缺口：

1. 还没有 cancel tx 策略。
2. 还没有 txpool 或等价数据源来识别外部替换交易。
3. 主网测试网/只读/最小仓位的推进清单仍待文档化。

### 2026-03-03 步骤 20：`max_daily_gas_usd` 切换到 receipt 实耗统计

本次完成：

1. `src/modules/state_store.py` 已扩展 `execution_attempts`：
   - 新增 `approve_tx_hash / approve_nonce`
   - 新增 `swap_tx_hash / swap_nonce`
   - 新增 `actual_gas_usd`
2. `record_execution_attempt(...)` 现在会返回 attempt id，供执行成功、失败和恢复路径回写同一条 attempt。
3. `src/evm.py` 已新增 `get_transaction(...)` 和 `get_transaction_gas_cost_usd(...)`：
   - 优先用 receipt 的 `effectiveGasPrice`
   - fallback 到 transaction `gasPrice`
   - 统一按 quote token 价值换算 gas 实耗
4. `src/app.py` 已把以下路径接到 actual gas 回写：
   - live buy / sell 成功成交
   - 执行异常且已知 tx hash
   - 启动/运行中恢复到 confirmed / failed / approve-only failed 的路径
5. `src/app.py` 的全局风控 `max_daily_gas_usd` 现在改为按 UTC 日内 `execution_attempts.actual_gas_usd` 聚合，而不是 `estimated_gas_usd`。

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/state_store.py`
   - 结果：成功
2. 执行一段本地 stub 脚本验证：
   - `execution_attempts` 在只写估算值时不会被计入日内 gas
   - 回写 `actual_gas_usd` 后，`sum_execution_attempt_gas_since(...)` 与 `Application._global_daily_gas_usd()` 都按实耗返回

本次剩余缺口：

1. 没有 receipt 的 in-flight 交易在确认前不会计入实际 gas，是否需要额外的“前瞻预算”仍待决定。
2. 还没有 replacement / cancel tx 的主动提价重发策略。
3. 主网测试网/只读/最小仓位的推进清单仍待文档化。

### 2026-03-03 步骤 19：README 与依赖声明对齐

本次完成：

1. `README.md` 已从“dry-run only”更新为当前真实状态：
   - 明确 `dry-run / paper / live` 三种模式
   - 明确当前 live 范围、已接能力和主网前剩余缺口
   - 补充 `config/live.toml` 用法与 `.[live]` 安装方式
2. `pyproject.toml` 已更新：
   - `requires-python` 从 `>=3.14` 调整为与 `tomllib` 使用一致的 `>=3.11`
   - 新增可选依赖组 `live = ["web3>=7,<8"]`
   - 项目描述改为与当前 dry-run / paper / live 能力一致
3. 本进度文档已同步把“文档与依赖声明漂移”从当前优先级中移除，后续优先处理风险统计和更强的 pending tx 管理。

本次验证：

1. 手工复核 `README.md`、`pyproject.toml` 与当前代码：
   - `src/cli.py` 已支持 `--mode dry-run|paper|live`
   - `src/core/config.py` 依赖 `tomllib`
   - `src/evm.py` 对 `web3` 保持可选导入
2. 结论：
   - 仓库说明与依赖声明已和当前实现口径基本一致

本次剩余缺口：

1. `max_daily_gas_usd` 仍按预计 gas 统计，不是按 receipt 实耗。
2. 还没有 replacement / cancel tx 的主动提价重发策略。
3. 主网测试网/只读/最小仓位的推进清单仍待文档化。

### 2026-03-03 步骤 18：nonce-aware pending tx 恢复与 in-flight 管理第一版

本次完成：

1. `src/evm.py` 已新增 nonce 相关能力：
   - `SubmittedTransaction`
   - `get_allowance_raw(...)`
   - `get_wallet_transaction_count(...)`
   - `get_wallet_nonce_state(...)`
   - `send_transaction(...)` 改为默认按 wallet `pending nonce` 发单，并把实际 nonce 带回执行层
2. `src/modules/execution.py` 已把 approve / swap 的 nonce 接到执行返回和异常路径：
   - `ExecutionFailure` 现在会携带 `approve_nonce / swap_nonce`
   - live buy / sell / retry 路径会在成功或失败时保留 nonce 元数据
3. `src/modules/state_store.py` 已扩展 `pending_txs`：
   - 新增 `approve_nonce / swap_nonce`
   - 新增 `mark_pending_tx_inflight(...)`
   - 新增 `count_open_pending_txs(...)`
4. `src/app.py` 已把 pending tx 恢复改成 nonce-aware：
   - 带 `swap_tx_hash` 的单据会先查 receipt
   - receipt 缺失时，会结合 wallet `latest/pending nonce` 判断 tx 仍在链上、nonce 已释放可安全重试，还是 nonce 已被消费需人工处理
   - 只有 approve 阶段的单据，会再结合 allowance 与 approve nonce 判断恢复动作
5. `src/app.py` 现在不只在启动时恢复 pending tx，而是 live 模式下每轮主循环都会扫描未决单。
6. `src/app.py` 已接入真实 in-flight 限流：
   - `single_symbol_single_inflight`
   - `max_inflight_txs`
   - 未决 `orphaned` 单据也会继续阻断新单，直到人工处理

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/execution.py src/modules/state_store.py src/core/models.py`
   - 结果：成功
2. 执行一段本地 stub 脚本验证：
   - swap nonce 仍在链上 pending 时，pending tx 会保持 `submitted`，不会盲目重发
   - swap nonce 已释放时，`retryable` 单据会重试并落到 `confirmed`
   - `single_symbol_single_inflight` 会按 SQLite 未决单计数阻断新单

本次剩余缺口：

1. 还没有 replacement / cancel tx 的主动提价重发策略。
2. 还没有 txpool 或等价数据源来识别外部替换交易。
3. `max_daily_gas_usd` 仍按预计 gas 统计，不是按 receipt 实耗。
4. `README.md` 与 `pyproject.toml` 已在步骤 19 对齐，本条缺口已关闭。

### 2026-03-03 步骤 17：剩余 live 风控字段第一版接线

本次完成：

1. `src/modules/state_store.py` 已新增：
   - `execution_attempts` 表
   - `record_execution_attempt(...)`
   - `sum_execution_attempt_gas_since(...)`
2. `pending_txs` 已新增 `estimated_gas_usd` 落库字段，启动重试也能继续沿用 gas 估算值。
3. `src/app.py` 已把 `max_daily_gas_usd` 接入全局风控：
   - 发单前会记录一次 `execution_attempts`
   - 全局风控按 UTC 日内累计 `estimated_gas_usd` 停止新单
4. `src/app.py` 已新增 `hard_stop_from_cost_bps` 紧急卖出路径：
   - 当 `exec_sell_price` 相对 `avg_cost_price` 跌破阈值时，会优先触发紧急卖出
   - `allow_emergency_sell_reserve = true` 时可卖出 reserve base
5. `src/modules/quote.py` 与 `src/modules/execution.py` 已新增可分类的 `QuoteSignalError`：
   - `route_failure`
   - `liquidity_drop`
6. `src/app.py` 已把以下暂停信号接入：
   - `pause_on_route_failure`
   - `pause_on_liquidity_drop`
   - `pause_on_honeypot_signal`
7. 当前口径：
   - route / liquidity pause 是可恢复的瞬时暂停
   - honeypot pause 是 sticky pause，当前需要重启或后续人工清理策略状态

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/modules/state_store.py src/modules/risk.py src/modules/execution.py src/modules/quote.py src/core/models.py`
   - 结果：成功
2. 执行一段本地 stub 脚本验证：
   - `max_daily_gas_usd` 超限会返回 `HALTED`
   - hard stop 会触发紧急卖出并清空 base
   - `route_failure` 会把 symbol 标成 `PAUSED`
   - honeypot 关键字错误会触发 sticky pause
3. 说明：
   - `max_daily_gas_usd` 当前按预计 gas 累加，不是按 receipt 实际 gas 消耗
   - honeypot / liquidity / route 仍是第一版启发式接线

本次剩余缺口：

1. pending tx 恢复仍不是 nonce-aware 状态机。
2. `max_daily_gas_usd` 仍应升级为基于 receipt 的实际 gas 统计。
3. `pause_on_liquidity_drop` 和 `pause_on_honeypot_signal` 仍缺少更稳健的链上数据源。
4. `README.md` 与 `pyproject.toml` 仍未和当前实现状态对齐。

### 2026-03-03 步骤 16：交易频控与 daily 语义改为窗口统计

本次完成：

1. `src/modules/state_store.py` 已新增 `execution_failures` 表，并增加窗口查询接口：
   - `count_fills_for_symbol_since(...)`
   - `count_execution_failures_for_symbol_since(...)`
   - `sum_realized_pnl_since(...)`
2. `src/app.py` 已新增基于 SQLite 的窗口刷新逻辑：
   - `daily_trade_count` 现在按 UTC 日内 `fills` 计数实时刷新
   - `max_trades_per_symbol_per_hour` 现在按最近 1 小时 `fills` 计数生效
   - `max_daily_realized_loss_usd` 现在按 UTC 日内 `fills.realized_pnl` 聚合生效
   - `max_failed_tx_per_symbol` 现在按 UTC 日内 `execution_failures` 计数生效
3. `src/modules/risk.py` 已移除原先基于 `buy_levels[-1].level * 200` 的硬编码近似，改为真正使用 `risk.max_trades_per_symbol_per_hour`。
4. 执行错误落库时，`src/app.py` 现在会同步写入 `execution_failures`，供后续窗口风控和恢复分析复用。

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/modules/state_store.py src/modules/risk.py src/core/models.py`
   - 结果：成功
2. 执行一段本地脚本向 SQLite 手工写入：
   - 昨日 fill / 今日 fill
   - 昨日 failure / 今日 failure
3. 结果：
   - `daily_trade_count` 只统计今日 fill
   - `max_daily_realized_loss_usd` 只统计今日 realized pnl
   - `max_failed_tx_per_symbol` 只统计今日 failure
   - `max_trades_per_symbol_per_hour` 已能拦截最近 1 小时交易数超限

本次剩余缺口：

1. `hard_stop_from_cost_bps`、`max_daily_gas_usd`、`pause_on_*` 已在步骤 17 收口。
2. pending tx 恢复仍不是 nonce-aware 状态机。

### 2026-03-03 步骤 15：关键 live 风控字段第一版接线

本次完成：

1. `src/core/models.py` 的 `ExecutionPreview` 已新增：
   - `price_impact_bps`
   - `expected_profit_usd`
   - `estimated_gas_usd`
2. `src/modules/execution.py` 的 preview 路径已开始回填上述风险元数据：
   - `dry-run/paper` 会计算价格冲击与预期收益
   - `live` 会在 router preview 基础上进一步估算 approve + swap 的预期 gas 成本
3. `src/evm.py` 已新增 gas 估算辅助：
   - `estimate_approve_gas_cost_usd(...)`
   - `estimate_swap_gas_cost_usd(...)`
   - `estimate_swap_bundle_gas_cost_usd(...)`
4. `src/modules/risk.py` 已新增 preview 与运行时风控判断：
   - `max_price_impact_bps`
   - `max_gas_usd_per_tx`
   - `min_expected_profit_usd`
   - `max_daily_realized_loss_usd`
   - `max_consecutive_failed_tx`
   - `max_failed_tx_per_symbol`
5. `src/app.py` 已接入这些判断：
   - 预览阶段 price impact / gas / expected profit 超限会直接阻断新单
   - 全局连续失败或累计 realized loss 超限会停止新单
   - 单币失败次数超限会暂停该币新单
6. `dry-run/paper` 在没有 router-backed preview 时，不会因为 gas 不可估而被误判为风控失败。

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/execution.py src/modules/risk.py src/core/models.py`
   - 结果：成功
2. 执行一段本地脚本构造最小 `dry-run` `Application`
   - 结果：无 router gas 估算时，`dry-run` buy 仍可正常成交
   - 结果：`max_failed_tx_per_symbol` 不会在一次成功后被错误清零
3. 执行一段本地脚本直接验证 `RiskManager`
   - 结果：price impact / gas / expected profit / 全局失败 / 单币失败门槛均按预期触发
4. 说明：
   - 当前终端里的 `python3` 仍是 `3.10.14`
   - 需要手写 dataclass 脚本做验证，不能直接依赖 `tomllib`

本次剩余缺口：

1. `max_daily_realized_loss_usd`、`max_failed_tx_per_symbol`、`max_trades_per_symbol_per_hour` 已在步骤 16 收口。
2. `hard_stop_from_cost_bps`、`max_daily_gas_usd`、`pause_on_*` 已在步骤 17 收口。

### 2026-03-03 步骤 14：receipt / event 级成交核对

本次完成：

1. `src/evm.py` 已新增：
   - `get_transaction_receipt(...)`
   - `get_erc20_transfer_deltas_raw(...)`
   - `TRANSFER_EVENT_TOPIC0`
2. `src/modules/execution.py` 的以下路径已优先按 receipt 中的 ERC20 `Transfer` 日志回推真实成交：
   - `execute_buy(...)`
   - `execute_sell(...)`
   - `retry_pending_tx(...)`
   - `confirm_pending_tx(...)`
3. 当 receipt log 无法稳定解析时，执行层仍会回落到余额差分，再不行才回落到 preview 估算，避免把这次接线做成单点故障。

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/execution.py src/modules/state_store.py src/core/models.py`
   - 结果：成功
2. 执行一段本地 stub 脚本验证 buy / sell 的 receipt transfer 解析
   - 结果：buy / sell 两个方向都能从 receipt log 反推出实际 `base_qty / quote_value / price`
3. 执行一段本地 stub 脚本验证 `confirm_pending_tx(...)`
   - 结果：启动恢复确认路径会优先使用 receipt 推导的成交结果，而不是 preview fallback

本次剩余缺口：

1. 当前仍主要依赖钱包地址相关的 ERC20 `Transfer` 日志，复杂聚合器路径和更隐蔽的 token 行为还没有专项适配。
2. nonce-aware 恢复与 in-flight 管理不在本步骤范围内，仍待后续补齐。

### 2026-03-03 步骤 13：pending tx 启动恢复与有限重试

本次完成：

1. `src/modules/state_store.py` 已新增 `load_pending_txs(...)`，供启动恢复扫描 open pending tx。
2. `src/evm.py` 已新增 `get_transaction_receipt_status(...)`，供恢复阶段先查链上 receipt。
3. `src/modules/execution.py` 已新增恢复辅助路径：
   - `retry_pending_tx(...)`
   - `confirm_pending_tx(...)`
4. `src/app.py` 启动时在 `live` 模式下会自动扫描 `prepared / submitted / retryable`：
   - `retryable` 且仍在预算内的单据会重试
   - 带 `swap_tx_hash` 的单据会先查 receipt
   - 缺少必要上下文或恢复失败的单据会标记为 `orphaned`
5. 单条 pending tx 的恢复失败现在不会打断整个应用初始化。

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/execution.py src/modules/state_store.py src/core/models.py`
   - 结果：成功
2. 执行一段本地脚本预写入 `retryable` pending tx 后再初始化 `Application`
   - 结果：pending tx 在启动恢复后变为 `confirmed`
   - 结果：`attempt_count = 2`
   - 结果：`tx_hash / approve_tx_hash / swap_tx_hash / receipt_status` 已正确回写

本次剩余缺口：

1. 还没有 receipt / event 级成交核对。
2. 当前恢复仍不是 nonce-aware 状态机。
3. `submitted` 但缺失 `swap_tx_hash` 的单据目前会保守转成 `orphaned`。
4. 可重试错误仍按字符串规则分类，后续要改成更稳健的错误类别。

### 2026-03-03 步骤 12：pending tx 状态机扩容

本次完成：

1. `src/modules/state_store.py` 已扩展 `pending_txs` schema，新增：
   - `attempt_count`
   - `submitted_at`
   - `confirmed_at`
   - `next_retry_at`
   - `approve_tx_hash`
   - `swap_tx_hash`
   - `receipt_status`
   - `fill_price / fill_base_qty / fill_quote_value`
2. `StateStore` 已新增状态转移接口：
   - `mark_pending_tx_submitted(...)`
   - `mark_pending_tx_retryable(...)`
   - `mark_pending_tx_orphaned(...)`
3. `mark_pending_tx_confirmed(...)` 已升级为回写成交明细，而不是只写一个 `tx_hash`。
4. `src/modules/execution.py` 已新增 `ExecutionFailure`，把 `approve_tx_hash / swap_tx_hash` 向上抛给应用层。
5. `src/evm.py` 已新增 `TransactionSendError`，在等待 receipt 失败或回执 revert 时尽量保留 `tx_hash`。
6. `src/app.py` 已在当前执行路径接入：
   - preview 成功后创建 `pending_tx`
   - execute 前标记 `submitted`
   - 成功后标记 `confirmed`
   - 可重试错误标记 `retryable`
   - 其他错误标记 `failed`

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/execution.py src/modules/state_store.py src/core/models.py`
   - 结果：成功
2. 使用临时 SQLite 数据库直接验证状态流：
   - `prepared -> submitted -> retryable`
   - `prepared -> submitted -> confirmed`
   - 结果：成功
3. 执行一段本地脚本模拟 `ExecutionFailure('Failed waiting for receipt: timeout')`
   - 结果：`pending_txs.status = 'retryable'`
   - 结果：`attempt_count = 1`
   - 结果：`approve_tx_hash / swap_tx_hash / next_retry_at` 已落库

本次剩余缺口：

1. `pending_txs` 还没有启动恢复与自动重试执行逻辑。
2. 还没有 receipt / event 级成交核对。
3. 可重试错误目前仍按字符串规则分类，后续要和恢复状态机统一。
4. nonce 管理仍未独立化。

### 2026-03-03 步骤 11：主循环 symbol 级异常隔离

本次完成：

1. `src/app.py` 已将单个 symbol 的主循环处理抽成 `_process_symbol(...)`。
2. 外层循环现在会对每个 symbol 单独做异常捕获。
3. 对于 quote / route / grid 等非执行阶段异常：
   - 当前 symbol 状态会标记为 `ERROR`
   - reporter 会写入 `symbol loop failed: ...`
   - 状态会同步写回 SQLite
4. 现有 buy / sell 执行错误处理保持不变，`pending_txs` 的失败落库逻辑没有被破坏。

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/execution.py src/modules/quote.py`
   - 结果：成功
2. 执行一段本地脚本模拟：
   - symbol `A` 的 quote 抛出 `quote boom`
   - symbol `B` 正常返回报价
3. 结果：
   - `A.status = ERROR`
   - `B.status = IDLE`
   - recent events 出现 `A symbol loop failed: quote boom`

本次剩余缺口：

1. `pending_txs` 还没有自动重试与恢复。
2. 还没有 receipt / event 级成交核对。
3. 共享 quote 余额目前只在启动时做静态分配，还没有运行时再平衡逻辑。
4. 运行时链上异常虽然不会打断整轮，但还没有按错误类型做 pause / retry / degrade 分流。

### 2026-03-03 步骤 10：paper/live 启动链上余额同步

本次完成：

1. `src/app.py` 启动路径已新增链上余额同步：
   - `paper/live` 模式会在 `_bootstrap_states()` 前半段读取链上 base / quote 余额
   - 再用链上余额覆盖本地 state 中的 `base_balance / quote_balance_usd`
2. `src/evm.py` 的只读客户端现在也会加载并校验 `chain.wallet_address`。
3. 只读客户端已支持在不持有私钥的前提下查询钱包 ERC20 余额，供 `paper` 模式启动同步使用。
4. 多个 symbol 共用同一 quote token 时，启动同步会按 `max_quote_per_symbol_usd` 比例拆分共享 quote 余额，避免把同一钱包余额重复记到每个 symbol。
5. `dry-run` 路径未改动，仍继续使用本地模拟初始仓位。

本次验证：

1. 执行 `python3 -m py_compile src/app.py src/evm.py src/modules/execution.py src/modules/quote.py`
   - 结果：成功
2. 执行一段本地伪客户端脚本验证 `paper` 启动同步逻辑
   - 结果：`A.base_balance = 5.0`
   - 结果：`B.base_balance = 2.0`
   - 结果：共享 `quote` 余额 `50.0` 被分配为 `20.0 / 30.0`
3. 说明：
   - 本次未做真实 RPC / `web3.py` 环境下的集成验证
   - 当前终端里的 `python3` 仍是 `3.10.14`

本次剩余缺口：

1. 主循环仍未做 symbol 级异常隔离。
2. `pending_txs` 还没有自动重试与恢复。
3. 还没有 receipt / event 级成交核对。
4. 共享 quote 余额目前只在启动时做静态分配，还没有运行时再平衡逻辑。

### 2026-03-03 步骤 9：补充审计与实施计划确认

本次完成：

1. 对当前 `live/paper` 路径补做了一轮严格代码审查。
2. 新确认两项比原计划更靠前的缺口：
   - 启动时缺少链上余额同步
   - 主循环缺少 symbol 级异常隔离
3. 已把下一阶段实施顺序扩展为 8 项正式计划，用于指导后续开发推进。
4. 已把本文件升级为“任务 / 计划 / 完成”三类事件都必须同步更新的工作基线。

本次验证：

1. 通过代码审读重新核对：
   - `src/app.py`
   - `src/modules/execution.py`
   - `src/modules/state_store.py`
   - `src/evm.py`
   - `src/modules/risk.py`
2. 当前本地 `python3 --version`
   - 结果：`3.10.14`
3. 说明：
   - 本次是文档与计划更新，不是代码实现
   - 当前环境仍低于仓库声明的 `Python >= 3.14`

本次剩余缺口：

1. `pending_txs` 还没有自动重试与恢复。
2. 还没有 receipt / event 级成交核对。
3. `live/paper` 启动仍未从链上同步真实余额。
4. 主循环仍未加 symbol 级异常隔离。

### 2026-03-02 基线审计

本次结论：

1. 仓库已有链上 live 模块雏形，但还没有形成可运行闭环。
2. 当前最优先事项不是策略调参，而是补齐执行层接线、模式切换、持久化和 kill switch。
3. 当前版本不能直接进入主网实盘阶段。

本次审计依据：

1. `src/modules/quote.py`
2. `src/modules/execution.py`
3. `src/evm.py`
4. `src/app.py`
5. `src/cli.py`
6. `src/core/config.py`
7. `src/core/models.py`
8. `config/strategy.example.toml`
9. `README.md`

本次运行验证：

1. 执行 `python3 main.py --iterations 1 --no-sleep`
2. 结果：失败
3. 原因：`src/app.py` 试图实例化 `ExecutionEngine()` protocol

### 2026-03-02 步骤 1：入口接线与模式切换

本次完成：

1. `src/app.py` 已改为通过 builder 接入 quote / execution engine。
2. `src/cli.py` 已新增 `--mode dry-run|paper|live`。
3. `paper` 模式已定义为“真实 quote + dry-run execution”。
4. `src/evm.py` 已支持只读 Router client，供 `paper` 模式链上询价使用。

本次验证：

1. 执行 `python3 main.py --iterations 1 --no-sleep`
   - 结果：成功
2. 执行 `python3 main.py --iterations 1 --no-sleep --mode dry-run`
   - 结果：成功
3. 执行 `python3 main.py --help`
   - 结果：已显示 `--mode {dry-run,paper,live}`

本次剩余缺口：

1. 还没有 `config/live.toml` 模板。
2. 还没有持久化层。
3. 还没有 kill switch。
4. 还没有 `ExecutionEngine` 的 preview / execute 拆分接口。

### 2026-03-02 步骤 2：live 配置模板

本次完成：

1. 已新增 `config/live.toml`。
2. 模板默认保持在安全状态：
   - `mode = "paper"`
   - `allow_live = false`
   - `allow_mainnet = false`
3. 已补齐当前代码已支持的 live / paper 关键字段：
   - `wallet_address`
   - `private_key_env`
   - `[router]`
   - symbol route 的 `base_token_address / buy_path / sell_path`
4. 尚未接线的字段已作为注释占位保留：
   - `kill_switch_file`
   - `router_abi_path`
   - `max_notional_per_order`
   - `max_position_per_symbol_usd`

本次验证：

1. 执行 `python3 -c "... load_config(Path('config/live.toml')) ..."`
   - 结果：成功
2. 执行 `python3 main.py --config config/live.toml --mode dry-run --iterations 1 --no-sleep`
   - 结果：成功

本次剩余缺口：

1. kill switch 仍未接线。
2. SQLite 持久化仍未实现。
3. `ExecutionEngine` 仍未拆成 preview / execute 接口。
4. `config/live.toml` 中的 placeholder 地址仍需要在实际 paper / live 前替换。

### 2026-03-02 步骤 3：ExecutionEngine 接口重构

本次完成：

1. `src/core/models.py` 已新增 `ExecutionPreview`。
2. `src/modules/execution.py` 已拆成统一接口：
   - `preview_buy`
   - `preview_sell`
   - `execute_buy`
   - `execute_sell`
3. `DryRunExecutionEngine` 和 `LiveExecutionEngine` 都已适配新接口。
4. `src/app.py` 已改成“先 preview，再 execute”的调用方式。

本次验证：

1. 执行 `python3 main.py --iterations 1 --no-sleep`
   - 结果：成功
2. 执行 `python3 main.py --config config/live.toml --mode dry-run --iterations 1 --no-sleep`
   - 结果：成功

本次剩余缺口：

1. preview 结果还没有持久化到 pending tx。
2. SQLite 持久化仍未实现。
3. kill switch 仍未接线。
4. live fill 仍按预期输出估算，不是按 receipt/event 实际成交回写。

### 2026-03-02 步骤 4：SQLite 持久化

本次完成：

1. 已新增 `src/modules/state_store.py`。
2. 已落地 SQLite 表：
   - `fills`
   - `positions`
   - `pending_txs`
   - `realized_pnl`
3. `src/app.py` 已接入持久化：
   - 启动时同步初始 position
   - 每轮更新后同步 positions / realized pnl
   - fill 完成后写入 `fills`
   - live preview 时创建 `pending_txs` 记录，并在成交后标记为 `confirmed`

本次验证：

1. 执行 `python3 main.py --iterations 1 --no-sleep`
   - 结果：成功
2. 查询 `data/alpha_grid_state.db`
   - 结果：已存在 `fills / positions / pending_txs / realized_pnl` 表
   - 结果：`positions = 2`，`realized_pnl = 2`
3. 执行 `python3 main.py --config config/live.toml --mode dry-run --iterations 1 --no-sleep`
   - 结果：成功
4. 查询 `data/alpha_grid_live.db`
   - 结果：已存在同样的 SQLite 表
   - 结果：`positions = 1`，`realized_pnl = 1`

本次剩余缺口：

1. 还没有从 SQLite 恢复状态。
2. pending tx 还没有失败/重试/恢复生命周期。
3. kill switch 仍未接线。
4. live fill 仍按预期输出估算，不是按 receipt/event 实际成交回写。

### 2026-03-02 步骤 5：kill switch

本次完成：

1. 已在 `RiskConfig` 中新增 `kill_switch_file`。
2. `config/strategy.example.toml` 和 `config/live.toml` 已补上 `kill_switch_file = "run/alpha-grid.stop"`。
3. `src/app.py` 已在每轮主循环检测 kill switch。
4. 文件存在时，程序会停止新单并把 symbol 状态标记为 `HALTED`。

本次验证：

1. 执行 `python3 main.py --iterations 1 --no-sleep`
   - 结果：成功，symbol 状态维持正常
2. 创建 `run/alpha-grid.stop` 后执行一轮 dry-run
   - 结果：symbol 状态变为 `HALTED`
   - 结果：Recent events 出现 `SYSTEM kill switch active: run/alpha-grid.stop`
3. 执行 `python3 main.py --config config/live.toml --mode dry-run --iterations 1 --no-sleep`
   - 结果：成功

本次剩余缺口：

1. 还没有从 SQLite 恢复状态。
2. pending tx 还没有失败/重试/恢复生命周期。
3. live fill 仍按预期输出估算，不是按 receipt/event 实际成交回写。

### 2026-03-02 步骤 6：从 SQLite 恢复状态

本次完成：

1. `StateStore` 已新增 `load_position(symbol)`。
2. `Application._bootstrap_states()` 启动时会优先读取 SQLite 中已存在的 position。
3. 如果找到持久化记录，应用会用 SQLite 中的：
   - `base_balance`
   - `quote_balance_usd`
   - `avg_cost_price`
   - `realized_pnl`
   - `unrealized_pnl`
   - `daily_trade_count`
   - `buy_done_count`
   - `sell_done_count`
   - `reference_price`
   - `last_mid_price`
   来恢复 symbol 状态。

本次验证：

1. 执行 `python3 main.py --iterations 1 --no-sleep`
   - 结果：成功
2. 执行 `python3 main.py --config config/live.toml --mode dry-run --iterations 1 --no-sleep`
   - 结果：成功
3. 使用临时 SQLite 数据库写入自定义 position 后再次创建 `Application`
   - 结果：`base_balance = 7.5`
   - 结果：`quote_balance_usd = 55.0`
   - 结果：`realized_pnl = 12.3`
   - 说明：应用已从 SQLite 读取并恢复这些值

本次剩余缺口：

1. pending tx 还没有失败/重试/恢复生命周期。
2. live fill 仍按预期输出估算，不是按 receipt/event 实际成交回写。
3. 恢复的仍是 position 级状态，不包括 recent mid price 队列等完整运行上下文。

### 2026-03-02 步骤 7：pending tx 失败落库

本次完成：

1. `pending_txs` 已补充 `last_error` 字段。
2. `StateStore` 已新增 `mark_pending_tx_failed(...)`。
3. `src/app.py` 已为 buy / sell 执行路径增加异常捕获。
4. 单个 symbol 的执行异常现在会：
   - 将 position 状态标记为 `ERROR`
   - 写入 reporter event
   - 把对应 `pending_txs` 行标记为 `failed`

本次验证：

1. 执行 `python3 main.py --iterations 1 --no-sleep`
   - 结果：成功
2. 使用临时 SQLite 数据库创建一条 pending tx 并模拟执行失败
   - 结果：`pending_txs.status = 'failed'`
   - 结果：`pending_txs.last_error = 'boom'`
3. 对已有 `data/alpha_grid_state.db` 执行 schema 迁移检查
   - 结果：`pending_txs.last_error` 列已存在

本次剩余缺口：

1. pending tx 还没有自动重试与恢复。
2. live fill 还没有 receipt/event 级核对。
3. position 恢复仍是最小集，不是完整运行上下文恢复。

### 2026-03-02 步骤 8：live 成交按余额差分回写

本次完成：

1. `src/evm.py` 已为 ERC20 ABI 补充 `balanceOf`。
2. `EvmRouterClient` 已新增 `get_token_balance_raw(...)`。
3. `LiveExecutionEngine.execute_buy / execute_sell` 现在会在 swap 前后读取钱包 token 余额。
4. live fill 将优先按钱包余额差分计算：
   - 实际买入 base 数量
   - 实际卖出 base 数量
   - 实际消耗/收到的 quote 数量
   - 实际成交均价
5. 如果余额差分不可用，仍会退回到 preview 值兜底。

本次验证：

1. 执行 `python3 main.py --iterations 1 --no-sleep`
   - 结果：成功
2. 执行 `python3 main.py --config config/live.toml --mode dry-run --iterations 1 --no-sleep`
   - 结果：成功
3. 执行 `python3 -c \"... import LiveExecutionEngine ...\"`
   - 结果：成功
4. 说明：
   - 本地环境未安装 `web3.py`
   - 本次没有做真实链上 live 成交验证

本次剩余缺口：

1. 还没有 receipt/event 级成交核对。
2. pending tx 还没有自动重试与恢复。
3. live 路径仍缺少真实链路集成测试。

# BSC 链上实盘接入需求与进度

## 文档目的

这份文档是当前仓库接入 `BSC 链上实盘` 的唯一进度基线。

这里的 `实盘` 明确定义为：

1. 接 `BSC/BNB Chain` 链上报价与交易执行。
2. 通过链上 Router / 聚合器报价、授权、签名、广播、回执完成交易。
3. 不接 `Binance Spot API`。

后续每次完成一个阶段性改动，都要同步更新本文件：

1. 修改“最近更新时间”。
2. 更新“状态矩阵”。
3. 在“更新日志”追加一条记录。

最近更新时间：`2026-03-02`

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
3. 仍缺少持久化、pending tx 管理、kill switch，以及 fully-wired 的 live 风控字段。

### 状态矩阵

| 事项 | 状态 | 当前情况 | 主要缺口 |
| --- | --- | --- | --- |
| 真实 quote / 模拟 quote 双实现 | 已完成 | `src/modules/quote.py` 已通过 builder 接入；`paper/live` 使用真实 quote，`dry-run` 使用模拟 quote | live quote 仍只有基础 `getAmountsOut`，后续还要补独立预览接口与更细致的价格保护 |
| 真实执行层 approve/swap/sign/send/receipt | 部分完成 | `src/modules/execution.py` 和 `src/evm.py` 已实现 allowance、approve、swap、签名、广播、wait receipt，并已拆成 preview / execute 两阶段接口；live fill 已优先按钱包余额差分回写 | 还没有 receipt/event 级解析与核对；pending tx 还没有完整恢复与重试状态机 |
| 单一路由源接入 | 部分完成 | `src/evm.py` 已按通用 `UniswapV2 Router` 方式接了单一路由器 | 缺少 router ABI 文件化配置、路由健康检查、价格冲击保护和失败重试闭环 |
| live 配置模板 | 部分完成 | 已新增 `config/live.toml`，默认安全模式为 `paper + allow_live=false + allow_mainnet=false`，并补了 router / route 示例与 `kill_switch_file` | `router_abi_path`、仓位上限等字段仍只是注释占位，尚未接入代码 |
| CLI 模式切换 `dry-run/paper/live` | 已完成 | `src/cli.py` 已支持 `--mode dry-run|paper|live` 覆盖配置模式 | `paper` 仍依赖一份可用的链上配置才能实际跑通 |
| ExecutionEngine 统一接口 | 已完成 | `ExecutionEngine` 已拆成 `preview_buy / preview_sell / execute_buy / execute_sell`，`app` 已切到新的调用路径，preview 结果也已可写入 `pending_txs` | 还没有和重试/恢复状态机进一步联动 |
| SQLite 持久化 | 部分完成 | 已新增 SQLite `StateStore`，并落地 `fills / positions / pending_txs / realized_pnl` 表；主循环会同步 positions / realized pnl，fill 完成后会写 fills，live preview 会写 pending_txs；启动时会从 SQLite 恢复 position | pending tx 已支持 `prepared / confirmed / failed`，但还没有完整重试与恢复逻辑 |
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

1. `config/live.toml` 已新增，但 `router_abi_path` 和仓位上限等字段仍未接线。
2. SQLite 已能写入和恢复 position，pending tx 也已能记录失败，但还没有自动重试与恢复。
3. 还没有完整的 pending tx 生命周期管理。
4. 还没有 receipt/event 级解析与成交核对。

#### 3. 即使修好入口，离“可控实盘”仍差的部分

1. `pending tx` 已有最小持久化，但程序重启后还没有自动恢复与续跑逻辑。
2. 没有 nonce 管理器，当前只依赖 `get_transaction_count(...)`。
3. 还没有按 receipt / event log 做成交核对，目前是余额差分优先。
4. 没有主网最小仓位、单币、单侧的显式保护开关。
5. `paper` 模式虽然已打通入口，但仍需要一份完整的链上配置才能实际验链。

## 六项任务的当前判定

### 1. `config/live.toml` 模板

状态：`部分完成`

说明：

1. 已新增 `config/live.toml`，默认以 `paper` 模式启动，避免直接进入可写主网模式。
2. 模板已补齐当前代码已支持的链上字段：
   - `[router]`
   - `allow_live`
   - `allow_mainnet`
   - `wallet_address`
   - `private_key_env`
   - symbol route 的 `buy_path / sell_path`
3. `kill_switch_file` 已接入代码。
4. `router_abi_path`、更细的仓位上限仍未接入代码，目前仍保留为注释占位。

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
6. 当前还没有：
   - pending tx 的重试/恢复逻辑

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

## 当前建议的实际开发顺序

下一阶段应该按下面顺序推进，而不是先改策略核：

1. 完整化 pending tx 生命周期
   - 增加重试、恢复与清理逻辑
2. 增加 receipt/event 级成交核对
   - 用链上回执进一步校验余额差分结果

## 更新日志

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

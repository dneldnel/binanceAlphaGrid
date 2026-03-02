# Binance Alpha 双边做市/网格策略规范

## 1. 目的

把 `紫鹊界大王` 在帖子里提到的思路，整理成一份可直接落地编码的策略规范。该规范的目标不是复刻对方的私有实现细节，而是把已经确认的信息和必要的工程化补全，整理成一套能在 BSC 链上运行的多标的双边做市机器人。

## 2. 帖子中可以确认的信息

以下内容来自讨论帖和截图，可信度最高：

1. 交易场景是 `Binance Alpha` 上已经上线并稳定一段时间的中文 meme 币。
2. 不依赖 Binance Alpha 官方 API，而是 `通过链上执行交易`。
3. 策略核心是 `普通双边套利/双边做市`，不是复杂方向策略。
4. 策略偏高频、多币并发，截图中有十多个币同时运行。
5. 截图中出现了 `多单(未/完)`、`空单(未/完)`、`多基准`、`空基准`、`EMA`、`余额` 等字段。
6. 日志里出现了 `策略参数调整`、`首通买出/买入`、`空单平台：保留3代币` 等信息。
7. 盈利统计分成 `已实现收益` 和 `保留代币盈利` 两部分。

## 3. 工程化假设

以下内容不是帖子原文直接给出的，而是为了可实现性做的合理补全：

1. 所谓“多单/空单”在链上现货环境里，不等于合约多空，而是 `买入腿` 和 `卖出腿`。
2. 所谓“双边套利”本质更接近 `双边报价 + 库存管理 + 震荡吃差价`，不是严格意义的无风险套利。
3. 由于 AMM/Dex 场景通常没有中心化交易所那种真正挂在订单簿上的限价单，所以这里的“挂单”应实现为 `虚拟网格单`：
   - 机器人本地维护目标买卖价。
   - 当链上可执行价格触发阈值时，再发起 swap。
4. `EMA` 更适合作为参考价格或节奏过滤器，而不是单独决定方向。
5. “保留3代币”意味着策略不一定把仓位全平，而是保留极小底仓，避免反复清零带来的重新建仓摩擦。

## 4. 策略目标

1. 在 BSC 上对多个 Alpha meme 币做链上双边做市。
2. 在高波动、强回转、低 gas 的环境里持续吃价差。
3. 用库存约束和动态网格避免单边下跌时无限接飞刀。
4. 通过参数外置，使选币、网格、执行、风控都可在设置文件中调整。

## 5. 总体架构

建议拆成以下模块：

1. `UniverseSelector`
   - 负责选币、剔除不合格币。
2. `QuoteEngine`
   - 负责链上询价、可执行买卖价、滑点和价格冲击估算。
3. `ReferencePriceEngine`
   - 负责 mid price、EMA、短期波动率、spread 计算。
4. `GridEngine`
   - 负责生成每个币的买卖虚拟网格。
5. `InventoryManager`
   - 负责仓位成本、库存偏移、保留底仓、超配修正。
6. `ExecutionEngine`
   - 负责路由、下单、gas、nonce、重试和成交回写。
7. `RiskManager`
   - 负责暂停、止损、异常检测、日限额。
8. `StateStore`
   - 负责持久化 symbol 状态、成交记录、PnL、运行统计。
9. `Reporter`
   - 输出类似截图里的控制台仪表盘。

## 6. 每个标的的最小状态

每个币至少维护如下状态：

```json
{
  "symbol": "示例币",
  "status": "RUNNING",
  "base_token": "0x...",
  "quote_token": "0x...",
  "base_balance": 0.0,
  "quote_allocated": 0.0,
  "reserve_base_tokens": 3.0,
  "reference_price": 0.0,
  "ema_price": 0.0,
  "exec_buy_price": 0.0,
  "exec_sell_price": 0.0,
  "spread_bps": 0.0,
  "volatility_bps": 0.0,
  "buy_basis_price": 0.0,
  "sell_basis_price": 0.0,
  "buy_open_count": 0,
  "buy_done_count": 0,
  "sell_open_count": 0,
  "sell_done_count": 0,
  "avg_cost_price": 0.0,
  "realized_pnl": 0.0,
  "unrealized_pnl": 0.0,
  "daily_trade_count": 0,
  "last_trade_ts": 0,
  "last_error": ""
}
```

说明：

1. `buy_basis_price` 对应截图里的“多基准”。
2. `sell_basis_price` 对应截图里的“空基准”，这里是卖出腿基准，不是合约空头。
3. `buy_open_count / sell_open_count` 表示当前处于待触发状态的虚拟网格层数。
4. `buy_done_count / sell_done_count` 表示已经成交的买卖腿次数。

## 7. 选币规则

### 7.1 必选条件

1. 币已进入 Binance Alpha 可交易范围。
2. 上线时间大于 `min_listing_age_hours`。
3. 链上流动性大于 `min_pool_liquidity_usd`。
4. 最近成交额大于 `min_24h_volume_usd`。
5. 代币可正常买卖，未出现 honeypot、极端税费、路由异常。

### 7.2 剔除条件

1. 最近 `n` 分钟跌幅超过 `max_drop_pct_n_min`。
2. LP 明显撤池。
3. 买卖税费高于 `max_token_tax_bps`。
4. 询价持续失败。
5. gas 相对单笔目标收益过高。

### 7.3 轮动规则

1. 每 `rotation_interval_sec` 重算候选池。
2. 已持仓币优先保留，避免频繁轮动造成尾仓。
3. 达到 `max_symbols` 后，优先淘汰：
   - 流动性恶化的币。
   - 交易密度过低的币。
   - 连续失败或连续亏损的币。

## 8. 价格与信号定义

### 8.1 可执行价格

链上场景不应直接使用展示价，应该使用 `可执行价格`：

1. `exec_buy_price`
   - 用固定探测资金 `probe_quote_usd` 买入目标代币时的实际成交均价。
2. `exec_sell_price`
   - 卖出固定探测数量目标代币时的实际成交均价。

### 8.2 中间价格

```text
exec_mid_price = (exec_buy_price + exec_sell_price) / 2
```

### 8.3 点差

```text
spread_bps = (exec_buy_price - exec_sell_price) / exec_mid_price * 10000
```

### 8.4 EMA 参考价

```text
ema_t = ema_{t-1} + alpha * (exec_mid_price - ema_{t-1})
```

其中：

1. `alpha` 可由配置直接指定。
2. 也可用 `ema_period_sec` 反算 alpha。

### 8.5 最终参考价

推荐：

```text
reference_price = w_mid * exec_mid_price + w_ema * ema_price
```

默认建议：

1. 震荡高频场景：`w_mid` 略高。
2. 波动太大时：`w_ema` 略高。

## 9. 网格生成规则

### 9.1 核心思想

网格不是固定死格，而是 `围绕 reference_price 动态生成`。

### 9.2 基础步长

```text
base_step_bps = max(
  min_step_bps,
  spread_bps * spread_multiplier,
  volatility_bps * volatility_multiplier
)
```

再做裁剪：

```text
step_bps = clamp(base_step_bps, min_step_bps, max_step_bps)
```

### 9.3 买卖层生成

对每个买入层 `i`：

```text
buy_trigger_i = reference_price * (1 - buy_offset_i_bps / 10000)
```

对每个卖出层 `i`：

```text
sell_trigger_i = reference_price * (1 + sell_offset_i_bps / 10000)
```

其中：

1. `buy_offset_i_bps` 可直接在配置文件里逐层指定。
2. `sell_offset_i_bps` 可直接在配置文件里逐层指定。
3. 也可由 `step_bps * level_multiplier_i` 自动生成。

### 9.4 每层下单量

买入层建议用 `quote notional` 定义：

```text
buy_size_quote_i = base_buy_quote * buy_size_multiplier_i
```

卖出层建议用 `base qty` 或 `持仓比例` 定义：

```text
sell_size_base_i = max(
  min_sell_base,
  available_base * sell_size_ratio_i
)
```

### 9.5 虚拟挂单

每层网格只记录本地目标，不直接上链挂限价单：

1. 如果 `exec_buy_price <= buy_trigger_i`，则触发买入评估。
2. 如果 `exec_sell_price >= sell_trigger_i`，则触发卖出评估。
3. 触发前仍需再次做净收益和风险检查。

## 10. 库存管理规则

### 10.1 目标库存

对每个币定义目标库存比重：

```text
inventory_ratio = base_value / (base_value + quote_value)
```

目标值：

```text
target_base_ratio
```

### 10.2 库存偏移

当库存过重时，自动调整网格：

```text
inventory_gap = inventory_ratio - target_base_ratio
inventory_shift_bps = clamp(
  inventory_gap * inventory_skew_factor_bps,
  -max_inventory_shift_bps,
  max_inventory_shift_bps
)
```

作用方式：

1. `inventory_gap > 0`
   - 说明代币仓位偏重。
   - 应该 `放宽买入`，`收紧卖出`。
2. `inventory_gap < 0`
   - 说明代币仓位偏轻。
   - 应该 `放宽卖出`，`收紧买入`。

### 10.3 保留底仓

如果 `base_balance <= reserve_base_tokens`：

1. 不再执行普通卖出。
2. 仅允许在紧急平仓模式下卖出底仓。

这对应截图里的“保留3代币”。

### 10.4 成本价更新

买入成交后更新加权成本：

```text
avg_cost_price =
  (old_base_qty * old_avg_cost + fill_base_qty * fill_price) /
  new_base_qty
```

卖出成交后：

1. 已实现收益累加。
2. 库存减少。
3. 底仓以上部分按加权成本核算已实现 PnL。

## 11. 交易触发规则

### 11.1 买入触发

满足以下条件才允许买入：

1. `exec_buy_price <= buy_trigger_i`
2. `quote_allocated_remaining >= buy_size_quote_i`
3. `base_exposure_usd < max_base_exposure_usd`
4. `net_edge_buy_bps >= min_net_edge_bps`
5. 代币未处于冷却或暂停状态
6. 当前无同币种 in-flight 交易

净边际建议按下面估算：

```text
gross_edge_buy_bps =
  (reference_price - exec_buy_price) / exec_buy_price * 10000

net_edge_buy_bps =
  gross_edge_buy_bps
  - estimated_slippage_bps
  - estimated_fee_bps
  - estimated_gas_bps
```

### 11.2 卖出触发

满足以下条件才允许卖出：

1. `exec_sell_price >= sell_trigger_i`
2. `sellable_base >= sell_size_base_i`
3. `base_balance - sell_size_base_i >= reserve_base_tokens`
4. `net_edge_sell_bps >= min_net_edge_bps`
5. 当前无同币种 in-flight 交易

净边际建议按下面估算：

```text
gross_edge_sell_bps =
  (exec_sell_price - reference_price) / reference_price * 10000

net_edge_sell_bps =
  gross_edge_sell_bps
  - estimated_slippage_bps
  - estimated_fee_bps
  - estimated_gas_bps
```

## 12. 成交后的重定价规则

### 12.1 买入成交后

1. 更新余额和成本价。
2. 对应买层标记为已成交。
3. 增加一条卖出配对层，目标可设为：

```text
paired_sell_price = fill_price * (1 + paired_take_profit_bps / 10000)
```

4. 如果采用全局动态网格模式，也可以只刷新整体参考价和所有卖层，不单独绑定配对单。

### 12.2 卖出成交后

1. 更新余额和已实现收益。
2. 对应卖层标记为已成交。
3. 视模式决定是否补回买层：
   - 配对模式：生成回补买层。
   - 全局模式：按最新 `reference_price` 重新生成全局买卖层。

## 13. 风控规则

### 13.1 单币限制

1. `max_quote_per_symbol`
2. `max_base_exposure_usd`
3. `max_trades_per_symbol_per_hour`
4. `max_failed_tx_per_symbol`
5. `max_open_levels_per_symbol`

### 13.2 全局限制

1. `max_daily_realized_loss_usd`
2. `max_daily_gas_usd`
3. `max_consecutive_failed_tx`
4. `max_inflight_txs`

### 13.3 暂停条件

满足任一条件则暂停该币新开仓：

1. `stale_quote_sec` 超时。
2. `pool_liquidity_usd` 低于阈值。
3. 最近 `n` 分钟跌幅超过阈值。
4. 路由返回价格冲击超过 `max_price_impact_bps`。
5. 连续失败交易达到阈值。

### 13.4 紧急退出

满足任一条件时允许平掉底仓：

1. 代币被判定无法继续正常交易。
2. 全局风险开关触发。
3. 持仓跌破 `hard_stop_from_cost_bps`。

## 14. 执行规则

### 14.1 询价

每次执行前必须重新询价：

1. 路由路径。
2. 预估收到数量。
3. price impact。
4. 预计 gas。

### 14.2 发单

每次 swap 必须带：

1. `deadline_sec`
2. `min_out` 或 `max_in`
3. `slippage_bps`
4. `gas_price_limit`

### 14.3 并发约束

1. 同一币同一时刻最多一笔 in-flight 交易。
2. 推荐全局最多 `max_inflight_txs` 笔。
3. 同一 wallet 需要 nonce 管理。

### 14.4 重试

只允许对以下错误做有限重试：

1. RPC 超时。
2. nonce 冲突。
3. 轻微 gas 不足。

以下情况不得盲目重试：

1. price impact 超限。
2. token transfer failed。
3. honeypot/税费异常。

## 15. 统计与展示

建议控制台展示以下字段，尽量贴近截图：

1. 币名
2. 状态
3. 最新价
4. 多单(未/完)
5. 空单(未/完)
6. 多基准
7. 空基准
8. 余额
9. EMA
10. 今日已实现收益
11. 保留代币盈利
12. 今日交易次数
13. 最近 10 条成交或参数调整日志

## 16. 推荐的最小实现顺序

建议分 4 个阶段做：

### 阶段 1

1. 单币种。
2. 固定买卖层。
3. 固定下单量。
4. 固定 gas/slippage。
5. 仅实现虚拟网格触发。

### 阶段 2

1. 加入 EMA。
2. 加入动态步长。
3. 加入库存偏移。
4. 加入保留底仓。

### 阶段 3

1. 多币种调度。
2. 轮动选币。
3. 持久化状态。
4. 控制台仪表盘。

### 阶段 4

1. honeypot/税费风控。
2. LP 异常检测。
3. 多 RPC 容灾。
4. 交易绩效分析。

## 17. 直接可编码的主循环伪代码

```text
loop every refresh_interval_ms:
  universe = select_symbols(config)

  for symbol in universe:
    if symbol is paused:
      continue

    quote = quote_engine.get_executable_quote(symbol)
    if quote is stale or invalid:
      risk.pause(symbol, "stale quote")
      continue

    ref = reference_engine.update(symbol, quote)
    grid = grid_engine.build(symbol, ref, quote, inventory_state)

    if risk.reject_symbol(symbol, quote, ref, inventory_state):
      continue

    buy_signal = grid_engine.find_buy_trigger(symbol, quote, grid)
    if buy_signal and risk.allow_buy(symbol, buy_signal):
      execution.buy(symbol, buy_signal)
      continue

    sell_signal = grid_engine.find_sell_trigger(symbol, quote, grid)
    if sell_signal and risk.allow_sell(symbol, sell_signal):
      execution.sell(symbol, sell_signal)
      continue

  reporter.render()
```

## 18. 配置原则

1. 所有阈值必须来自设置文件，不应硬编码。
2. 每个币支持单独覆盖默认参数。
3. 网格层数、每层偏移、每层下单量、库存偏移参数都应支持配置。
4. 选币、执行、风控参数与策略参数分开。
5. 敏感信息只从环境变量读取，不放进配置文件。

## 19. 建议的配置文件

配套示例配置见：

`config/strategy.example.toml`

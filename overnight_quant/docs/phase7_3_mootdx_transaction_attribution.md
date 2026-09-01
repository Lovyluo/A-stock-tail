# mootdx 分钟逐笔归因合同 v2

## 范围

本合同只修正分钟标签研究证据的逐笔归因，不改变策略、评分、风险门禁或交易安全边界。
归因结果最多进入 `PM_REVIEW_REQUIRED`，始终保持 `data_ready=false`，不会自动写入来源
合格日累计，也不会生成 candidates、tickets 或 orders。

## 时间合同

mootdx 当前逐笔响应中的时间标签按来源原样保留为 `source_time_text=HH:MM`，并保留
`source_position` 作为同一分钟内的确定性顺序。规范化 `event_time` 对齐到该分钟的
`:00`，同时明确记录 `timestamp_precision=minute`，不得将来源没有提供的秒数伪造成
秒级精度。

分钟精度记录只有在以下条件全部满足时才可参与归因：

- 采集分页已完整跨过 14:49 和 14:50 区间；
- 14:49、14:50 两个分钟桶均存在；
- 记录时间全部对齐整分钟，精度不混用；
- 比较区间严格为 `HH:MM:00-HH:MM:59`。

未知精度、混合精度、非整分钟区间或覆盖不完整均返回 `INCONCLUSIVE`。

## 成交量合同

| 对象 | 原始单位 | 规范化单位 | 换算 |
|---|---|---|---|
| mootdx 分钟 K 线 | `share` | `share` | 1 |
| mootdx 逐笔 | `lot` | `share` | `1 lot = 100 shares` |

逐笔记录同时保留 `volume`、`raw_volume`、`normalized_volume`、原始和规范化单位、
`volume_conversion_factor=100` 以及
`volume_conversion_basis=A_share_round_lot_100_shares`。原始值不被覆盖。

只有 open、high、low、close 和规范化后的成交量全部匹配，并且 14:49、14:50 两个
区间中恰好一个匹配，才生成 provisional 结论。两个区间同时匹配或均不匹配都保持
`INCONCLUSIVE`；分钟 K 线没有成交笔数字段，因此逐笔成交笔数仅作审计。

## 最终态与哈希

分钟行必须连续三次观察保持相同 OHLCV 哈希才可标记 `is_final=true`。后续任一次变化
都会重新开始稳定计数，不能提前认定最终态。

归因算法版本为 `mootdx_minute_transaction_attribution_v2`，成交量规范化版本为
`a_share_lot_to_share_v1`。算法版本、单位合同、分钟证据哈希和逐笔证据哈希共同进入
组合证据哈希；任何单位或算法变化都会改变 `combined_evidence_hash`。

## 不可变重放

历史原始证据只读，重放结果写入独立 ignored cache 文件：

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_mootdx_probe_reanalysis.py `
  --input overnight_quant/data/cache/minute_label_probe_mootdx_YYYY-MM-DD.json `
  --output overnight_quant/data/cache/minute_label_probe_mootdx_YYYY-MM-DD.reanalysis-v2.json
```

重放结果必须再次通过 `run_probe_evidence_verify.py --source mootdx` 的三层哈希验证。
即使得到 provisional，也只作为 PM 候选证据，不自动启用正式来源。

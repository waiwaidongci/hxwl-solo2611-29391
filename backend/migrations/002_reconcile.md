# 002 跨系统对账

新增跨系统对账运行类型与两类来源批次（候车清点、发车记录）。新建环境由应用启动自动建表；从 001 升级的既有库执行：

```sql
ALTER TABLE datasets ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'ledger';
ALTER TABLE runs ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'analysis';
ALTER TABLE runs ADD COLUMN aux_dataset_id BIGINT NULL,
  ADD CONSTRAINT fk_runs_aux_dataset FOREIGN KEY (aux_dataset_id) REFERENCES datasets(id);
```

- `datasets.kind`：`ledger` 合并台账、`checks` 候车清点、`departures` 发车记录。并发补录另成批次，仍按名称和内容指纹去重，不改写既有批次。
- `runs.kind`：`analysis` 台账关联分析、`reconcile` 跨系统对账。对账运行在 `dataset_id` 存发车记录批次、`aux_dataset_id` 存候车清点批次，门槛随 `params` 落库；计算中断后恢复沿用接单时的两份记录和门槛。

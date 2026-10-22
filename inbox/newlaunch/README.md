# 手动补充一手新盘资料

把 CSV 放在本文件夹（可多个文件），每天运行时会纳入「一手新盘」页面，覆盖种子快照。列名见 `template.csv`，不需要的列可以留空。

**两种写法可以混用：**
- **项目级**：每个楼盘一行，填 `priceMinWan`（万）、`psfMin`、`areaMin/areaMax`、`discountPct`（最高折扣 %）等。
- **价单单位级**：每个单位一行，填 `name` + `block,floor,unit,area,price`（price 可填元或万），系统会自动计算价单范围、中位呎价和折实价。

`areaText` 填片区（如 紅磡、油塘、啟德），用来配对同区二手；`unitsTotal/unitsSold` 用来判断销情；`keyDate` 填预计关键日期（YYYY-MM-DD）。
资料请以发展商价单、售楼说明书及成交纪录册为准。
